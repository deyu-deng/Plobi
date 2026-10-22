import path from 'node:path'

// Match the POSIX fallback surface used by the Python terminal environment.
// macOS apps launched from Finder/Dock often inherit only /usr/bin:/bin:/usr/sbin:/sbin,
// which misses Apple Silicon Homebrew and user-installed CLI tools such as codex.
const POSIX_SANE_PATH_ENTRIES = Object.freeze([
  '/opt/homebrew/bin',
  '/opt/homebrew/sbin',
  '/usr/local/sbin',
  '/usr/local/bin',
  '/usr/sbin',
  '/usr/bin',
  '/sbin',
  '/bin'
])

function delimiterForPlatform(platform = process.platform) {
  return platform === 'win32' ? ';' : ':'
}

function pathModuleForPlatform(platform = process.platform) {
  return platform === 'win32' ? path.win32 : path.posix
}

function pathEnvKey(env = process.env, platform = process.platform) {
  if (platform !== 'win32') {
    return 'PATH'
  }

  return Object.keys(env || {}).find(key => key.toUpperCase() === 'PATH') || 'PATH'
}

function currentPathValue(env = process.env, platform = process.platform) {
  const key = pathEnvKey(env, platform)

  return env?.[key] || ''
}

function appendUniquePathEntries(entries, { delimiter = path.delimiter } = {}) {
  const seen = new Set()
  const ordered = []

  for (const entry of entries) {
    if (!entry) {
      continue
    }

    const parts = Array.isArray(entry) ? entry : String(entry).split(delimiter)

    for (const part of parts) {
      if (!part || seen.has(part)) {
        continue
      }

      seen.add(part)
      ordered.push(part)
    }
  }

  return ordered.join(delimiter)
}

function buildDesktopBackendPath({
  plobiHome,
  venvRoot,
  currentPath = '',
  platform = process.platform,
  pathModule = pathModuleForPlatform(platform)
}: any = {}) {
  const delimiter = delimiterForPlatform(platform)
  const plobiNodeBin = plobiHome ? pathModule.join(plobiHome, 'node', 'bin') : null
  const venvBin = venvRoot ? pathModule.join(venvRoot, platform === 'win32' ? 'Scripts' : 'bin') : null
  const saneEntries = platform === 'win32' ? [] : POSIX_SANE_PATH_ENTRIES

  return appendUniquePathEntries([plobiNodeBin, venvBin, currentPath, saneEntries], { delimiter })
}

// desktopCliSearchPath — the PATH an already-installed `plobi` CLI is discovered
// against. Reuses buildDesktopBackendPath's sane-PATH surface (which already
// folds in Homebrew, the Plobi-managed node/venv bins, and the system dirs) and
// additionally prepends the well-known per-user install bins. A GUI app launched
// from Finder/Dock inherits launchd's minimal PATH (/usr/bin:/bin:/usr/sbin:/sbin)
// that omits ~/.local/bin and ~/.cargo/bin — exactly where a CLI install lives —
// so widening the search here mirrors the reachability a shell launch has.
// POSIX only; Windows CLI discovery runs off the installer/registry probes and a
// bare dir scan would pick up the wrong (extensionless) file.
function desktopCliSearchPath({
  home,
  plobiHome,
  venvRoot,
  currentPath = '',
  platform = process.platform,
  pathModule = pathModuleForPlatform(platform)
}: any = {}) {
  const base = buildDesktopBackendPath({ plobiHome, venvRoot, currentPath, platform, pathModule })

  if (platform === 'win32' || !home) {
    return base
  }

  const userBins = [pathModule.join(home, '.local', 'bin'), pathModule.join(home, '.cargo', 'bin')]

  return appendUniquePathEntries([userBins, base], { delimiter: delimiterForPlatform(platform) })
}

function normalizePlobiHomeRoot(plobiHome, { pathModule = pathModuleForPlatform(process.platform) }: any = {}) {
  if (!plobiHome) {
    return plobiHome
  }

  const resolved = pathModule.resolve(String(plobiHome))
  const parent = pathModule.dirname(resolved)

  if (pathModule.basename(parent).toLowerCase() === 'profiles') {
    return pathModule.dirname(parent)
  }

  return resolved
}

/**
 * 把 env 里**大小写重复**的键归一成一个（后写覆盖先写，与 Windows 环境块同语义）。
 *
 * 2026-10-22 现核的一整串诡异故障的根因：WorkBuddy 沙箱起子进程时同时塞了
 * `HTTPS_PROXY` 与 `https_proxy`（还有 `HTTP_PROXY`/`http_proxy`，值都相同）。
 * Node 的 env 对象大小写敏感，两个键都活着、相安无事；可这份 env 一旦交给
 * **PowerShell**（bootstrap 的 install.ps1、任何 Start-Process），.NET 的环境
 * 字典大小写**不**敏感 ⇒ `An item with the same key has already been added`
 * ⇒ Start-Process 抛 ArgumentException ⇒ 子进程 exit=**1073807364**（0x3FFF0004）
 * ⇒ bootstrap 的 config-templates 阶段 "produced no JSON result frame"
 * ⇒ 后端起不来 ⇒ 桌面 `Timed out connecting to Plobi backend after 15000ms`
 * ⇒ **L2 Agent 进不去、互相发不了信息**。
 *
 * 同一台机器上 `Get-ChildItem Env:` 都会炸（现核），所以这不是我们的代码写错，
 * 是**继承来的环境本身不合法**——我们必须在自己这一侧把它修合法。
 */
export function dedupeEnvKeys(env: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
  const out: NodeJS.ProcessEnv = {}
  const keptByLower = new Map<string, string>()

  for (const [key, value] of Object.entries(env)) {
    const lower = key.toLowerCase()
    const previous = keptByLower.get(lower)

    if (previous !== undefined && previous !== key) {
      // 同一键的另一种拼写已经写过了：按"后写覆盖"把先那个删掉。
      delete out[previous]
    }

    keptByLower.set(lower, key)
    out[key] = value
  }

  return out
}

function buildDesktopBackendEnv({
  plobiHome,
  pythonPathEntries = [],
  venvRoot,
  currentEnv = process.env,
  platform = process.platform,
  pathModule = pathModuleForPlatform(platform)
}: any = {}) {
  const delimiter = delimiterForPlatform(platform)
  // 先把继承来的环境修合法（大小写重复键），再往上叠我们自己的两格——
  // 否则 `HTTPS_PROXY` + `https_proxy` 这种会被原样传给 PowerShell 一侧，
  // 那边一碰就抛（见 `dedupeEnvKeys` 的注释：那一整串"后端起不来"的根因）。
  const base = dedupeEnvKeys(currentEnv || {})
  const currentPythonPath = base.PYTHONPATH || ''
  const key = pathEnvKey(base, platform)

  return {
    PYTHONPATH: appendUniquePathEntries([...pythonPathEntries, currentPythonPath], { delimiter }),
    [key]: buildDesktopBackendPath({
      plobiHome,
      venvRoot,
      currentPath: currentPathValue(base, platform),
      platform,
      pathModule
    })
  }
}

export {
  appendUniquePathEntries,
  buildDesktopBackendEnv,
  buildDesktopBackendPath,
  delimiterForPlatform,
  desktopCliSearchPath,
  normalizePlobiHomeRoot,
  pathEnvKey,
  POSIX_SANE_PATH_ENTRIES
}
