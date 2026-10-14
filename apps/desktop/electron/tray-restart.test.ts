/**
 * Tests for electron/tray-restart.ts (WP-TRAY-RESTART).
 *
 * Run with: node --test electron/tray-restart.test.ts
 * (picked up by npm run test:desktop:platforms, which globs electron/*.test.ts.)
 *
 * Four jobs, in the order the task books pin them:
 *  ① the gate's direction — busy ⇒ ask, proven-idle ⇒ go, **unavailable ⇒ ask**
 *     (that last one is the tooth: the forbidden degradation is "assume idle");
 *  ② "did the old tree really exit" is decided by COMMAND LINE, not by image name
 *     (裁定 92: the gateway is `pythonw.exe -m plobi_cli.main gateway run`, invisible
 *     to a `python.exe` name filter — and my own live check fell for exactly that
 *     trailing-space/`python.exe` blind spot on 2026-10-09);
 *  ③ dev vs packaged relaunch as a RELATION (which chain gets reused), never as a
 *     hardcoded command string copied into Electron;
 *  ④ our own ANCESTRY is not a leftover (WP-TRAY-RESTART-REPAIR / R-087): in dev
 *     the wait used to count `concurrently` — our own parent — so it could never
 *     pass, and the restart never once succeeded. Same-tree cousins (the vite
 *     branch) are still counted; that boundary is pinned on purpose.
 */
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))

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

// ── ②b 我们自己的祖先不算残留（WP-TRAY-RESTART-REPAIR / R-087） ─────────────
//
// The deadlock this repairs, measured on the live dev tree 2026-10-14
// (`Get-CimInstance Win32_Process` ppid probe): the app is a descendant of
// `cmd /c npm run dev` → `concurrently`, and the old wait counted those parents
// as leftovers — processes that only die BECAUSE the app exits. The wait could
// never pass, the handoff script was never written, and no dev restart ever
// succeeded once. Per the task book the entries we must not wait for are our
// own ancestry, in whatever shape they arrive (concurrently / vite): the walk
// from selfPid up the ppid chain decides, never the shape.

/** Mirror of main.ts ownAncestorPids: every pid from `fromPid` up to the root. */
function walkAncestorPids(entries: Array<{ pid: number; ppid?: number }>, fromPid: number): number[] {
  const byPid = new Map(entries.map((entry) => [entry.pid, entry]))
  const chain: number[] = []
  const seen = new Set<number>([fromPid])
  let cursor = fromPid

  for (;;) {
    const parent = byPid.get(cursor)?.ppid

    if (!parent || seen.has(parent)) {
      return chain
    }

    seen.add(parent)
    chain.push(parent)
    cursor = parent
  }
}

const SELF_DEV = {
  pid: 4244,
  name: 'electron.exe',
  commandLine: 'D:\\Code\\node_modules\\electron\\dist\\electron.exe .',
  ppid: 4101
}

const ANC_CONCURRENTLY = {
  pid: 4101,
  name: 'node.exe',
  commandLine: 'node D:\\Code\\node_modules\\.bin\\..\\concurrently\\dist\\bin\\index.js -k "npm:dev:renderer" "npm:dev:electron"',
  ppid: 4102
}

const ANC_VITE = {
  pid: 4102,
  name: 'node.exe',
  commandLine: 'node D:\\Code\\node_modules\\.bin\\..\\vite\\bin\\vite.js --host 127.0.0.1 --port 5174',
  ppid: 4103
}

const ANC_OUTER_CMD = { pid: 4103, name: 'cmd.exe', commandLine: 'C:\\Windows\\system32\\cmd.exe /d /s /c npm run dev', ppid: 0 }
const FOREIGN_GATEWAY = { pid: 4242, name: 'pythonw.exe', commandLine: 'D:\\Runtimes\\Python\\pythonw.exe -m plobi_cli.main gateway run', ppid: 999 }
const NEW_TREE_SERVE = { pid: 4243, name: 'python.exe', commandLine: 'D:\\Code\\.venv\\Scripts\\python.exe -m plobi_cli.main serve --host 127.0.0.1 --port 60073', ppid: 999 }

test('our own ancestry is not counted as leftover — the dev deadlock is gone', () => {
  const table = [SELF_DEV, ANC_CONCURRENTLY, ANC_VITE, ANC_OUTER_CMD, FOREIGN_GATEWAY]
  const ancestors = walkAncestorPids(table, SELF_DEV.pid)

  assert.deepEqual(ancestors, [4101, 4102, 4103], 'ppid 链必须从 selfPid 一路走到根')

  const lingering = findLingeringProcesses(table, { selfPid: SELF_DEV.pid, ancestorPids: ancestors })

  assert.deepEqual(lingering.map((entry) => entry.pid), [FOREIGN_GATEWAY.pid], '只剩外来者；祖先一个都不计')
})

