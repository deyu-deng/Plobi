/**
 * Tests for electron/deferred-sidecars.ts — R-051's honest status ledger.
 *
 * Run with: npm run test:desktop:platforms
 *
 * Pure state with an injected clock: no Electron, no tray, no ports, no probes.
 * The last test is a cross-language drift guard — the desktop wording must stay
 * byte-identical to `deferred_row()` in scripts/plobi/doctor.py, because those
 * two are meant to be one 口径 with two renderings.
 */

import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import {
  createBackendRespawnGuard,
  createSidecarRespawnPlan,
  createDeferredSubsystemLedger,
  DEFERRED_SUBSYSTEMS,
  DEFERRED_SUFFIX,
  PROFILE_BACKEND_ID_PREFIX,
  RED_SUFFIX,
  RESPAWN_MAX_DELAY_MS,
  RESPAWN_PERMANENT_HOLD_MS,
  deferredDetail,
  definitionFor,
  isMissingProfileFailure,
  missingProfileDetail,
  profileBackendRowId,
  spawnFaultOf
} from './deferred-sidecars'

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', '..')
const DOCTOR_PATH = path.join(REPO_ROOT, 'scripts', 'plobi', 'doctor.py')
/** The renderer's copy of the state union — `src/global.d.ts`. */
const GLOBAL_DTS_PATH = path.join(REPO_ROOT, 'apps', 'desktop', 'src', 'global.d.ts')
const LEDGER_PATH = path.join(REPO_ROOT, 'apps', 'desktop', 'electron', 'deferred-sidecars.ts')

/** Observation fixtures — the shape the main process passes in from a real probe. */
function observation(id: string, state: 'deferred' | 'probing' | 'ready', observed: string) {
  const definition = definitionFor(id)

  assert.ok(definition, `test fixture: ${id} must be a known deferred subsystem`)

  return { ...definition, observed, state, timestamp: 1 }
}

test('the ledger seeds a row for every deferred subsystem before any probe runs', () => {
  // Silence is the failure mode this module exists to remove: a row must exist
  // ("probe queued") even when nothing has been checked yet.
  const ledger = createDeferredSubsystemLedger({ now: () => 7 })
  const rows = ledger.rows()

  assert.deepEqual(rows.map(row => row.id).sort(), ['aigw', 'chatlog'])
  assert.ok(rows.every(row => row.state === 'probing'))
  assert.ok(rows.every(row => row.observed.length > 0))
  assert.ok(rows.every(row => row.timestamp === 7))
})

test('an observation replaces the row instead of accumulating a second one', () => {
  const ledger = createDeferredSubsystemLedger({ now: () => 42 })

  ledger.record('aigw', { observed: 'connection refused', state: 'deferred' })
  ledger.record('aigw', { observed: 'healthy on :8000', state: 'ready' })

  const rows = ledger.rows()

  assert.equal(rows.length, 2, 'one row per subsystem, never one per event')
  assert.equal(rows.find(row => row.id === 'aigw')?.state, 'ready')
  assert.equal(rows.find(row => row.id === 'aigw')?.timestamp, 42)
})

test('a failed probe downgrades the row but never hides what was observed', () => {
  // doctor.py rule: "a deferred row that hides what it found is indistinguishable
  // from a skipped check".
  const row = deferredDetail(observation('aigw', 'deferred', 'http://127.0.0.1:8000 unreachable (no listener)'))

  assert.match(row, /http:\/\/127\.0\.0\.1:8000 unreachable \(no listener\)/)
  assert.match(row, /不是坏了/)
  assert.match(row, /怎么回到绿：/)
})

test('a ready row reports the observation plainly, with no deferred wording', () => {
  const row = deferredDetail(observation('chatlog', 'ready', 'answered on :5030'))

  assert.equal(row, 'answered on :5030')
  assert.ok(!row.includes(DEFERRED_SUFFIX), 'a live subsystem must never be labelled deferred')
})

test('a deferred row never reads as success', () => {
  // The false-green is the exact thing R-048 removed from doctor.py.
  for (const id of Object.keys(DEFERRED_SUBSYSTEMS)) {
    const row = deferredDetail(observation(id, 'deferred', 'nothing answered'))

    assert.ok(row.includes(DEFERRED_SUFFIX))
    assert.ok(!/^ok/i.test(row))
  }
})

