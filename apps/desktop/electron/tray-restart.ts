/**
 * Tray "重启应用" — the pure decision pieces (WP-TRAY-RESTART).
 *
 * Why this file exists instead of a two-line `app.relaunch()`: on this machine
 * the desktop runs in DEV mode. The Startup artifact
 * (`...\Startup\plobi_desktop_autostart.vbs`) is
 * `PLOBI_DESKTOP_START_HIDDEN=1` + `cd apps/desktop` + `cmd /c npm run dev`, so
 * Electron is a *child* of `concurrently` and the renderer loads from the Vite
 * dev server. Relaunching only Electron would produce a window pointing at a
 * dead dev server and a backend that never restarted — a button that looks like
 * it worked while the code stays old. That is the exact complaint the owner
 * brought when asking for this feature.
 *
 * ── The gate: 丙 was the owner's pick, and it degrades to 甲 ────────────────
 * 丙 = "ask only when something is in flight". Building that requires proving a
 * NEGATIVE: "no work is running in any of the processes I cannot read". Measured
 * on 2026-10-09, every authoritative busy signal is process-local memory:
 *
 *   * `cron/scheduler.py:299` `_running_job_ids` (read by `:312`) — in-process;
 *     no route in `plobi/console/router.py` exposes cron at all, and the
 *     `.tick.lock` file is created/truncated *before* the flock is taken
 *     (`cron/scheduler.py:3593` then `:3595-3599`), so its *existence* proves
 *     nothing. Async jobs are dispatched onto a pool (`cron/scheduler.py:3686`)
 *     and the `finally` releases the lock when `tick()` returns
 *     (`:3798-3808`) — a running job with NO lock held is normal.
 *   * `plobi/delegation/tracker.py:71-75` says it outright: "In-memory and
 *     process-local by design. Nothing here survives a restart."
 *     `GET /api/agents/{agent_id}/subagents` (`plobi/console/router.py:968`)
 *     needs an agent id; there is no "all in-flight children" surface.
 *   * `tui_gateway/server.py:5954` `session["running"]` is per-session, in-process;
 *     `plobi_cli/web_server.py:3690-3695` states "there is no HTTP control channel
 *     into the running gateway".
 *   * The one cross-process read that looks tempting fails in the WRONG direction:
 *     `gateway/status.py:887-890` documents that `derive_gateway_busy` "degrades to
 *     False whenever liveness is unknown… never a spurious 'busy'". For a restart
 *     button that is precisely backwards — an unknown state must not authorize
 *     killing the user's work (R-053 / 裁定 97: a gate that passes when it cannot
 *     see is indistinguishable from a broken gate).
 *
 * So the honest outcome is 甲: **always confirm**. `planRestartConfirmation` keeps
 * the 丙 shape (an explicit conjunction of positive observations) but fail-closes:
 * anything missing, unparseable, or unverified yields "confirm". Upgrading this to
 * a real 丙 needs a cross-process busy surface, not a smarter guess here.
 */

export type ProcessEntry = { pid: number; name: string; commandLine: string }

export type RestartLabelSet = {
  restart: string
  confirmGo: string
  confirmCancel: string
  confirmTitle: string
  confirmBody: string
  cancelled: string
  /** 弹确认框**之前**就要有的那一行：把"点了没反应"与"正在等你回答"分开（裁定 115 / 94）。 */
  awaitingConfirm: string
  /** 确认框到点仍没人回答：释放闸门并说清"是我等的超时，不是没反应"。 */
  dialogStalled: string
  restarting: string
  doneDev: string
  donePackaged: string
  failedNotGone: string
  failedRelaunch: string
  unsupported: string
}