test('the SAME table still deadlocks without ancestorPids (the R-087 regression net)', () => {
  // 修前形状：只排 selfPid，concurrently 那两条（我们的父母）全被计成残留，
  // 等待永远超时。这个断言钉住「ancestorPids 确实是解毒药」，不是摆设。
  const table = [SELF_DEV, ANC_CONCURRENTLY, ANC_VITE, ANC_OUTER_CMD, FOREIGN_GATEWAY]

  const before = findLingeringProcesses(table, { selfPid: SELF_DEV.pid })

  assert.deepEqual(before.map((entry) => entry.pid), [ANC_CONCURRENTLY.pid, ANC_VITE.pid, FOREIGN_GATEWAY.pid])
})

test('ancestorPids and pendingPids coexist: ancestry and the new tree both excluded', () => {
  const table = [SELF_DEV, ANC_CONCURRENTLY, ANC_VITE, NEW_TREE_SERVE, FOREIGN_GATEWAY]
  const ancestors = walkAncestorPids(table, SELF_DEV.pid)

  const lingering = findLingeringProcesses(table, {
    selfPid: SELF_DEV.pid,
    ancestorPids: ancestors,
    pendingPids: [NEW_TREE_SERVE.pid]
  })

  assert.deepEqual(lingering.map((entry) => entry.pid), [FOREIGN_GATEWAY.pid], 'pendingPids 既有行为不变，外来者也仍计')
})

test('waitForTreeGone passes once only our own ancestry remains', async () => {
  const table = [[SELF_DEV, ANC_CONCURRENTLY, ANC_VITE, FOREIGN_GATEWAY], [SELF_DEV, ANC_CONCURRENTLY, ANC_VITE], []]
  let index = 0

  const verdict = await waitForTreeGone(() => table[index++], {
    ancestorPids: walkAncestorPids(table[0], SELF_DEV.pid),
    selfPid: SELF_DEV.pid,
    timeoutMs: 1000,
    intervalMs: 1,
    sleep: async () => {}
  })

  assert.equal(verdict.ok, true, '只剩祖先时等待必须放行：它们的死不是这个函数该等的事')
})

test('waitForTreeGone still fails on a FOREIGN tree process with ancestors excluded', async () => {
  const table = [SELF_DEV, ANC_CONCURRENTLY, FOREIGN_GATEWAY]

  const verdict = await waitForTreeGone(() => table, {
    ancestorPids: walkAncestorPids(table, SELF_DEV.pid),
    selfPid: SELF_DEV.pid,
    timeoutMs: 5,
    intervalMs: 1,
    sleep: async () => {}
  })

  assert.equal(verdict.ok, false)
  assert.equal(verdict.stage, 'not-gone')
  assert.deepEqual(verdict.lingering.map((entry) => entry.pid), [FOREIGN_GATEWAY.pid], '外来者仍计：排除只针对我们自己的父链')
})