test('an unknown subsystem cannot acquire a row', () => {
  // The ledger covers the deferral tier only. Anything else is a normal check with
  // normal red/green semantics and must not get a free "not a fault" pass.
  const ledger = createDeferredSubsystemLedger()

  assert.equal(ledger.record('dingtalk', { observed: 'no webhook secret', state: 'deferred' }), null)
  assert.deepEqual(
    ledger
      .rows()
      .map(row => row.id)
      .sort(),
    ['aigw', 'chatlog']
  )
})

test('rows() order is stable so the two renderings cannot disagree', () => {
  const ledger = createDeferredSubsystemLedger()

  ledger.record('chatlog', { observed: 'x', state: 'deferred' })
  ledger.record('aigw', { observed: 'y', state: 'deferred' })

  const first = ledger.rows()
  const second = ledger.rows()

  assert.deepEqual(
    first.map(row => row.id),
    second.map(row => row.id)
  )
  assert.deepEqual(
    first.map(row => row.id),
    ['aigw', 'chatlog']
  )
})

test('rows() hand out copies, so a renderer cannot mutate the ledger', () => {
  const ledger = createDeferredSubsystemLedger()
  const rows = ledger.rows()

  rows[0].state = 'ready'

  assert.equal(ledger.rows()[0].state, 'probing')
})

test('every deferred row states how to turn it on', () => {
  for (const [id, definition] of Object.entries(DEFERRED_SUBSYSTEMS)) {
    assert.ok(definition.enableHint.length > 10, `${id} needs an actionable enable hint`)
    assert.ok(definition.port > 0 && definition.port < 65536, `${id} needs a real port`)
    assert.ok(definition.name.en && definition.name.zh, `${id} needs both tray languages`)
  }
})

test('enable hints name no Windows-only artifact', () => {
  // The Windows box was decommissioned on 2026-09-27; macOS is the only true
  // source, so advice like "run scripts/chatlog_server.ps1" is unactionable.
  for (const [id, definition] of Object.entries(DEFERRED_SUBSYSTEMS)) {
    assert.ok(!/\.ps1/i.test(definition.enableHint), `${id} hint must not require PowerShell`)
    assert.ok(!/[A-Z]:\\/.test(definition.enableHint), `${id} hint must not name a drive letter`)
  }
})

/**
 * Read doctor.py the way these parity rows actually mean it. Every git blob is
 * LF, while a Windows checkout puts CRLF in the working tree — and `/…\)\n/`
 * then finds nothing, so both rows below fail on that box for a reason that has
 * nothing to do with the wording they exist to guard. Measured here on
 * 2026-10-09: same bytes, HEAD blob matches, worktree file does not (986 CRLF).
 * A permanently red gate on one platform is a gate nobody reads.
 */
function readDoctorSource(): string {
  return fs.readFileSync(DOCTOR_PATH, 'utf8').replace(/\r\n/g, '\n')
}

test('desktop deferred wording matches scripts/plobi/doctor.py exactly', () => {
  const doctorSource = readDoctorSource()
  const suffix = /\nDEFERRED_SUFFIX = "([^"]*)"\n/.exec(doctorSource)

  assert.ok(suffix, 'doctor.py must keep the deferred suffix in one DEFERRED_SUFFIX constant')
  assert.equal(DEFERRED_SUFFIX, suffix[1], '两侧的下架口吻漂开了 —— 一条口径两种渲染，不许各写一份')

  assert.match(
    doctorSource,
    /detail = f"\{observed\} — \{DEFERRED_SUFFIX\}\{hint\}"/,
    'doctor.py must still render the deferred detail as: observed — suffix + hint'
  )

  // Substitute the same values on both sides and compare the rendered strings.
  const observed = 'http://127.0.0.1:8000 unreachable (no listener)'
  const hint = definitionFor('aigw')?.enableHint ?? ''
  const fromPython = `${observed} — ${suffix[1]}${hint}`
  const fromDesktop = deferredDetail(observation('aigw', 'deferred', observed))

  assert.equal(fromDesktop, fromPython)
})