const RESTART_STRINGS: Record<'zh' | 'en', RestartLabelSet> = {
  zh: {
    restart: '重启应用',
    confirmGo: '重启',
    confirmCancel: '取消',
    confirmTitle: '重启 Plobi？',
    confirmBody:
      '这会关掉整个后端树（会话后端 / 额度网关 / cron / chatlog）再用新代码重起。' +
      '正在跑的会话、未回的子分身委派、cron 本轮都可能被打断。确定要重启吗？',
    cancelled: '未重启：确认框取消，什么都没动。',
    awaitingConfirm: '重启应用 · 正在等你在弹窗里确认',
    dialogStalled: '未重启：等确认框回答等到超时（10 分钟），闸门已释放 —— 再点一次即可。',
    restarting: '正在停整棵树并按命令行确认没了…',
    doneDev: '已重启（开发态：重起 npm run dev 整树，Vite 与后端都是新代码）。',
    donePackaged: '已重启（打包态：应用自己重拉，后端随之起来）。',
    failedNotGone: '没重启成功：旧进程树在超时内仍按命令行可见，新树没起（避免撞端口/文件占用）。',
    failedRelaunch: '部分完成：旧树已退，但新树没拉起来，需要手动重开。',
    unsupported: '这种运行形态我暂时不支持自动重启，请手动重开应用。'
  },
  en: {
    restart: 'Restart Plobi',
    confirmGo: 'Restart',
    confirmCancel: 'Cancel',
    confirmTitle: 'Restart Plobi?',
    confirmBody:
      'This tears down the whole backend tree (session backend / quota gateway / cron / chatlog) ' +
      'and brings it back on new code. An in-flight chat turn, an unanswered L2 delegation or a ' +
      'cron tick can all be interrupted. Restart now?',
    cancelled: 'Not restarted: confirmation cancelled, nothing was touched.',
    awaitingConfirm: 'Restart app · waiting for you in the dialog',
    dialogStalled: 'Not restarted: the confirmation dialog went unanswered for 10 minutes; the guard is released — just click again.',
    restarting: 'Stopping the tree and verifying by command line…',
    doneDev: 'Restarted (dev mode: the whole `npm run dev` tree came back, Vite and backend included).',
    donePackaged: 'Restarted (packaged: the app re-launched itself and the backend came with it).',
    failedNotGone: 'Restart aborted: the old tree was still visible by command line when the timeout hit; nothing new was launched.',
    failedRelaunch: 'Half done: the old tree is gone but the new one did not come up — open the app manually.',
    unsupported: 'This run mode cannot be restarted automatically yet — please reopen the app by hand.'
  }
}

export function trayLanguageOf(locale?: string): 'zh' | 'en' {
  return String(locale ?? '').toLowerCase().startsWith('en') ? 'en' : 'zh'
}

export function restartLabels(locale?: string): RestartLabelSet {
  return RESTART_STRINGS[trayLanguageOf(locale)]
}

/**
 * Command-line fingerprints of the tree this app owns. Matching is on the
 * **command line**, never on the image name — 裁定 92 exists because the gateway
 * is `pythonw.exe -m plobi_cli.main gateway run`, which a `Name='python.exe'`
 * filter does not see at all.
 */
const TREE_PATTERNS: RegExp[] = [
  /plobi_cli[.\s]main\s+(serve|gateway|dashboard|gui)\b/i,
  /plobi_cli\.main\s+(serve|gateway|dashboard|gui)\b/i,
  /(?:^|[\\/])electron\.exe\s+\./i,
  /(?:^|[\\/])Plobi\.exe(?:\s|$)/i,
  /\bconcurrently\b[^\n]*dev:electron/i,
  /\bvite\b[^\n]*(5174|--host)/i,
  /plobi_quota_mcp_server/i,
  /(?:^|[\\/])(chatlog|aigw)\.exe\b/i
]

export function isOwnPlobiProcess(entry: ProcessEntry, selfPid: number): boolean {
  if (!entry || typeof entry.pid !== 'number') {
    return false
  }
  // The restarting Electron is itself in the tree until it exits; the caller
  // treats it separately so "gone?" is a question about the *old* tree.
  if (entry.pid === selfPid) {
    return false
  }
  const commandLine = String(entry.commandLine || '')
  if (!commandLine) {
    return false
  }
  return TREE_PATTERNS.some((pattern) => pattern.test(commandLine))
}

/**
 * What is still alive from the old tree. Three kinds of pid are NOT leftovers:
 * `pendingPids` are the pids we launched ourselves (the new tree), and
 * `ownTreePids` are the dev session tree we belong to — everything from the
 * `npm run dev` session root down (裁定 WP-TRAY-RESTART-REPAIR 补丁: the vite
 * branch is a COUSIN of the app, not an ancestor, and the whole tree only dies
 * because we exit). Either set must not be counted, or the verification would
 * never pass. A plobi process outside our tree is a foreign tree and still
 * counts — that is what keeps the wait honest (no port / file-lock collisions).
 */
