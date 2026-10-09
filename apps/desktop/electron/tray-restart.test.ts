/**
 * Tests for electron/tray-restart.ts (WP-TRAY-RESTART).
 *
 * Run with: node --test electron/tray-restart.test.ts
 * (picked up by npm run test:desktop:platforms, which globs electron/*.test.ts.)
 *
 * Three jobs, in the order the task book pins them:
 *  ① the gate's direction — busy ⇒ ask, proven-idle ⇒ go, **unavailable ⇒ ask**
 *     (that last one is the tooth: the forbidden degradation is "assume idle");
 *  ② "did the old tree really exit" is decided by COMMAND LINE, not by image name
 *     (裁定 92: the gateway is `pythonw.exe -m plobi_cli.main gateway run`, invisible
 *     to a `python.exe` name filter — and my own live check fell for exactly that
 *     trailing-space/`python.exe` blind spot on 2026-10-09);
 *  ③ dev vs packaged relaunch as a RELATION (which chain gets reused), never as a
 *     hardcoded command string copied into Electron.
 */
import assert from 'node:assert/strict'
import test from 'node:test'

import {
  RESTART_HANDOFF_MAX_WAIT_SECONDS,
  buildWindowsRestartHandoff,
  createRestartGuard,
  findLingeringProcesses,
  isOwnPlobiProcess,
  planRelaunch,
  planRestartConfirmation,
  restartLabels,
  waitForTreeGone
} from './tray-restart'
import { TRAY_RESTART_ROW_ID, createDeferredSubsystemLedger } from './deferred-sidecars'

const GATEWAY = { pid: 4242, name: 'pythonw.exe', commandLine: 'D:\\Runtimes\\Python\\pythonw.exe -m plobi_cli.main gateway run' }
const SERVE = { pid: 4243, name: 'python.exe', commandLine: 'D:\\Code\\.venv\\Scripts\\python.exe -m plobi_cli.main serve --host 127.0.0.1 --port 60073' }
const ELECTRON = { pid: 4244, name: 'electron.exe', commandLine: 'D:\\Code\\node_modules\\electron\\dist\\electron.exe .' }
const UNRELATED = { pid: 7, name: 'python.exe', commandLine: 'C:\\Tools\\python.exe -m http.server 8000' }

// ── ① 闸门方向：不许从「查不到」推出「没在忙」 ────────────────────────────────
test('the gate asks when it cannot see anything (fail-CLOSED, the tooth)', () => {
  const plan = planRestartConfirmation({})

  assert.equal(plan.ask, true)
  assert.ok(plan.reasons.length > 0, '退化成甲时必须说清为什么还是问了')
})

test('the gate asks when a probe errored even if the rest looks idle', () => {
  const plan = planRestartConfirmation({
    gatewayState: 'running',
    activeAgents: 0,
    subagentsByAgent: { aura: [] },
    tickLockHeld: false,
    desktopLedgerEmpty: true,
    probeErrors: ['GET /api/status timeout']
  })

  assert.equal(plan.ask, true)
  assert.ok(plan.reasons.some((r) => r.includes('timeout')), '错误原因要能被用户看见')
})

test('the gate asks when the gateway state is unknown rather than absent-busy', () => {
  // derive_gateway_busy() fails open to False (gateway/status.py:887-890); this
  // must not inherit that direction.
  const plan = planRestartConfirmation({ activeAgents: 0, subagentsByAgent: { aura: [] }, tickLockHeld: false, desktopLedgerEmpty: true })

  assert.equal(plan.ask, true)
  assert.ok(plan.reasons.some((r) => r.includes('确证')))
})

test('the gate asks when work is positively observed', () => {
  const plan = planRestartConfirmation({
    gatewayState: 'running',
    activeAgents: 2,
    subagentsByAgent: { aura: ['working', 'idle'] },
    tickLockHeld: true,
    desktopLedgerEmpty: false
  })

  assert.equal(plan.ask, true)
  assert.ok(plan.reasons.some((r) => r.includes('2')))
  assert.ok(plan.reasons.some((r) => r.includes('子分身没回')))
})

