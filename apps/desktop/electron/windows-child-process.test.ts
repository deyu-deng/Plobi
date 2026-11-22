import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const ELECTRON_DIR = path.dirname(fileURLToPath(import.meta.url))

// Deliberate lightweight guard (kept as source-grep snapshot tests on purpose):
// these protect the Windows child-process console-hidden invariants (CREATE_NO_WINDOW,
// no pythonw, no cmd.exe handoff) from regressions. Full behavioral tests would need
// electron/pty mocking that is disproportionate to the blast radius of a regression here.
// REWRITE DIRECTION (tracked debt): move to a compile-time contract + runtime seam
// (e.g. export childLaunchOptions for unit tests) when the desktop launcher is refactored.

function readElectronFile(name) {
  return fs.readFileSync(path.join(ELECTRON_DIR, name), 'utf8').replace(/\r\n/g, '\n')
}

function requireHiddenChildOptions(source, needle) {
  const match = needle instanceof RegExp ? needle.exec(source) : null
  const index = needle instanceof RegExp ? (match?.index ?? -1) : source.indexOf(needle)
  assert.notEqual(index, -1, `missing call site: ${needle}`)
  const snippet = source.slice(index, index + 700)
  assert.match(
    snippet,
    /hiddenWindowsChildOptions\(/,
    `expected ${needle} to wrap child-process options with hiddenWindowsChildOptions`
  )
}

test('desktop background child processes opt into hidden Windows consoles', () => {
  const source = readElectronFile('main.ts')

  assert.match(source, /function hiddenWindowsChildOptions\(options: any = \{\}\)/)

  requireHiddenChildOptions(source, "execFileSync(\n          'reg'")
  requireHiddenChildOptions(source, /execFileSync\(\s*pyExe/)
  requireHiddenChildOptions(source, /spawn\(\s*resolveGitBinary\(\)/)
  requireHiddenChildOptions(source, "execFileSync('taskkill'")
  requireHiddenChildOptions(source, /spawn\(\s*command,\s*args/)
  requireHiddenChildOptions(source, "spawn('curl'")
  requireHiddenChildOptions(source, /spawn\(\s*backend\.command,\s*backend\.args/)
  requireHiddenChildOptions(source, /plobiProcess = spawn\(\s*backend\.command,\s*backend\.args/)
  requireHiddenChildOptions(source, /spawn\(\s*py,\s*\['-m', 'plobi_cli\.main', 'uninstall', '--gui-summary'\]/)
  // The restart handoff is the one child that must OUTLIVE us: launched through a
  // hidden wscript launcher, not `cmd.exe /c x.bat` (that attaches to our console —
  // visible window, and our own teardown's CTRL event killed the relaunch, 0xC000013A).
  requireHiddenChildOptions(source, /spawn\(\s*'wscript\.exe',\s*\['\/\/NoLogo',\s*launcherPath/)
  assert.doesNotMatch(
    source,
    /spawn\('cmd\.exe', \['\/c', scriptPath\]/,
    'handoff 不许再用 cmd.exe 直接起：继承我们的 console 就等于又给用户一枚可见窗口'
  )

  assert.match(source, /function unwrapWindowsVenvPlobiCommand\(command, backendArgs\)/)
  assert.match(source, /function getVenvSitePackagesEntries\(venvRoot\)/)
  assert.match(source, /path\.join\(venvRoot, 'Lib', 'site-packages'\)/)
  assert.match(source, /args: \['-m', 'plobi_cli\.main', \.\.\.backendArgs\]/)
})

test('desktop backend launches console python so child consoles are inherited, not pythonw', () => {
  const source = readElectronFile('main.ts')

  // The flash fix is structural: the backend runs as a console-subsystem
  // python.exe under hiddenWindowsChildOptions() (-> CREATE_NO_WINDOW), so it
  // owns ONE windowless console that every descendant spawn inherits. Launching
  // it as GUI-subsystem pythonw.exe is what made each child allocate (and flash)
  // its own console, so the backend command must never be pythonw.
  assert.doesNotMatch(source, /pythonw\.exe'\)/, 'backend must not be launched via pythonw.exe')
  assert.doesNotMatch(
    source,
    /function getNoConsoleVenvPython\b/,
    'pythonw-conversion helper should be gone; console python is launched directly'
  )
  assert.doesNotMatch(
    source,
    /function applyWindowsNoConsoleSpawnHints\b/,
    'pythonw spawn-hint rewriter should be gone'
  )

  // Console python restores stdout, so the port is announced on the normal
  // PLOBI_DASHBOARD_READY stdout line — no ready-file side channel is set.
  assert.doesNotMatch(source, /readyFile: true/, 'no backend should opt into the pythonw ready-file path')

  // Both desktop backend launches must still go through hiddenWindowsChildOptions
  // so the single backend console is created windowless.
  requireHiddenChildOptions(source, /spawn\(\s*backend\.command,\s*backend\.args/)
  requireHiddenChildOptions(source, /plobiProcess = spawn\(\s*backend\.command,\s*backend\.args/)
})

test('desktop backend teardown tree-kills Windows backend descendants', () => {
  const source = readElectronFile('main.ts')

  const helperIndex = source.indexOf('function stopBackendChild(child)')
  assert.notEqual(helperIndex, -1, 'missing backend teardown helper')
  const helperSnippet = source.slice(helperIndex, helperIndex + 500)
  assert.match(helperSnippet, /IS_WINDOWS && Number\.isInteger\(child\.pid\)/)
  assert.match(helperSnippet, /forceKillProcessTree\(child\.pid\)/)
  assert.match(helperSnippet, /child\.kill\('SIGTERM'\)/)

    // Match the declaration by name, not by an exact parameter list: the
  // signature carries `{ soft = false } = {}` today, and pinning the parens
  // made this assertion dead (it predates R-038 — this suite never ran).
  const resetIndex = source.indexOf('function resetPlobiConnection(')
  assert.notEqual(resetIndex, -1, 'missing resetPlobiConnection')
  const resetSnippet = source.slice(resetIndex, resetIndex + 300)
  assert.match(resetSnippet, /stopBackendChild\(plobiProcess\)/)
  assert.doesNotMatch(resetSnippet, /plobiProcess\.kill\('SIGTERM'\)/)

  const quitIndex = source.indexOf("app.on('before-quit'")
  assert.notEqual(quitIndex, -1, 'missing before-quit handler')
  // The handler is long (tray teardown, pet overlay, installer abort, log
  // flush...) and the backend stop sits near its end, so the window has to
  // cover the whole body — 900 chars cut it off and made this dead.
  const quitSnippet = source.slice(quitIndex, quitIndex + 3000)
  assert.match(quitSnippet, /stopBackendChild\(plobiProcess\)/)
  assert.doesNotMatch(quitSnippet, /plobiProcess\.kill\('SIGTERM'\)/)
})

test('intentional or interactive desktop child processes stay documented', () => {
  const source = readElectronFile('main.ts')

  assert.match(source, /windowsHide: false/)
  assert.match(source, /handOffWindowsBootstrapRecovery/)
  assert.match(source, /'--repair', '--branch'/)
  assert.match(source, /'--update', '--branch'/)
  assert.match(source, /nodePty\.spawn\(command, args/)
  assert.match(source, /spawn\('cmd\.exe', \['\/c', 'start'/)
})

test('bootstrap PowerShell runner hides Windows console children', () => {
  const source = readElectronFile('bootstrap-runner.ts')

  assert.match(source, /function hiddenWindowsChildOptions\(options = \{\}\)/)
  requireHiddenChildOptions(source, /spawn\(\s*ps,\s*fullArgs/)
})

// ---------------------------------------------------------------------------
// 0.3.3 的黑窗：helper 明明 windowsHide 了，windowsHide 只管直接子进程，
// 且 detached 会让孙进程（powershell）自己新开一块可见控制台。
// ---------------------------------------------------------------------------

test('the update helper is spawned windowless, never detached', () => {
  const release = readElectronFile('desktop-release.ts')
  const winspawn = readElectronFile('winspawn.ts')

  const spawnIndex = release.indexOf('export function spawnUpdateHelper')
  assert.notEqual(spawnIndex, -1, 'missing spawnUpdateHelper')

  const snippet = release.slice(spawnIndex, spawnIndex + 1200)

  assert.match(release, /import \{ windowlessLaunchOptions \} from '\.\/winspawn'/)
  assert.match(snippet, /windowlessLaunchOptions\(\)/)
  assert.doesNotMatch(
    snippet,
    /detached: true/,
    'Windows 上 detached=DETACHED_PROCESS：helper 没有控制台，它内部的 powershell 只能自己新分配一块**可见**的，黑窗就是这么来的'
  )

  assert.match(winspawn, /export const CREATE_NO_WINDOW = 0x08000000/)
  assert.match(winspawn, /process\.platform === 'win32' \? extra \| CREATE_NO_WINDOW : extra/)
  assert.match(winspawn, /detached: false, creationflags: hiddenFlags\(\)/)
})

test('the installer stops shouting once it has no console to shout into', () => {
  const install = fs
    .readFileSync(path.join(ELECTRON_DIR, '..', '..', '..', 'scripts', 'install.ps1'), 'utf8')
    .replace(/\r\n/g, '\n')

  // 所有人类可读输出都穿过同一个闸门；Write-Host 只允许出现在闸门内部。
  assert.match(install, /function Write-PlobiLine \{/)
  assert.match(install, /function Test-PlobiQuietOutput \{/)
  assert.match(install, /\$NonInteractive\) \{ \$quiet = \$true \}/)
  assert.match(install, /Write-Host @hostArgs\n/)

  const directHostCalls = install.split('\n').filter((line) => /^\s*Write-Host\b/.test(line))
  assert.deepEqual(directHostCalls, ['        Write-Host @hostArgs'], 'install.ps1 里任何一处裸 Write-Host 都会在无窗口控制台里被丢掉，又或者在可见控制台里刷屏')

  // stage 协议的 stdout 不能被动：它还是 Write-Output 的活儿。
  assert.match(install, /Write-Output/)
})