export function findLingeringProcesses(
  entries: ProcessEntry[],
  {
    selfPid,
    pendingPids = [] as number[],
    ownTreePids = [] as number[]
  } = {} as { selfPid?: number; pendingPids?: number[]; ownTreePids?: number[] }
): ProcessEntry[] {
  const pending = new Set(pendingPids)
  const ownTree = new Set(ownTreePids)

  return (entries || []).filter(
    (entry) =>
      isOwnPlobiProcess(entry, Number(selfPid ?? -1)) && !pending.has(entry.pid) && !ownTree.has(entry.pid)
  )
}

export type BusyObservation = {
  /** Only a POSITIVE, parsed reading can ever move toward "no need to ask". */
  gatewayState?: string
  activeAgents?: number
  subagentsByAgent?: Record<string, string[]>
  tickLockHeld?: boolean
  desktopLedgerEmpty?: boolean
  /** Any probe that errored / timed out / returned something unparseable. */
  probeErrors?: string[]
}

export type ConfirmationPlan = { ask: boolean; reasons: string[] }

/**
 * The gate. Fail-CLOSED by construction: `ask` is true unless every one of these
 * is positively observed — an explicit running gateway state, zero active agents,
 * an empty subagent list for every resident agent, no tick-lock contention, an
 * empty desktop-side ledger, and no probe error. Missing field, unknown string,
 * or a failed probe ⇒ ask. That ordering is the whole point of the function;
 * do not "simplify" it to `if (busy) ask`.
 */
export function planRestartConfirmation(observation: BusyObservation = {}): ConfirmationPlan {
  const reasons: string[] = []
  const obs = observation || {}

  const errors = Array.isArray(obs.probeErrors) ? obs.probeErrors.filter(Boolean) : []
  if (errors.length > 0) {
    reasons.push(`存活探针没全读出来：${errors.slice(0, 3).join('；')}`)
  }

  if (obs.gatewayState !== 'running' || typeof obs.activeAgents !== 'number') {
    reasons.push('读不到「网关在跑且活跃会话数为 0」的确证')
  } else if (obs.activeAgents > 0) {
    reasons.push(`网关里有 ${obs.activeAgents} 个会话正在跑`)
  }

  const subagents = obs.subagentsByAgent || {}
  const resident = Object.keys(subagents)
  if (resident.length === 0) {
    reasons.push('一个在册分身都没读到，无法证明没有未回的派工')
  }
  for (const agentId of resident) {
    const statuses = subagents[agentId]
    if (!Array.isArray(statuses)) {
      reasons.push(`分身 ${agentId} 的子任务状态读不成列表`)
      continue
    }
    const working = statuses.filter((status) => String(status).toLowerCase() !== 'idle')
    if (working.length > 0) {
      reasons.push(`分身 ${agentId} 还有 ${working.length} 个子分身没回`)
    }
  }

  if (obs.tickLockHeld !== false) {
    reasons.push('cron 的 .tick.lock 没能确认是没占用的')
  }
  if (obs.desktopLedgerEmpty !== true) {
    reasons.push('桌面自己启动过的活还没确认为空')
  }

  return { ask: reasons.length > 0, reasons }
}

export type RelaunchPlan =
  | { kind: 'dev'; command: string; cwd: string; note: string }
  | { kind: 'packaged'; execPath: string; note: string }
  | { kind: 'unsupported'; note: string }

/**
 * Which relaunch path is honest, as a relation — not a copied command string.
 *
 * Dev reuses the SAME launcher artifact the autostart writes (the caller passes
 * the existing `DEV_DESKTOP_COMMAND`, so this module never invents a second npm
 * line). Packaged asks the app to re-launch its own executable, which is correct
 * there because the packaged app spawns its own backend. Anything else (e.g. dev
 * mode on a platform where that Windows launcher artifact does not exist) says
 * so out loud instead of half-doing it.
 */
export function planRelaunch({
  isPackaged,
  isWindows,
  devCommand,
  devCwd,
  execPath,
  devLauncherAvailable
}: {
  isPackaged: boolean
  isWindows: boolean
  devCommand?: string
  devCwd?: string
  execPath?: string
  devLauncherAvailable?: boolean
}): RelaunchPlan {
  if (isPackaged) {
    return {
      kind: 'packaged',
      execPath: String(execPath || ''),
      note: '打包态：应用重拉自己的可执行文件，后端随它起来'
    }
  }

  if (!isWindows || !devLauncherAvailable || !devCommand || !devCwd) {
    return {
      kind: 'unsupported',
      note: '开发态但复用不到那条 dev 启动链（非 Windows / 拿不到 launcher 命令）—— 只能手动重开'
    }
  }

  return {
    kind: 'dev',
    command: devCommand,
    cwd: devCwd,
    note: '开发态：默认重起 npm run dev 那一整棵树（Vite + Electron + 后端），只重拉 Electron 是假重启。唯一例外（用户 10-15 拍甲）：端口被**我们自己那棵残留 vite**占着时复用它、只重起 Electron 那条腿；占用者不是我们的 ⇒ 不重起，只在日志里说清'
  }
}

