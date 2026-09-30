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

function buildDesktopBackendEnv({
  plobiHome,
  pythonPathEntries = [],
  venvRoot,
  currentEnv = process.env,
  platform = process.platform,
  pathModule = pathModuleForPlatform(platform)
}: any = {}) {
  const delimiter = delimiterForPlatform(platform)
  const currentPythonPath = currentEnv?.PYTHONPATH || ''
  const key = pathEnvKey(currentEnv, platform)

  return {
    PYTHONPATH: appendUniquePathEntries([...pythonPathEntries, currentPythonPath], { delimiter }),
    [key]: buildDesktopBackendPath({
      plobiHome,
      venvRoot,
      currentPath: currentPathValue(currentEnv, platform),
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