test('a same-tree COUSIN (the real vite branch) is still counted — the exclusion is the parent chain only', () => {
  // Live ppid probe 2026-10-14: the vite branch hangs off concurrently as a
  // COUSIN of the app, not on its parent chain:
  //   app 22020 ← … ← concurrently 20380 → npm run dev:renderer → cmd 9348 → vite 22936
  // The fix is deliberately narrow (ancestorPids only), so the cousin stays
  // counted. Pinned so that widening the exclusion later is a conscious change.
  const REAL_TREE = [
    { pid: 22020, name: 'electron.exe', commandLine: 'D:\\Code\\node_modules\\electron\\dist\\electron.exe .', ppid: 24372 },
    { pid: 24372, name: 'node.exe', commandLine: 'node D:\\Code\\node_modules\\.bin\\..\\electron\\cli.js "."', ppid: 14872 },
    { pid: 14872, name: 'cmd.exe', commandLine: 'C:\\Windows\\system32\\cmd.exe /d /s /c "electron ^^".^^""', ppid: 2936 },
    { pid: 2936, name: 'node.exe', commandLine: 'node cross-env.js XCURSOR_SIZE=24 PLOBI_DESKTOP_DEV_SERVER=http://127.0.0.1:5174 electron .', ppid: 31308 },
    { pid: 31308, name: 'cmd.exe', commandLine: 'C:\\Windows\\system32\\cmd.exe /d /s /c wait-on http://127.0.0.1:5174 && node scripts/bundle-electron-main.mjs --dev && cross-env XCURSOR_SIZE=24 PLOBI_DESKTOP_DEV_SERVER=http://127.0.0.1:5174 electron .', ppid: 17520 },
    { pid: 17520, name: 'node.exe', commandLine: 'node D:\\Runtimes\\Languages\\Node\\node_modules\\npm\\bin\\npm-cli.js run dev:electron', ppid: 32836 },
    { pid: 32836, name: 'cmd.exe', commandLine: 'cmd.exe /s /c "npm run dev:electron"', ppid: 20380 },
    { pid: 20380, name: 'node.exe', commandLine: 'node D:\\Code\\node_modules\\.bin\\..\\concurrently\\dist\\bin\\index.js -k "npm:dev:renderer" "npm:dev:electron"', ppid: 27956 },
    { pid: 27956, name: 'cmd.exe', commandLine: 'C:\\Windows\\system32\\cmd.exe /d /s /c concurrently -k "npm:dev:renderer" "npm:dev:electron"', ppid: 10020 },
    { pid: 10020, name: 'node.exe', commandLine: 'node D:\\Runtimes\\Languages\\Node\\node_modules\\npm\\bin\\npm-cli.js run dev', ppid: 9340 },
    { pid: 33124, name: 'cmd.exe', commandLine: 'cmd.exe /s /c "npm run dev:renderer"', ppid: 20380 },
    { pid: 12864, name: 'node.exe', commandLine: 'node D:\\Runtimes\\Languages\\Node\\node_modules\\npm\\bin\\npm-cli.js run dev:renderer', ppid: 33124 },
    { pid: 9348, name: 'cmd.exe', commandLine: 'C:\\Windows\\system32\\cmd.exe /d /s /c node scripts/assert-root-install.mjs && vite --host 127.0.0.1 --port 5174', ppid: 12864 },
    { pid: 22936, name: 'node.exe', commandLine: 'node D:\\Code\\node_modules\\.bin\\..\\vite\\bin\\vite.js --host 127.0.0.1 --port 5174', ppid: 9348 },
    FOREIGN_GATEWAY
  ]

  const lingering = findLingeringProcesses(REAL_TREE, {
    selfPid: 22020,
    ancestorPids: walkAncestorPids(REAL_TREE, 22020)
  })

  assert.deepEqual(lingering.map((entry) => entry.pid), [9348, 22936, FOREIGN_GATEWAY.pid])
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

// ── ⑤ 文案：用户态就两三句（干什么 + 可能打断什么 + 确认吗），论证归日志 ────
test('both locales keep the confirmation to two or three user-facing sentences', () => {
  for (const locale of ['zh-CN', 'en-US']) {
    const labels = restartLabels(locale)
    const body = labels.confirmBody

    assert.ok(body.trim().length > 0)
    // 干什么
    assert.match(body, /关掉|tears down/)
    // 可能打断什么
    assert.match(body, /打断|interrupted/)
    // 确认吗
    assert.match(body, /吗[？?]|Restart now\?/)
    // 「没有可靠忙闲信号」的论证是审计轨迹，归 rememberLog，不归对话框（R-086）
    assert.ok(!/信号|signal/.test(body), '确认框不许再写自审墙')
    // 源码路径引用一律不许出现
    assert.ok(!/tray-restart\.ts|electron[\\/]/.test(body), '用户态确认框不许带源码路径')
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

test('the handoff writes its endings into a fixed outcome log, not the ignored stdio', () => {
  // spawn('cmd.exe', ['/c', scriptPath], { stdio: 'ignore' }) used to drop the
  // giveup line into the void; every ending now lands in $PLOBI_HOME/logs/.
  const log = 'D:\\plobi\\logs\\tray-restart-handoff.log'

  const script = buildWindowsRestartHandoff({
    waitPid: 4244,
    workingDirectory: 'D:\\x\\apps\\desktop',
    command: 'cmd /c npm run dev',
    outcomeLogPath: log
  })

  const giveup = script.split('\n').find((line) => line.includes('not relaunching'))

  assert.ok(giveup, 'giveup 行还在')
  assert.ok(giveup.startsWith(`>>"${log}" echo`), 'giveup 结局必须重定向进固定名文件')
  assert.ok(
    script.split('\n').some((line) => line.startsWith(`>>"${log}" echo`) && line.includes('relaunching:')),
    'relaunch 结局也要落盘'
  )
})

test('without an outcome log the endings stay on stdout (back-compat)', () => {
  const script = buildWindowsRestartHandoff({ waitPid: 7, workingDirectory: 'C:\\x', command: 'npm run dev' })

  assert.ok(
    script
      .split('\n')
      .some((line) => line.trim() === `echo [tray-restart] old pid 7 still alive after ${RESTART_HANDOFF_MAX_WAIT_SECONDS}s; not relaunching`)
  )
  assert.ok(!/>>"/.test(script), '没给日志路径就不许出现重定向')
})

test('a handoff that would wait on pid 0 is refused', () => {
  assert.throws(
    () => buildWindowsRestartHandoff({ waitPid: 0, workingDirectory: 'x', command: 'npm run dev' }),
    /pid/,
  )
})

/**
 * 裁定 115 的两件无条件修法，钉在源码顺序上（这台没法在测试里真开模态框）：
 * ① 弹框**之前**必须先上一行台账 —— 否则"点了没反应"与"正在等你回答"在用户面上不可分（裁定 94）；
 * ② 那条 await 必须有有限出口 —— `restartGuard.end()` 只在 finally 放，
 *    一条永不返回的 await 会把重启功能永久锁死（现核：两条 18:48 起的后端到 19:52 还活着，卡在弹框前）。
 */
test('the tray restart reports "awaiting your answer" before it asks, and can never hang the guard', () => {
  const source = fs.readFileSync(path.join(HERE, 'main.ts'), 'utf8')

  const reportAt = source.indexOf("labels.awaitingConfirm")
  const dialogAt = source.indexOf('dialog.showMessageBox(host, options)')
  const raceAt = source.indexOf('Promise.race([')
  const guardEndAt = source.indexOf('restartGuard.end()')

  assert.ok(reportAt > 0, '缺了 awaiting-confirm 那一行台账')
  assert.ok(dialogAt > 0, '弹框调用没找到（改名了？）')
  assert.ok(reportAt < dialogAt, '台账必须先于弹框：否则弹窗不出现时用户什么也看不到')
  assert.ok(raceAt > 0 && raceAt < dialogAt + 400, '那条 await 必须被 Promise.race 套住，才有有限出口')
  assert.ok(source.includes('RESTART_CONFIRM_TIMEOUT_MS'), '超时常量不见了 = 闸门又回到可以永久锁死')
  assert.ok(guardEndAt > 0 && source.includes('} finally {'), '闸门释放仍必须在 finally 里')
  assert.ok(source.includes('labels.dialogStalled'), '到点那条读数必须有自己的一句话，不能静默当取消')
})

test('both label sets carry the two new readings (zh and en, no silent fallback)', () => {
  const zh = restartLabels('zh-CN')
  const en = restartLabels('en-US')

  for (const [label, set] of [['zh', zh], ['en', en]] as const) {
    assert.ok(set.awaitingConfirm.length > 0, `${label}: awaitingConfirm 缺`)
    assert.ok(set.dialogStalled.length > 0, `${label}: dialogStalled 缺`)
    assert.match(set.dialogStalled, /10|10 分/, `${label}: 超时读数要写清等了多久`)
  }
})

/**
 * WP-TRAY-RESTART-REPAIR 的主进程接线，钉在源码形状上（这台没法在测试里真跑 WMI）：
 * ① R-087 —— WMI 查询要拿 ParentProcessId，等待要收到祖先集，否则 dev 态死锁复发；
 * ② R-086 —— 确认原因的审计轨迹归 rememberLog，dialog detail 只剩用户态文案；
 * ③ handoff 结局落固定名文件，不再留在 stdio: 'ignore' 的虚空里。
 */
test('main.ts wires the ancestry exclusion, the R-086 split and the handoff outcome log', () => {
  const source = fs.readFileSync(path.join(HERE, 'main.ts'), 'utf8')

  assert.ok(source.includes('$_.ParentProcessId'), 'listPlobiTreeProcesses 没拿 ppid，祖先链无从走起')
  assert.ok(/waitForTreeGone\(listPlobiTreeProcesses, \{[\s\S]*?ancestorPids/.test(source), 'waitForTreeGone 没收到 ancestorPids')
  assert.ok(source.includes('ownAncestorPids('), '缺了从 process.pid 沿父链走到底的查询')

  assert.ok(source.includes('[tray-restart] confirm reasons:'), '确认原因必须落 rememberLog 留痕')
  assert.ok(source.includes('detail: labels.confirmBody'), 'detail 不再拼 gate.reasons')
  assert.ok(!/gate\.reasons\.map/.test(source), 'gate.reasons 不许再爬回对话框')

  assert.ok(/outcomeLogPath: handoffLogPath/.test(source), 'handoff 必须拿到结局日志路径')
  assert.ok(source.includes('tray-restart-handoff.log'), '结局日志要有固定名')
})