/**
 * The detached handoff script (Windows). Needed because the *outer* dev chain —
 * `cmd /c npm run dev` → `concurrently` → Vite + Electron — is our own ancestry:
 * we cannot kill it from inside without dying first, and we cannot start the new
 * tree while the old Vite still owns port 5174. So a detached script waits for our
 * PID to disappear and only then brings the tree back.
 *
 * The wait is a `tasklist /FI "PID eq N"` filter, i.e. it keys on the PID and on
 * the command line shape of that exact process — never on an image name
 * (裁定 92). The loop is bounded so a hung exit cannot leave the machine with no
 * app and no message. With `outcomeLogPath`, the giveup/relaunch endings are
 * appended to that fixed file instead of a stdio the detached child discards —
 * otherwise "did the handoff even happen" is unanswerable after the fact.
 */
export const RESTART_HANDOFF_MAX_WAIT_SECONDS = 60

/** The dev renderer's port: the new Vite cannot bind it while a leftover one holds it. */
export const DEV_RENDERER_PORT = 5174

/**
 * The launcher that actually starts the handoff. A `.bat` spawned straight from a
 * console app attaches to *our* console: the user sees a window, and when our tree
 * is torn down the console CTRL events reach `npm run dev` — measured on this box
 * 10-15 09:13 as `relaunch command exited with code -1073741510` (0xC000013A
 * STATUS_CONTROL_C_EXIT) with no Electron ever coming back.
 *
 * `wscript` is a GUI-subsystem host (nothing flashes) and `Shell.Run` with
 * window-style 0 starts the batch **hidden and in its own console**, so the
 * relaunch survives our teardown. Same mechanism the dev autostart .vbs relies on.
 */
