#!/usr/bin/env node
/**
 * Collect the RUNTIME source tree of the agent into the desktop package.
 *
 * Why the source tree and not just the wheel: the backend reads a lot of
 * things by their repo-relative path -- `skills/`, `optional-skills/`,
 * `plugins/<name>/plugin.yaml`, `locales/*.yaml`, `scripts/` -- and the wheel
 * only carries a part of that (locales and the MCP catalogs travel as
 * data-files; `skills/` only travels in the sdist). Shipping the source
 * tree keeps the layout byte-for-byte identical to a git checkout, so every
 * one of those paths resolves exactly as it does today, and the install can
 * stay an editable install of that tree.
 *
 * What is NOT copied (it is build-time or development-only):
 *   - `web/`, `ui-tui/`  -- the sources of `plobi_cli/web_dist` and
 *     `plobi_cli/tui_dist`; the BUILT artefacts are what ships.
 *   - `tests/`, `website/`, `apps/`, `ci/`, `docker/`, `nix/`, `docs/`,
 *     `.github/`, `packaging/`, `datagen-config-examples/`
 *   - `.git/`, `node_modules/`, `__pycache__/`, `venv/`
 *
 * Usage: node scripts/collect-backend-bundle.mjs [--out <dir>]
 *   --out defaults to <repo>/apps/desktop/build/backend
 *
 * The dependency wheels (the "wheelhouse") are produced by the CI step that
 * runs `uv export` + `pip download`; this script only lays out the source
 * side. Both end up under the app's `resources/` via extraResources.
 */

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const DESKTOP_ROOT = path.resolve(HERE, '..')
const REPO_ROOT = path.resolve(DESKTOP_ROOT, '..', '..')

const args = process.argv.slice(2)
const outIdx = args.indexOf('--out')
const OUT_DIR =
  outIdx >= 0 && args[outIdx + 1]
    ? path.resolve(args[outIdx + 1])
    : path.join(DESKTOP_ROOT, 'build', 'backend')

/** Directories the backend reads at runtime. */
const RUNTIME_DIRS = [
  'plobi_cli',
  'plobi',
  'agent',
  'gateway',
  'tui_gateway',
  'cron',
  'acp_adapter',
  'plugins',
  'providers',
  'tools',
  'skills',
  'optional-skills',
  'locales',
  'optional-mcps',
  'scripts',
  'assets',
  'datagen-config-examples',
]

/** Files the backend (or the installer running against the tree) needs. */
const RUNTIME_FILES = [
  'pyproject.toml',
  'uv.lock',
  'MANIFEST.in',
  'LICENSE',
]

/** Top-level modules the wheel declares as `py-modules`. */
const RUNTIME_MODULES = [
  'run_agent.py',
  'model_tools.py',
  'toolsets.py',
  'batch_runner.py',
  'trajectory_compressor.py',
  'toolset_distributions.py',
  'cli.py',
  'plobi_bootstrap.py',
  'plobi_constants.py',
  'plobi_state.py',
  'plobi_time.py',
  'plobi_logging.py',
  'utils.py',
  'mcp_serve.py',
  'mcp_tools_serve.py',
  'plobi.py',
  'plobi_user_inbox.py',
  'plobi_round_lock.py',
]

const SKIP = new Set(['.git', 'node_modules', '__pycache__', 'venv', '.venv'])

function copyDir(src, dest) {
  fs.mkdirSync(dest, { recursive: true })

  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    if (SKIP.has(entry.name)) continue
    const from = path.join(src, entry.name)
    const to = path.join(dest, entry.name)

    if (entry.isDirectory()) {
      copyDir(from, to)
    } else if (entry.isSymbolicLink()) {
      // A symlink that points outside the tree would break the copy; keep the
      // content instead (Windows checkouts rarely have them, but the tarball
      // export does).
      fs.copyFileSync(fs.realpathSync(from), to)
    } else {
      fs.copyFileSync(from, to)
    }
  }
}