test('the doctor.py hint table still covers every desktop deferred subsystem', () => {
  // One shared definition of "which capabilities are deferred" — if doctor.py
  // renames or drops an id, the desktop tray must not keep asserting it is
  // postponed.
  const doctorSource = readDoctorSource()
  const hints = /DEFERRED_ENABLE_HINTS: dict\[str, str\] = \{([\s\S]*?)\n\}/.exec(doctorSource)

  assert.ok(hints, 'doctor.py must still define DEFERRED_ENABLE_HINTS')

  const doctorIds = new Set([...hints[1].matchAll(/^\s{4}"([^"]+)":/gm)].map(match => match[1]))

  for (const id of Object.keys(DEFERRED_SUBSYSTEMS)) {
    assert.ok(doctorIds.has(id), `${id} is deferred on the desktop but not in doctor.py`)
  }
})

// ── R-052: `red` — the state that was missing ───────────────────────────────
// doctor.py calls "reachable (or attempted) but not healthy" RED and reserves
// `deferred` for "nothing here could even be started". Before this, the desktop
// ledger had no way to say the first thing, so every real sidecar fault was
// reported as a postponement.

test('red is a state the ledger can actually hold, and it is worded as a fault', () => {
  const ledger = createDeferredSubsystemLedger({ now: () => 3 })

  const row = ledger.record('chatlog', {
    observed: 'spawned /bin/chatlog but :5030 never answered',
    state: 'red'
  })

  assert.ok(row, 'record() must accept red — refusing it is what hid the fault')
  assert.equal(row.state, 'red')
  assert.match(row.detail, /spawned \/bin\/chatlog but :5030 never answered/)
  assert.match(row.detail, /是坏了，不是后置/)
  // The "how it comes back" line stays in the detail for every non-ready row.
  assert.match(row.detail, /怎么回到绿：/)
  assert.ok(!row.detail.includes(DEFERRED_SUFFIX), 'a real fault must never read as a deferral')
})

test('a red row with no recorded observation still says it is a fault', () => {
  const red = deferredDetail({ ...observation('aigw', 'ready', ''), observed: '', state: 'red' })

  assert.match(red, /是坏了，不是后置/)
  assert.match(red, /怎么回到绿：/)
  assert.ok(!red.includes(DEFERRED_SUFFIX))
})

test('red and deferred coexist in the union and are rendered by different branches', () => {
  // Anti-regression, and the reason this test reads the source: the one bug worth
  // protecting against is someone folding `red` back into `deferred` to quiet the
  // UI. Both copies of the union must carry both states.
  const statesOf = (source: string) => {
    const line = source.split('\n').find(candidate => candidate.includes("'probing'") && candidate.includes('|'))

    assert.ok(line, 'the subsystem state union must stay a single declared line')

    return [...line.matchAll(/'([^']+)'/g)].map(match => match[1])
  }

  for (const file of [LEDGER_PATH, GLOBAL_DTS_PATH]) {
    const states = statesOf(fs.readFileSync(file, 'utf8'))

    assert.ok(states.includes('red'), `${file} must declare 'red'`)
    assert.ok(states.includes('deferred'), `${file} must still declare 'deferred'`)
    assert.deepEqual(states, ['deferred', 'probing', 'ready', 'red'])
  }

  assert.notEqual(RED_SUFFIX, DEFERRED_SUFFIX)

  const observed = 'http://127.0.0.1:5030 answered 500'
  const asDeferred = deferredDetail(observation('chatlog', 'deferred', observed))
  const asRed = deferredDetail({ ...observation('chatlog', 'deferred', observed), state: 'red' })

  assert.ok(asDeferred.includes(DEFERRED_SUFFIX) && !asDeferred.includes(RED_SUFFIX))
  assert.ok(asRed.includes(RED_SUFFIX) && !asRed.includes(DEFERRED_SUFFIX))
  assert.notEqual(asDeferred, asRed, 'two states that render identically are one state lying')
})

test('a red row that starts answering turns ready on the same row, without a second one', () => {
  const ledger = createDeferredSubsystemLedger()

  ledger.record('aigw', { observed: 'answered 500 on /healthz', state: 'red' })
  ledger.record('aigw', { observed: 'healthy on :8000', state: 'ready' })

  const rows = ledger.rows()
  const aigw = rows.find(row => row.id === 'aigw')

  assert.equal(rows.length, 2, 'a state change replaces the row, it never adds an event row')
  assert.equal(aigw?.state, 'ready')
  assert.equal(aigw?.detail, 'healthy on :8000')
})

// ── Profile backends: the framelet crash loop ───────────────────────────────
// A per-profile backend that exits 1 is a 真故障 in exactly the sense ac05264
// added `red` for. It reports through this ledger (no fifth state, no second
// status store) and the respawn guard below is the backoff the spawn path lacked.

const MISSING_PROFILE_LINE = "Profile 'framelet' does not exist. Create it with: plobi profile create framelet"
/**
 * A failure that says nothing about whether the profile *can* exist — the shape
 * the generic backoff tests are about. The missing-profile line is now a
 * deterministic class of its own (see `isMissingProfileFailure`), so it must not
 * double as the transient fixture.
 */
const TRANSIENT_BACKEND_EXIT = 'Plobi backend for profile "framelet" exited (1) before it became ready'

test('a profile backend gets a lazily-created red row that quotes the CLI itself', () => {
  const ledger = createDeferredSubsystemLedger({ now: () => 5 })

  assert.deepEqual(
    ledger
      .rows()
      .map(row => row.id)
      .sort(),
    ['aigw', 'chatlog'],
    'no profile row exists before anything was attempted'
  )

  const row = ledger.record(profileBackendRowId('framelet'), { observed: MISSING_PROFILE_LINE, state: 'red' })

  assert.ok(row, 'record() must accept a backend:<profile> id')
  assert.equal(row.state, 'red')
  assert.match(row.detail, /Profile 'framelet' does not exist/)
  assert.ok(row.detail.includes(RED_SUFFIX))
  assert.ok(!row.detail.includes(DEFERRED_SUFFIX), 'a crash loop must never read as a postponement')

  // Replacing the observation, not stacking an event row per attempt — otherwise
  // 1173 cycles would become 1173 rows.
  ledger.record(profileBackendRowId('framelet'), { observed: 'still missing', state: 'red' })

  assert.equal(ledger.rows().filter(candidate => candidate.id.startsWith(PROFILE_BACKEND_ID_PREFIX)).length, 1)
})

test('an unknown id still cannot acquire a row now that profile ids are dynamic', () => {
  const ledger = createDeferredSubsystemLedger()

  assert.equal(ledger.record('backend:', { observed: 'x', state: 'red' }), null)
  assert.equal(ledger.record('dingtalk', { observed: 'x', state: 'deferred' }), null)
  assert.deepEqual(
    ledger
      .rows()
      .map(row => row.id)
      .sort(),
    ['aigw', 'chatlog']
  )
})

test('forget() drops a profile row we no longer intend to attempt', () => {
  const ledger = createDeferredSubsystemLedger()

  ledger.record(profileBackendRowId('framelet'), { observed: MISSING_PROFILE_LINE, state: 'red' })

  assert.equal(ledger.forget(profileBackendRowId('framelet')), true)
  assert.equal(ledger.forget(profileBackendRowId('framelet')), false, 'a second drop must report no change')
  assert.equal(ledger.rows().find(row => row.id.startsWith(PROFILE_BACKEND_ID_PREFIX)), undefined)
})

test('the respawn gate blocks a retry immediately after a failure', () => {
  let clock = 1_000
  const guard = createBackendRespawnGuard({ now: () => clock })

  guard.arm('framelet', TRANSIENT_BACKEND_EXIT)

  assert.ok(guard.blockReason('framelet'), 'a failing backend must not be respawned on the spot')
  assert.match(guard.blockReason('framelet') ?? '', /exited \(1\)/, 'the reason carries the cause')

  // One millisecond short of the first delay is still blocked.
  clock += 2_000 - 1

  assert.ok(guard.blockReason('framelet'))

  clock += 1

  assert.equal(guard.blockReason('framelet'), null, 'the next attempt must be allowed once it is due')
})

test('the respawn interval grows with consecutive failures and is capped', () => {
  let clock = 0
  const guard = createBackendRespawnGuard({ now: () => clock })

  const gaps = [1, 2, 3, 4].map(attempt => {
    const { delayMs } = guard.arm('framelet', TRANSIENT_BACKEND_EXIT)

    clock += delayMs

    return delayMs
  })

  assert.deepEqual(gaps, gaps.slice().sort((a, b) => a - b), 'the delay must never shrink as it fails again')
  assert.ok(gaps[1] > gaps[0], `attempt 2 (${gaps[1]}ms) must wait longer than attempt 1 (${gaps[0]}ms)`)
  assert.equal(gaps[0], 2_000, 'the first retry is a couple of seconds out, not immediate')

  // Keep arming: the growth is bounded so the worst-case block is predictable.
  for (let index = 0; index < 40; index += 1) {
    assert.ok(guard.arm('framelet', TRANSIENT_BACKEND_EXIT).delayMs <= 120_000)
  }
})

test('auto-retry is bounded, but a later request still gets through', () => {
  let clock = 0
  const guard = createBackendRespawnGuard({ maxAutoRetries: 3, now: () => clock })

  const schedules = [1, 2, 3, 4].map(() => {
    const schedule = guard.arm('framelet', TRANSIENT_BACKEND_EXIT)

    clock += schedule.delayMs

    return schedule
  })

  assert.deepEqual(schedules.map(schedule => schedule.autoRetry), [true, true, true, false])

  // The gate expired with the last schedule, so the next user action re-attempts
  // — a capped retry, never a permanent latch that outlives the fix.
  assert.equal(guard.blockReason('framelet'), null)
})

// ── 确定性失败：profile 根本不存在（WP-DESKTOP-POOL-GHOST-PROFILE）─────────────

test('a roster row with no profile gets no retry timer and a long hold', () => {
  let clock = 0
  const guard = createBackendRespawnGuard({ now: () => clock })
  // The real line from the 2026-10-13 incident (a course row, no profile dir).
  const ghostLine = "Profile '电工电子学' does not exist. Create it with: plobi profile create 电工电子学"

  const schedule = guard.arm('电工电子学', ghostLine)

  assert.equal(schedule.permanent, true, 'the CLI line must classify as deterministic')
  assert.equal(schedule.autoRetry, false, 'a deterministic failure must never be on a timer — that was the loop')
  assert.equal(schedule.delayMs, RESPAWN_PERMANENT_HOLD_MS, 'the hold is the long window, not the 2 s backoff step')

  // 15 s renderer reconnects: blocked, and the cause is said ONCE per window.
  clock += 15_000

  const first = guard.blockReason('电工电子学') ?? ''

  assert.match(first, /Profile '电工电子学' does not exist/, 'the first reason carries the cause')

  clock += 15_000

  const second = guard.blockReason('电工电子学') ?? ''

  assert.ok(second, 'still blocked')
  assert.ok(!/does not exist/.test(second), 'a repeat within the window must not re-print the cause 2,214 times')

  // The window expires, so creating the profile heals the row by itself.
  clock = RESPAWN_PERMANENT_HOLD_MS

  assert.equal(guard.blockReason('电工电子学'), null, 'never a permanent latch')
})

test('the CLI accepts no name at all: argparse invalid choice is the same class', () => {
  const argparseLine = "plobi: error: argument command: invalid choice: '大学物理（甲）ⅱ' (choose from chat, model)"

  assert.equal(isMissingProfileFailure(argparseLine), true, 'a non-profile-id name lands in the subcommand slot')
  assert.equal(spawnFaultOf(argparseLine), 'unusable-name')
  assert.equal(isMissingProfileFailure(MISSING_PROFILE_LINE), true)
  assert.equal(spawnFaultOf(MISSING_PROFILE_LINE), 'missing-profile')
  assert.equal(isMissingProfileFailure(TRANSIENT_BACKEND_EXIT), false, 'ordinary exits stay retryable')
  assert.equal(spawnFaultOf(TRANSIENT_BACKEND_EXIT), 'transient')
})

test('a reserved profile name is the third permanent shape (the `Plobi` roster row)', () => {
  // 产品线 13:2x 实测：`plobi -p plobi --help` 退 0，话术只在真起后端时出现 —— 所以它
  // 既不是 `does not exist` 也不是 `invalid choice`，而 `Plobi` 这条是 `l2_project`，
  // **不是数据条目**：建目录这条路根本不存在，名字本身被永久禁用。
  const reservedLine =
    "Error: Profile name 'plobi' is reserved — it collides with either the Plobi installation " +
    'itself or a common system binary. Pick a different name.'

  assert.equal(isMissingProfileFailure(reservedLine), true, 'retrying a banned name proves nothing')
  assert.equal(spawnFaultOf(reservedLine), 'reserved-name')

  let clock = 0
  const guard = createBackendRespawnGuard({ now: () => clock })

  assert.equal(guard.arm('plobi', reservedLine).autoRetry, false, 'no respawn timer for a banned name')

  const detail = missingProfileDetail('plobi', reservedLine)

  assert.match(detail, /reserved profile name/, 'it must name the real cause, not "the profile does not exist"')
  assert.match(detail, /can never be opened/)
  assert.ok(!/plobi profile create plobi/.test(detail), 'telling the user to create a reserved name is a lie')
  assert.match(detail, /Rename the profile/, 'the only two ways out: rename, or stop treating it as an agent')
})

test('each permanent shape gets its own wording, and the CLI line stays attached', () => {
  const missing = missingProfileDetail('电工电子学', MISSING_PROFILE_LINE)
  const unusable = missingProfileDetail(
    '大学物理（甲）ⅱ',
    "plobi: error: argument command: invalid choice: '大学物理（甲）ⅱ'"
  )

  assert.match(missing, /plobi profile create 电工电子学/, 'this shape really is fixable by creating it')
  assert.match(unusable, /not a valid profile id/, 'this shape is not')
  assert.ok(!/plobi profile create 大学物理/.test(unusable), 'don hand the user a command that cannot work')
  assert.match(unusable, /never even reaches the profile switch/)
})

test('the permanent row names both ways out instead of dumping CLI text', () => {
  const detail = missingProfileDetail('电工电子学', MISSING_PROFILE_LINE)

  assert.match(detail, /no profile to run as/)
  assert.match(detail, /plobi profile create 电工电子学/, 'the one actionable command it has')
  assert.match(detail, /data, not an agent/, 'it must say a 课程 row is data — the honest cause')
  assert.match(detail, new RegExp(MISSING_PROFILE_LINE.slice(0, 20)), 'the CLI line stays attached as evidence')
})

test('clear() only reports a real change, so a healthy backend stays silent', () => {
  const guard = createBackendRespawnGuard({ now: () => 0 })

  assert.equal(guard.clear('framelet'), false)

  guard.arm('framelet', MISSING_PROFILE_LINE)

  assert.equal(guard.clear('framelet'), true)
  assert.equal(guard.clear('framelet'), false)
})

// ── 裁定 (2026-09-29) 「改成不可执行的说明，由软件自行拉起」 ─────────────────

test('the quota hub enable hint is a statement, not a command the user is handed', () => {
  const hint = definitionFor('aigw')?.enableHint ?? ''

  assert.match(hint, /Plobi 需要时自己起这一条/)
  assert.ok(!/cd |uv run|python -m|\$ /.test(hint), 'a runnable-looking line is what made this read as TODO')
  assert.ok(!/aigw/.test(hint), '裁定 45: the internal word must not reach user-visible copy')
})

test('the desktop quota-hub hint is byte-equal to the one in doctor.py', () => {
  // chatlog keeps doctor-only phrasing ("absent on this machine"), so the drift
  // guard covers the one hint that is a claim about app behaviour and must match
  // wherever the user reads it.
  const doctorSource = fs.readFileSync(DOCTOR_PATH, 'utf8')
  const block = /"aigw": \(([\s\S]*?)\n\s*\),/.exec(doctorSource)

  assert.ok(block, 'doctor.py must still carry an aigw entry in DEFERRED_ENABLE_HINTS')

  const doctorHint = [...block[1].matchAll(/"((?:[^"\\]|\\.)*)"/g)]
    .map(match => match[1])
    .join('')
    .trim()

  assert.equal(definitionFor('aigw')?.enableHint, doctorHint)
})