test('the gate only skips the dialog on a fully observed idle conjunction', () => {
  const plan = planRestartConfirmation({
    gatewayState: 'running',
    activeAgents: 0,
    subagentsByAgent: { aura: [], orbit: ['idle'] },
    tickLockHeld: false,
    desktopLedgerEmpty: true
  })

  assert.equal(plan.ask, false, plan.reasons.join('；'))
})

test('no resident agent listed is NOT evidence of no delegation', () => {
  const plan = planRestartConfirmation({
    gatewayState: 'running',
    activeAgents: 0,
    subagentsByAgent: {},
    tickLockHeld: false,
    desktopLedgerEmpty: true
  })

  assert.equal(plan.ask, true)
})

// ── ② 退干净与否按命令行判 ──────────────────────────────────────────────────
test('a gateway running as pythonw.exe is found by its command line', () => {
  assert.equal(isOwnPlobiProcess(GATEWAY, 1), true)
  assert.equal(isOwnPlobiProcess(SERVE, 1), true)
  assert.equal(isOwnPlobiProcess(ELECTRON, 1), true)
})

test('an unrelated python.exe on the same port shape is NOT ours', () => {
  assert.equal(isOwnPlobiProcess(UNRELATED, 1), false)

  const lingering = findLingeringProcesses([UNRELATED], { selfPid: 1 })
  assert.deepEqual(lingering, [])
})

test('the restarting Electron itself is not counted as a leftover', () => {
  assert.equal(isOwnPlobiProcess({ ...ELECTRON, pid: 4244 }, 4244), false)
})

test('a freshly spawned tree is excluded so verification can actually pass', () => {
  const entries = [GATEWAY, SERVE, ELECTRON]
  const pending = [SERVE.pid, ELECTRON.pid]

  const lingering = findLingeringProcesses(entries, { selfPid: 999, pendingPids: pending })

  assert.deepEqual(lingering.map((entry) => entry.pid), [GATEWAY.pid])
})

test('waitForTreeGone succeeds once the command-line matches disappear', async () => {
  const table = [[GATEWAY, SERVE], [SERVE], []]
  let index = 0

  const verdict = await waitForTreeGone(() => table[index++], {
    selfPid: 999,
    timeoutMs: 1000,
    intervalMs: 1,
    sleep: async () => {}
  })

  assert.equal(verdict.ok, true)
})

test('waitForTreeGone reports its OWN failure mode: still there when the clock ran out', async () => {
  const verdict = await waitForTreeGone(() => [GATEWAY], {
    selfPid: 999,
    timeoutMs: 5,
    intervalMs: 1,
    sleep: async () => {}
  })

  assert.equal(verdict.ok, false)
  assert.equal(verdict.stage, 'not-gone', '「没退干净」必须和「起了但后端没起来」分得开')
  assert.deepEqual(verdict.lingering.map((entry) => entry.pid), [GATEWAY.pid])
})

// ── ③ 两条重起路是关系，不是抄来的字符串 ─────────────────────────────────────
test('dev mode reuses the existing dev launcher chain instead of relaunching Electron', () => {
  const plan = planRelaunch({
    isPackaged: false,
    isWindows: true,
    devCommand: 'cmd /c npm run dev',
    devCwd: 'D:\\Code\\apps\\desktop',
    devLauncherAvailable: true
  })

  assert.equal(plan.kind, 'dev')
  if (plan.kind === 'dev') {
    assert.equal(plan.cwd, 'D:\\Code\\apps\\desktop', '工作目录必须是 apps/desktop，否则 npm run dev 起不来')
    assert.match(plan.command, /npm run dev/)
    assert.ok(plan.note.includes('假重启'), '要能说出来：只重拉 Electron 就是假按钮')
  }
})

test('packaged mode relaunches its own executable', () => {
  const plan = planRelaunch({ isPackaged: true, isWindows: true, execPath: 'D:\\Plobi\\Plobi.exe' })

  assert.equal(plan.kind, 'packaged')
  if (plan.kind === 'packaged') {
    assert.equal(plan.execPath, 'D:\\Plobi\\Plobi.exe')
  }
})

test('when the dev chain cannot be reused the plan says UNSUPPORTED, it does not half-do it', () => {
  const plan = planRelaunch({ isPackaged: false, isWindows: false, devCommand: 'npm run dev', devCwd: '/tmp' })

  assert.equal(plan.kind, 'unsupported')
  assert.ok(plan.note.includes('手动'), '明说不支持并给手动路径（任务书必做 2）')
})