function dirSizeMB(dir) {
  let total = 0
  const walk = d => {
    let entries = []
    try {
      entries = fs.readdirSync(d, { withFileTypes: true })
    } catch {
      return
    }
    for (const e of entries) {
      const p = path.join(d, e.name)
      if (e.isDirectory()) walk(p)
      else {
        try {
          total += fs.statSync(p).size
        } catch {
          /* raced away */
        }
      }
    }
  }
  walk(dir)
  return total / 1048576
}

fs.rmSync(OUT_DIR, { recursive: true, force: true })
fs.mkdirSync(OUT_DIR, { recursive: true })

// The wheelhouse (the locked dependency wheels) is produced by the CI step
// (`uv export` + `pip download`). Create the directory so electron-builder
// always finds it -- an empty one on the platforms that do not bundle the
// backend yet.
const WHEELHOUSE_DIR = path.join(DESKTOP_ROOT, 'build', 'wheelhouse')
fs.mkdirSync(WHEELHOUSE_DIR, { recursive: true })

// The toolchain bundle (PortableGit + ripgrep, Windows only) lands beside it.
// The folder must exist even when nothing was collected (mac/linux, or a local
// build that skips the collection step) -- electron-builder's extraResources
// fails on a missing source directory.
const TOOLCHAIN_DIR = path.join(DESKTOP_ROOT, 'build', 'toolchain')
fs.mkdirSync(TOOLCHAIN_DIR, { recursive: true })

// --- the local quota hub (`Desktop Quotas` sidecar) -------------------------
// The gateway is spawned as `python -m quota_hub start --config <dir>/config.yaml`
// with cwd=<this dir> (see electron/main.ts startAigwGateway). Shipping the
// checkout -- the `quota_hub/` package *and* config.yaml beside it -- is what
// makes a fresh install able to start the hub from `resources/quota_hub`
// instead of falling back to a developer checkout that does not exist.
//
// What is NOT copied: caches, virtualenvs, the test suite and build metadata.
const QUOTA_HUB_SRC = path.join(REPO_ROOT, 'quota_hub')
const QUOTA_HUB_OUT = path.join(DESKTOP_ROOT, 'build', 'quota_hub')

const QUOTA_HUB_SKIP_DIRS = new Set([
  '.git',
  '.venv',
  '.venv-bak-broken',
  'venv',
  '__pycache__',
  'node_modules',
  'tests',
  'dist',
  '.mypy_cache',
  '.pytest_cache',
  '.ruff_cache',
  '.tox',
])

/** Files the gateway needs at runtime, or the interpreter probe reads. */
const QUOTA_HUB_RUNTIME_FILES = [
  'config.yaml',
  'config.antigravity-desktop.yaml',
  'pyproject.toml',
  'requirements.txt',
]

function copyQuotaHub(src, dest) {
  fs.mkdirSync(dest, { recursive: true })

  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    if (QUOTA_HUB_SKIP_DIRS.has(entry.name) || entry.name.endsWith('.egg-info')) continue
    const from = path.join(src, entry.name)
    const to = path.join(dest, entry.name)

    if (entry.isDirectory()) {
      copyQuotaHub(from, to)
    } else if (entry.isSymbolicLink()) {
      fs.copyFileSync(fs.realpathSync(from), to)
    } else if (!entry.name.endsWith('.pyc')) {
      fs.copyFileSync(from, to)
    }
  }
}

fs.rmSync(QUOTA_HUB_OUT, { recursive: true, force: true })