export function buildHiddenHandoffLauncher(batPath: string): string {
  const quoted = batPath.replace(/"/g, '""')

  return [
    "' Plobi desktop restart handoff launcher — hidden, own console, survives the old tree.",
    "' Written by apps/desktop/electron/main.ts (WP-TRAY-RESTART-REPAIR).",
    'Set shell = CreateObject("WScript.Shell")',
    `shell.Run """${quoted}""", 0, False`,
    'WScript.Quit 0',
    ''
  ].join('\r\n')
}

/**
 * The Electron-only leg, **derived from the same npm line the autostart writes** — this
 * module still does not invent a second launcher artifact (the rule in `planRelaunch`).
 * `cmd /c npm run dev` → `cmd /c npm run dev:electron`; anything whose dev line is not
 * literally `npm run dev` yields '' and the caller must then refuse to reuse.
 */
export function deriveElectronLegCommand(devCommand: string): string {
  const replaced = String(devCommand || '').replace(/\bnpm run dev(?![\w:-])/, 'npm run dev:electron')

  return replaced === String(devCommand || '') ? '' : replaced
}

export function buildWindowsRestartHandoff({
  waitPid,
  workingDirectory,
  command,
  outcomeLogPath,
  maxWaitSeconds = RESTART_HANDOFF_MAX_WAIT_SECONDS,
  rendererPort = DEV_RENDERER_PORT,
  reuseCommand,
  ownTreePids
}: {
  waitPid: number
  workingDirectory: string
  command: string
  outcomeLogPath?: string
  maxWaitSeconds?: number
  rendererPort?: number
  reuseCommand?: string
  ownTreePids?: number[]
}): string {
  const limit = Math.max(1, Math.floor(Number(maxWaitSeconds) || RESTART_HANDOFF_MAX_WAIT_SECONDS))
  const pid = Number(waitPid)

  if (!Number.isInteger(pid) || pid <= 0) {
    throw new Error(`refusing to build a handoff that waits on pid ${waitPid}`)
  }

  // The handoff is spawned detached with stdio ignored, so a bare `echo` lands
  // in the void. Redirect every ending into the fixed outcome log instead.
  const outcome = outcomeLogPath ? `>>"${outcomeLogPath}" ` : ''

  // Reuse is only on the table when BOTH the Electron leg and a positive list of
  // "pids that belong to the tree we are replacing" were handed to us. Without the
  // list there is nothing to identify the leftover with, so the held branch stays
  // the honest refusal it was.
  const ownPids = (ownTreePids || []).filter((p) => Number.isInteger(p) && p > 0)
  const reuse = reuseCommand && ownPids.length > 0 ? String(reuseCommand) : ''
  const holderList = ownPids.join(',')

  const portStage: string[] = [
    ':waitport',
    'rem The leftover dev Vite belongs to the tree we just replaced, and the new one cannot',
    'rem bind the renderer port while it still holds it. Wait for the port to go FREE instead',
    'rem of killing anything: the tree above us is the user to close, and a wrong kill takes',
    'rem other sessions with it. Every ending below is logged, so "I clicked restart and',
    'rem nothing came back" has an answer in the file instead of a guess.',
    'set /a LEFT2=%LEFT%',
    ':portloop',
    `powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort ${rendererPort} -State Listen -ErrorAction SilentlyContinue) { exit 10 } else { exit 0 }" 2>nul`,
    'rem errorlevel is a ">=" test, so the two gates below read as: under 10 = FREE, exactly',
    'rem 10 = HELD, 11 or above (9009 = no PowerShell) = the probe itself could not answer —',
    'rem and that third case falls through to "relaunch anyway". A probe that cannot speak',
    'rem must never be the reason the app does not come back. (Measured 10-15 12:5x: getting',
    'rem this backwards sent a HELD port down the blind branch, i.e. straight into relaunching',
    'rem into the collision we came here to avoid — and the source-text test passed anyway.)',
    'if not errorlevel 10 goto portfree',
    reuse ? 'if not errorlevel 11 goto portown' : 'if not errorlevel 11 goto portwait',
    `${outcome}echo [%TIME%] [tray-restart] cannot tell whether port ${rendererPort} is free; relaunching anyway`,
    'goto relaunch',
    ':portwait',
    'set /a LEFT2-=1',
    'if %LEFT2% LSS 1 goto portbusy',
    'timeout /t 1 /nobreak >nul',
    'goto portloop',
    ':portfree',
    `${outcome}echo [%TIME%] [tray-restart] renderer port ${rendererPort} is free; relaunching`,
    'goto relaunch',
    ':portbusy',
    `${outcome}echo [%TIME%] [tray-restart] renderer port ${rendererPort} still held after ${limit}s; not relaunching - something else owns it, close it and restart again`,
    'exit /b 3'
  ]

  if (reuse) {
    // Measured 10-15: the old vite does NOT reliably die with us, so "wait for the port"
    // alone turns the restart into a logged refusal. Owner picked 甲: reuse it. The
    // leftover counts as ours ONLY if its pid is in the tree snapshot we took before
    // quitting AND its command line still says vite — a stranger on 5174 is never reused.
    portStage.splice(
      portStage.indexOf(':portwait'),
      0,
      ':portown',
      `powershell -NoProfile -Command "$o=@(${holderList}); $q=@(Get-NetTCPConnection -LocalPort ${rendererPort} -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess); foreach($x in $q){ if($o -contains [int]$x){ $c=(Get-CimInstance Win32_Process -Filter ('ProcessId='+[int]$x) -ErrorAction SilentlyContinue).CommandLine; if($c -match 'vite'){ exit 20 } } }; exit 30" 2>nul`,
      'rem 20 = 占着端口的正是我们刚换掉那棵树里的 vite（pid 来自退出前拍下的会话树快照，',
      'rem 且命令行仍写着 vite）；30 或读不出 = 别人占着 ⇒ 不重起，只在日志里说清。',
      'if not errorlevel 20 goto portwait',
      'if not errorlevel 21 goto reuse',
      'goto portwait',
      ':reuse',
      `cd /d "${workingDirectory}"`,
      `${outcome}echo [%TIME%] [tray-restart] port ${rendererPort} is held by OUR OWN leftover vite - reusing it, relaunching only the Electron leg: ${reuse}`,
      reuse,
      `${outcome}echo [%TIME%] [tray-restart] reuse relaunch exited with code %ERRORLEVEL%`,
      'exit /b 0'
    )
  }

  return [
    '@echo off',
    'rem Plobi desktop restart handoff (WP-TRAY-RESTART) — waits for the old app to exit,',
    'rem then brings the SAME tree back. Keyed on the PID, never on an image name.',
    'rem',
    'rem A tray restart is USER-initiated: he asked for the app back, so the new tree must SHOW.',
    'rem This flag comes from the Startup artifact (the .vbs sets PLOBI_DESKTOP_START_HIDDEN=1, the',
    'rem packaged login item passes --hidden) and it is INHERITED down cmd -> wscript -> npm ->',
    'rem electron, so without clearing it a restart that works perfectly still ends with no window',
    'rem — which is indistinguishable, from the user\'s seat, from the failure this WP is about.',
    'rem (R-088 is the separate complaint that a hidden cold boot gives no signal at all.)',
    'set "PLOBI_DESKTOP_START_HIDDEN="',
    `set /a LEFT=${limit}`,
    ':waitforold',
    `tasklist /NH /FI "PID eq ${pid}" 2>nul | findstr /r /c:" ${pid} " >nul`,
    'if errorlevel 1 goto waitport',
    'set /a LEFT-=1',
    'if %LEFT% LSS 1 goto giveup',
    'timeout /t 1 /nobreak >nul',
    'goto waitforold',
    ...portStage,
    ':relaunch',
    `cd /d "${workingDirectory}"`,
    `${outcome}echo [%TIME%] [tray-restart] old pid ${pid} gone; relaunching: ${command}`,
    command,
    `${outcome}echo [%TIME%] [tray-restart] relaunch command exited with code %ERRORLEVEL%`,
    'exit /b 0',
    ':giveup',
    'rem The old process never exited in time. Do NOT start a second tree on top of',
    'rem it (port and file-lock collisions) — say so in the log the app reads back.',
    `${outcome}echo [%TIME%] [tray-restart] old pid ${pid} still alive after ${limit}s; not relaunching`,
    'exit /b 2'
  ].join('\r\n')
}

/**
 * Single-flight guard: two tray clicks must not produce two backends. The
 * release happens on every terminal outcome (cancelled, aborted, relaunched),
 * and `takeover` is refused rather than queued — the user can click again once
 * the first attempt settles.
 */
export function createRestartGuard() {
  let inFlight = false

  return {
    begin(): boolean {
      if (inFlight) {
        return false
      }
      inFlight = true
      return true
    },
    end(): void {
      inFlight = false
    },
    get busy(): boolean {
      return inFlight
    }
  }
}

export type GoneVerdict =
  | { ok: true; waitedMs: number }
  | { ok: false; stage: 'not-gone'; waitedMs: number; lingering: ProcessEntry[] }

/**
 * Poll "is the old tree gone" by command line, with a timeout. `list` and `sleep`
 * are injected so the loop is testable without a machine. `ownTreePids` is the
 * dev session tree we belong to — excluded exactly like `pendingPids`, because
 * it exits only when we do (裁定 WP-TRAY-RESTART-REPAIR 补丁). A plobi process
 * OUTSIDE that tree is foreign and still counts. On timeout the caller MUST NOT
 * relaunch (a new tree would collide on ports and file locks) and must report
 * this as its own failure mode, distinct from "old tree gone, relaunch failed".
 */
export async function waitForTreeGone(
  list: () => Promise<ProcessEntry[]> | ProcessEntry[],
  {
    selfPid,
    pendingPids = [] as number[],
    ownTreePids = [] as number[],
    timeoutMs = 15000,
    intervalMs = 500,
    sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))
  }: {
    selfPid?: number
    pendingPids?: number[]
    ownTreePids?: number[]
    timeoutMs?: number
    intervalMs?: number
    sleep?: (ms: number) => Promise<unknown>
  } = {}
): Promise<GoneVerdict> {
  const deadline = Date.now() + Math.max(0, Number(timeoutMs) || 0)
  let waited = 0
  let lingering: ProcessEntry[] = []

  for (;;) {
    const entries = await list()
    lingering = findLingeringProcesses(entries, { selfPid, pendingPids, ownTreePids })
    if (lingering.length === 0) {
      return { ok: true, waitedMs: waited }
    }
    if (Date.now() >= deadline) {
      return { ok: false, stage: 'not-gone', waitedMs: waited, lingering }
    }
    const step = Math.min(Number(intervalMs) || 500, Math.max(1, deadline - Date.now()))
    await sleep(step)
    waited += step
  }
}