test('后置服务意外退出走有界重拉：间隔翻倍、到顶就停、成功或本意关闭就清零', () => {
  const plan = createSidecarRespawnPlan({ baseDelayMs: 1000, maxAutoRetries: 3, maxDelayMs: 90_000 })
  const first = plan.fail()
  const second = plan.fail()
  const third = plan.fail()

  assert.deepEqual([first.autoRetry, second.autoRetry, third.autoRetry], [true, true, true])
  assert.deepEqual([first.delayMs, second.delayMs, third.delayMs], [1000, 2000, 4000], '指数退避：不立刻重跑同一个死法')
  assert.equal(plan.fail().autoRetry, false, '到顶之后不许再排定时器——这文件上面记着 3,900 与 1,173 两次风暴')
  assert.equal(plan.attempts(), 4)

  plan.reset()
  assert.equal(plan.attempts(), 0)
  assert.equal(plan.fail().delayMs, 1000, '重头来过：一次成功或一次本意关闭就把计数清零')
})

test('重拉的默认上限取自既有那把尺，不另造一套数', () => {
  const plan = createSidecarRespawnPlan()
  let last = plan.fail()

  for (let i = 0; i < 12; i += 1) {
    last = plan.fail()
  }
  assert.equal(last.autoRetry, false)
  assert.ok(last.delayMs <= RESPAWN_MAX_DELAY_MS, String(last.delayMs))
})