if (fs.existsSync(QUOTA_HUB_SRC)) {
  copyQuotaHub(QUOTA_HUB_SRC, QUOTA_HUB_OUT)
  const hubPkg = path.join(QUOTA_HUB_OUT, 'quota_hub')
  const missingHub = QUOTA_HUB_RUNTIME_FILES.filter(f => !fs.existsSync(path.join(QUOTA_HUB_OUT, f)))
  console.log(`[collect-backend-bundle] wrote ${QUOTA_HUB_OUT}`)
  console.log(`  size: ${dirSizeMB(QUOTA_HUB_OUT).toFixed(1)} MB`)
  console.log(`  importable package: ${fs.existsSync(path.join(hubPkg, '__init__.py'))}`)

  if (missingHub.length) {
    console.warn(`  WARNING: quota hub is missing ${missingHub.join(', ')}`)
  }

  // `python -m quota_hub` needs the package itself, not just the directory.
  if (!fs.existsSync(path.join(hubPkg, '__main__.py')) || !fs.existsSync(path.join(QUOTA_HUB_OUT, 'config.yaml'))) {
    console.warn(
      '[collect-backend-bundle] WARNING: quota_hub bundle is incomplete (need quota_hub/__main__.py + config.yaml) -- ' +
        'the packaged desktop could not start the Desktop Quotas gateway.'
    )
  }
} else {
  fs.mkdirSync(QUOTA_HUB_OUT, { recursive: true })
  console.warn(
    `[collect-backend-bundle] WARNING: ${QUOTA_HUB_SRC} not found -- resources/quota_hub will be empty and ` +
      'Desktop Quotas cannot start on this build.'
  )
}

const missing = []
let copied = 0

for (const dir of RUNTIME_DIRS) {
  const from = path.join(REPO_ROOT, dir)
  if (!fs.existsSync(from)) {
    missing.push(dir)
    continue
  }
  copyDir(from, path.join(OUT_DIR, dir))
  copied += 1
}

for (const file of [...RUNTIME_FILES, ...RUNTIME_MODULES]) {
  const from = path.join(REPO_ROOT, file)
  if (!fs.existsSync(from)) {
    missing.push(file)
    continue
  }
  fs.copyFileSync(from, path.join(OUT_DIR, file))
}

// The locked requirements (the universal export the CI produced) travel WITH
// the tree: the offline install reads it (`uv pip install --no-index
// --find-links <wheelhouse> --require-hashes -r requirements.txt`). It is not
// the lockfile -- `uv sync` would install from the URLs the lock records and
// reach for the network even with a full wheelhouse beside it.
const REQUIREMENTS = path.join(DESKTOP_ROOT, 'build', 'requirements.txt')
if (fs.existsSync(REQUIREMENTS)) {
  fs.copyFileSync(REQUIREMENTS, path.join(OUT_DIR, 'requirements.txt'))
  console.log(`  requirements.txt: copied (${(fs.statSync(REQUIREMENTS).size / 1024).toFixed(0)} KB)`)
} else {
  console.warn(
    '[collect-backend-bundle] WARNING: build/requirements.txt is missing -- the offline install cannot run (the first launch would fall back to PyPI).'
  )
}

const webDist = path.join(OUT_DIR, 'plobi_cli', 'web_dist')
const tuiDist = path.join(OUT_DIR, 'plobi_cli', 'tui_dist')

console.log(`[collect-backend-bundle] wrote ${OUT_DIR}`)
console.log(`  entries: ${copied} dirs + ${RUNTIME_FILES.length + RUNTIME_MODULES.length - missing.length} files`)
console.log(`  size: ${dirSizeMB(OUT_DIR).toFixed(1)} MB`)
console.log(`  web_dist present: ${fs.existsSync(webDist)}  tui_dist present: ${fs.existsSync(tuiDist)}`)

if (missing.length) {
  console.log(`  MISSING (not copied): ${missing.join(', ')}`)
}

// The dashboard the backend serves is a BUILD ARTEFACT (`plobi_cli/web_dist`).
// Without it the packaged backend has no UI to serve, so the CI step that
// builds the web bundle must have run. Say so loudly rather than shipping a
// package whose backend would answer with an empty page.
if (!fs.existsSync(webDist)) {
  console.warn(
    '[collect-backend-bundle] WARNING: plobi_cli/web_dist is missing -- the packaged backend would serve no dashboard.'
  )
}

