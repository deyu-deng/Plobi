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
  createDeferredSubsystemLedger,
  DEFERRED_SUBSYSTEMS,
  DEFERRED_SUFFIX,
  RED_SUFFIX,
  deferredDetail,
  definitionFor
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
  assert.match(row, /deferred by R-047/)
  assert.match(row, /Enable: /)
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
  // The ledger covers the R-047 tier only. Anything else is a normal check with
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

test('desktop deferred wording matches scripts/plobi/doctor.py exactly', () => {
  const doctorSource = fs.readFileSync(DOCTOR_PATH, 'utf8')
  const block = /def deferred_row[\s\S]*?detail = \(([\s\S]*?)\)\n/.exec(doctorSource)

  assert.ok(block, 'doctor.py must still build the deferred detail inside deferred_row()')

  const doctorDetail = [...block[1].matchAll(/f?"((?:[^"\\]|\\.)*)"/g)].map(match => match[1]).join('')

  // Substitute the same values on both sides and compare the rendered strings.
  const observed = 'http://127.0.0.1:8000 unreachable (no listener)'
  const hint = definitionFor('aigw')?.enableHint ?? ''
  const fromPython = doctorDetail.replace('{observed}', observed).replace('{hint}', hint)
  const fromDesktop = deferredDetail(observation('aigw', 'deferred', observed))

  assert.equal(fromDesktop, fromPython)
})

test('the doctor.py hint table still covers every desktop deferred subsystem', () => {
  // One shared definition of "which capabilities are deferred" — if doctor.py
  // renames or drops an id, the desktop tray must not keep asserting it is
  // postponed.
  const doctorSource = fs.readFileSync(DOCTOR_PATH, 'utf8')
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
  assert.match(row.detail, /RED/)
  // The enable steps stay in the detail for every non-ready row.
  assert.match(row.detail, /Enable: /)
  assert.ok(!row.detail.includes(DEFERRED_SUFFIX), 'a real fault must never read as "postponed, not a fault"')
})

test('a red row with no recorded observation still says it is a fault', () => {
  const red = deferredDetail({ ...observation('aigw', 'ready', ''), observed: '', state: 'red' })

  assert.match(red, /RED/)
  assert.match(red, /Enable: /)
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
