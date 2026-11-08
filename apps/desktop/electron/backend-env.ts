import fs from 'node:fs'
import path from 'node:path'

import { readWindowsUserEnvVar } from './windows-user-env'

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

/**
 * 脑与人读面住哪：env → 注册表 → 什么都不给。
 *
 * 2026-11-07 现核：从**开始菜单**启动的那个 app，它的进程环境是**登录那一刻**的快照；
 * 之后用 `setx` 写进 HKCU 的 MIND_ROOT／SURFACE_ROOT，它一个字都看不见 ⇒ 后端（继承
 * 这份环境）就按旧串去读脑 ⇒ **开机自启那条路读不到脑**。注册表那份是活的
 * （`readWindowsUserEnvVar` 读的就是 HKCU\Environment）⇒ 这一格按 env → 注册表 的
 * 次序解析，与 PLOBI_HOME 那边同一套口径（见 windows-user-env.ts 的注释与 #45471）。
 *
 * 继承来的那串可能已经不在了（长驻进程继承的是登录那一刻的快照）：那一份不在、
 * 注册表那份还在 ⇒ 改读注册表那份。与 Python 侧 `paths.py::_surface_pointer()` 同一条
 * 口径（"宁可不静默换家"）。
 *
 * 两格都读不到时**什么都不给**：让 Python 侧按它自己的默认走（脑在仓内兄弟目录、
 * 人读面在 `<mind>/Surface`），不在这里另造一个默认。
 */
export function resolveBrainSurfacePointers({
  currentEnv = process.env,
  platform = process.platform,
  readEnvVar = readWindowsUserEnvVar,
  exists = (candidate: string) => {
    try {
      return fs.existsSync(candidate)
    } catch {
      return false
    }
  }
}: {
  currentEnv?: NodeJS.ProcessEnv
  platform?: NodeJS.Platform
  readEnvVar?: typeof readWindowsUserEnvVar
  exists?: (candidate: string) => boolean
} = {}) {
  const out: Record<string, string> = {}

  for (const name of ['MIND_ROOT', 'SURFACE_ROOT']) {
    const fromEnv = String(currentEnv?.[name] ?? '').trim()
    const fromRegistry = String(readEnvVar(name, { platform, env: currentEnv }) ?? '').trim()
    let value = fromEnv || fromRegistry

    if (fromEnv && fromRegistry && fromRegistry !== fromEnv && !exists(fromEnv) && exists(fromRegistry)) {
      value = fromRegistry
    }

    if (value) {
      out[name] = value
    }
  }

  return out
}

/**
 * 由**数据根**派生"脑"与"人读面"的两格指针。
 *
 * 2026-11-08 起这两格不再从 env / 注册表捡：那两处是**上一次安装**钉的门牌，
 * 装机版与开发态共用同一格 ⇒ 谁后装谁把对方带偏（这台现核：开发态
 * `Plobi-Dev\mind` 与装机版 `Plobi\mind` 抢同一格 MIND_ROOT）。
 * 数据根（`PLOBI_HOME`，见 data-root.ts）才是唯一的上游——脑在家里的 `mind/`，
 * 人读面就住在家本体（2026-11-03 起不再有 `<家>\surface` 这一层）。
 *
 * `plobiHome` 为空 ⇒ 返空对象，由调用方退回 `resolveBrainSurfacePointers()`
 * 的旧口径（不在这里另造默认，见那个函数末尾的说明）。
 */
function deriveBrainSurfacePointers({
  plobiHome,
  platform = process.platform,
  pathModule = pathModuleForPlatform(platform)
}: {
  plobiHome?: string | null
  platform?: NodeJS.Platform
  pathModule?: any
} = {}) {
  const home = String(plobiHome ?? '').trim()

  if (!home) {
    return {}
  }

  return {
    MIND_ROOT: pathModule.join(home, 'mind'),
    SURFACE_ROOT: home
  }
}

function buildDesktopBackendEnv({
  plobiHome,
  pythonPathEntries = [],
  venvRoot,
  currentEnv = process.env,
  platform = process.platform,
  pathModule = pathModuleForPlatform(platform),
  brainSurface,
  // 仅测试注入：旧口径那道兜底会读本机注册表，开发机上残留的 `MIND_ROOT` 会
  // 让"没给数据根"的用例读到真实值而假红。生产路径不传，仍走 readWindowsUserEnvVar。
  readEnvVar,
  exists
}: any = {}) {
  const delimiter = delimiterForPlatform(platform)
  // 先把继承来的环境修合法（大小写重复键），再往上叠我们自己的两格——
  // 否则 `HTTPS_PROXY` + `https_proxy` 这种会被原样传给 PowerShell 一侧，
  // 那边一碰就抛（见 `dedupeEnvKeys` 的注释：那一整串"后端起不来"的根因）。
  const base = dedupeEnvKeys(currentEnv || {})
  const currentPythonPath = base.PYTHONPATH || ''
  const key = pathEnvKey(base, platform)
  const home = String(plobiHome ?? '').trim()
  // 数据根说了算：三格一律由它派生，绝不再从继承来的 env / 注册表捡——
  // 那两个都是"上一次安装"钉的门牌，装机版与开发态会互相带偏。
  // 调用方没给 `plobiHome`（老调用点 / 单测）时才退回旧口径。
  const pointers = home
    ? deriveBrainSurfacePointers({ plobiHome: home, platform, pathModule })
    : resolveBrainSurfacePointers({ currentEnv, platform, ...(readEnvVar ? { readEnvVar } : {}), ...(exists ? { exists } : {}) })
  const brainSurfaceValues = brainSurface ?? pointers

  return {
    PYTHONPATH: appendUniquePathEntries([...pythonPathEntries, currentPythonPath], { delimiter }),
    [key]: buildDesktopBackendPath({
      plobiHome,
      venvRoot,
      currentPath: currentPathValue(base, platform),
      platform,
      pathModule
    }),
    // 后端（Python 侧）不从继承来的环境里"猜"家：`get_plobi_home()` 的第二档就是
    // `PLOBI_HOME` env，这里显式写成数据根，装机版读装机版的格、开发态读开发态的格。
    // 展开顺序：这三格写在最后 ⇒ 覆盖 `currentEnv` 里同名的脏值（大小写不同也算，
    // Windows 环境块不区分大小写，后写的那一格赢）。
    ...(home ? { PLOBI_HOME: home } : {}),
    ...brainSurfaceValues
  }
}

export {
  appendUniquePathEntries,
  buildDesktopBackendEnv,
  buildDesktopBackendPath,
  delimiterForPlatform,
  deriveBrainSurfacePointers,
  desktopCliSearchPath,
  normalizePlobiHomeRoot,
  pathEnvKey,
  POSIX_SANE_PATH_ENTRIES
}