// --- the WeChat collector engine: HARD gate --------------------------------
// The vendored chatlog is a Go project and its `bin/` is gitignored
// (`vendor/chatlog/.gitignore`), so a checkout -- and therefore every CI
// runner -- has no binary until somebody builds one. Shipping a package
// without it is how users ended up with a collector that says "unavailable"
// and a help text telling them to go build it themselves.
//
// Warn-and-continue is what let that happen, so this one EXITS. Building it is
// one command: `npm run build:chatlog` (Windows needs a mingw gcc on PATH for
// CGO; see scripts/build-chatlog.mjs).
const CHATLOG_BIN_NAMES = { win32: 'chatlog.exe', darwin: 'chatlog', linux: 'chatlog' }
const targetPlatform = process.env.PLOBI_CHATLOG_TARGET_PLATFORM || process.platform
const chatlogBinName = CHATLOG_BIN_NAMES[targetPlatform]

if (!chatlogBinName) {
  console.warn(
    `[collect-backend-bundle] WARNING: unknown target platform "${targetPlatform}" -- ` +
      'skipping the WeChat collector binary check (known: ' + Object.keys(CHATLOG_BIN_NAMES).join(', ') + ').'
  )
} else {
  const chatlogRel = path.join('plobi', 'collectors', 'chatlog', 'vendor', 'chatlog', 'bin', chatlogBinName)
  const chatlogSource = path.join(REPO_ROOT, chatlogRel)
  const chatlogStaged = path.join(OUT_DIR, chatlogRel)
  const skipAssert = process.env.PLOBI_SKIP_CHATLOG_ASSERT === '1'

  let ok = false
  let detail = fs.existsSync(chatlogSource)
    ? `not copied into the bundle (source exists: ${chatlogSource})`
    : `not built (no ${chatlogSource})`
  if (fs.existsSync(chatlogStaged)) {
    const size = fs.statSync(chatlogStaged).size
    const fd = fs.openSync(chatlogStaged, 'r')
    const head = Buffer.alloc(4)
    fs.readSync(fd, head, 0, 4, 0)
    fs.closeSync(fd)
    const magic = head.toString('latin1')
    // `go build -o` writes a Go archive (and exits 0) when the -o package is not
    // `main`; magic bytes are the only cheap way to tell that from a real binary.
    ok = (targetPlatform === 'win32' ? magic.startsWith('MZ') : magic.startsWith('\x7fELF')) && size > 1048576
    detail = `magic ${JSON.stringify(magic)}, ${(size / 1048576).toFixed(1)} MB`
  }

  if (ok) {
    console.log(`  wechat collector binary: ${chatlogBinName} present (${detail})`)
  } else if (skipAssert) {
    console.warn(
      '\n' +
        '  **********************************************************\n' +
        '  WARNING: the WeChat collector engine is NOT in this package\n' +
        `  (${chatlogStaged} -- ${detail}).\n` +
        '  PLOBI_SKIP_CHATLOG_ASSERT=1 was set, so packaging continues.\n' +
        '  The installed app will show the collector as unavailable.\n' +
        '  **********************************************************\n'
    )
  } else {
    console.error(
      '\n' +
        '✗ collect-backend-bundle: the WeChat collector engine is missing from the bundle.\n' +
        `  expected: ${chatlogStaged}\n` +
        `  (copied from ${chatlogSource} -- ${detail})\n` +
        '\n' +
        '  Why this is fatal: the collector is a Go binary whose `bin/` is gitignored,\n' +
        '  so it is NOT in a fresh checkout. Packaging without it ships a build in which\n' +
        '  the WeChat message collector can never run, and no user-side action fixes it.\n' +
        '\n' +
        '  Build it, then re-run:\n' +
        '    npm run build:chatlog        (or: node scripts/build-chatlog.mjs --force)\n' +
        '  Windows needs a C compiler for CGO (mingw-w64 gcc on PATH, or set CC).\n' +
        '\n' +
        '  Emergency only (produces a knowingly broken package):\n' +
        '    PLOBI_SKIP_CHATLOG_ASSERT=1 npm run dist\n'
    )
    process.exit(1)
  }
}