// ── ④ 幂等：连点两次不许起第二份后端 ────────────────────────────────────────
test('the guard refuses a second restart while one is in flight', () => {
  const guard = createRestartGuard()

  assert.equal(guard.begin(), true)
  assert.equal(guard.begin(), false, '第二次必须被拒，而不是排队')
  guard.end()
  assert.equal(guard.begin(), true)
})

// ── ⑤ 文案：甲口径要写在确认语里，不能只在日志里 ─────────────────────────────
test('both locales say the confirmation exists because no reliable busy signal exists', () => {
  for (const locale of ['zh-CN', 'en-US']) {
    const labels = restartLabels(locale)

    assert.ok(labels.restart.trim().length > 0)
    assert.match(labels.confirmBody, /信号|signal/)
    assert.match(labels.confirmBody, /确认|confirm/)
  }
})

// ── ⑥ 结果必须真能落到面上（今天刚为同一形状自纠过一次） ─────────────────────
test('the boot ledger accepts the restart row, so its outcome is actually visible', () => {
  const ledger = createDeferredSubsystemLedger()

  assert.ok(
    !ledger.rows().some((row) => row.id === TRAY_RESTART_ROW_ID),
    '这一格由第一次真实观察建行：没点过重启就不该挂一条"还在探"',
  )

  const row = ledger.record(TRAY_RESTART_ROW_ID, {
    observed: '没重启成功：旧进程树在超时内仍按命令行可见，新树没起（残留 2 个：4242/pythonw.exe、4244/electron.exe）',
    state: 'red',
  })

  assert.ok(row, 'record() 丢掉这个 id ⇒ reportDeferredSubsystem 无处渲染，"重启失败"就只活在 desktop.log 里')
  assert.equal(row.port, 0, '这一格没有端口，渲染侧要按非正端口省略')
  assert.match(row.detail, /RED — real fault/, '失败不许被读成 R-047 那种"故意推迟"')
  assert.ok(row.detail.length > 0)
})

test('an unregistered row id is still refused after registering this one', () => {
  const ledger = createDeferredSubsystemLedger()

  assert.equal(ledger.record('tray-restart-typo', { observed: 'x', state: 'red' }), null)
})

// ── ⑦ 交接脚本：等的是 PID，起的是同一条链，超时就不叠第二棵树 ────────────────
test('the handoff script waits on the PID, not an image name', () => {
  const script = buildWindowsRestartHandoff({
    waitPid: 4244,
    workingDirectory: 'D:\\Projects\\Plobi\\Plobi-Desktop\\Code\\apps\\desktop',
    command: 'cmd /c npm run dev',
  })

  assert.match(script, /tasklist \/NH \/FI "PID eq 4244"/, '按 PID 精确筛（desktop-uninstall.ts 立的同一形状）')
  assert.ok(!/IMAGENAME/i.test(script), '按镜像名筛就会看不见 pythonw 那一腿（裁定 92）')
})

test('the handoff reuses the caller\'s dev command and directory verbatim', () => {
  const command = 'cmd /c npm run dev'
  const dir = 'D:\\x\\apps\\desktop'
  const script = buildWindowsRestartHandoff({ waitPid: 1, workingDirectory: dir, command })

  assert.ok(script.includes(`cd /d "${dir}"`), '工作目录错了 npm 就起不来')
  assert.ok(script.split('\n').some((line) => line.trim() === command), '命令原样用，不在 Electron 里另抄一份 npm 命令行')
})

test('the handoff gives up instead of stacking a second tree on a live one', () => {
  const script = buildWindowsRestartHandoff({ waitPid: 7, workingDirectory: 'C:\\x', command: 'npm run dev' })

  assert.ok(script.includes(`LEFT=${RESTART_HANDOFF_MAX_WAIT_SECONDS}`), '等待必须有上界')
  assert.match(script, /:giveup/)
  assert.match(script, /not relaunching/, '超时后不许再起，免得撞端口与文件锁')
})

test('a handoff that would wait on pid 0 is refused', () => {
  assert.throws(
    () => buildWindowsRestartHandoff({ waitPid: 0, workingDirectory: 'x', command: 'npm run dev' }),
    /pid/,
  )
})
