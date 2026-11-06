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
