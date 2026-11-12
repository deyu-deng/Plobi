#!/usr/bin/env node
/**
 * Collect the LOCKED dependency wheels (the "wheelhouse") into the desktop
 * package.
 *
 * Why this file exists at all: the offline install used to be a CI-only step.
 * `release-desktop.yml` ran `uv export` + `pip download` between "build the
 * desktop app" and "collect the backend bundle", so a package built by CI had
 * a wheelhouse and a package built on someone's machine quietly did not. The
 * local build then handed out an installer whose first launch has to reach
 * PyPI -- on a machine behind the GFW, that is the difference between "opens"
 * and "sits at a spinner". 2026-11-10 measured exactly that: `build/wheelhouse`
 * was an empty directory and `build/requirements.txt` was missing, with nothing
 * but a WARNING in the build log.
 *
 * So the CI step moved in here, and both callers run this script:
 *   - `postbuild` (package.json) -> every local build ships an offline install
 *   - `release-desktop.yml`      -> the release leg uses the same commands
 *
 * What it does, in order:
 *   1. `uv export --format requirements-txt` from the repo lockfile. This is a
 *      REQUIREMENTS file, not a lockfile: the install reads it with
 *      `uv pip install -r`, while `uv sync` would go to the recorded URLs
 *      instead of the local wheelhouse.
 *   2. `pip download --require-hashes --only-binary=:all: --python-version 3.11`
 *      into `build/wheelhouse`. Two of those flags are load-bearing:
 *      `--only-binary` (an sdist would have to be compiled on the user's
 *      machine -- exactly what this bundle removes) and `--python-version 3.11`
 *      (the installers pin 3.11; without it the runner's own Python ships
 *      cp312 wheels the venv cannot use -- measured 2026-11-07: 19 cp312
 *      wheels and one cp311).
 *
 * A platform whose wheels are incomplete (an arm64 mac missing a dependency,
 * say) keeps the old behaviour: warn and let the install resolve from PyPI,
 * instead of failing the whole build.
 *
 * Usage: node scripts/collect-wheelhouse.mjs [--force] [--check]
 *   --force  re-download even when the stamp says the wheelhouse is current
 *   --check  do not download; exit 1 when the bundle is incomplete (for
 *            release jobs that must not ship a PyPI-fallback package)
 */

import { execFileSync } from 'node:child_process'
import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const DESKTOP_ROOT = path.resolve(HERE, '..')
const REPO_ROOT = path.resolve(DESKTOP_ROOT, '..', '..')

const BUILD_DIR = path.join(DESKTOP_ROOT, 'build')
const REQUIREMENTS = path.join(BUILD_DIR, 'requirements.txt')
const WHEELHOUSE = path.join(BUILD_DIR, 'wheelhouse')
/** Kept OUTSIDE the wheelhouse: extraResources copies that directory verbatim. */
const STAMP = path.join(BUILD_DIR, '.wheelhouse-stamp.json')

/** Pinned by the installers (scripts/install.ps1, scripts/install.sh). */
const PYTHON_VERSION = '3.11'

const args = process.argv.slice(2)
const FORCE = args.includes('--force')
const CHECK_ONLY = args.includes('--check')

function say(message) {
  console.log(`[collect-wheelhouse] ${message}`)
}

/** Fingerprint of the inputs: a changed lockfile means the wheels are stale. */
function inputsHash() {
  const lock = path.join(REPO_ROOT, 'uv.lock')
  const pyproject = path.join(REPO_ROOT, 'pyproject.toml')
  const hash = crypto.createHash('sha256')

  for (const file of [lock, pyproject]) {
    hash.update(path.basename(file))
    hash.update(fs.existsSync(file) ? fs.readFileSync(file) : Buffer.alloc(0))
  }
  hash.update(`python=${PYTHON_VERSION}`)

  return hash.digest('hex')
}

function readStamp() {
  try {
    return JSON.parse(fs.readFileSync(STAMP, 'utf-8'))
  } catch {
    return null
  }
}

function wheels() {
  try {
    return fs.readdirSync(WHEELHOUSE).filter(name => name.endsWith('.whl') || name.endsWith('.tar.gz'))
  } catch {
    return []
  }
}

function megabyte(bytes) {
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function wheelhouseBytes() {
  return wheels().reduce((total, name) => {
    try {
      return total + fs.statSync(path.join(WHEELHOUSE, name)).size
    } catch {
      return total
    }
  }, 0)
}

/** Is the bundle ready to be packaged, as far as we can tell without installing it? */
function bundleState() {
  const problems = []

  if (!fs.existsSync(REQUIREMENTS) || fs.statSync(REQUIREMENTS).size === 0) {
    problems.push('build/requirements.txt is missing')
  }
  if (wheels().length === 0) {
    problems.push('build/wheelhouse has no wheels')
  }

  return problems
}

function run(command, argv) {
  say(`$ ${command} ${argv.map(a => (a.includes(' ') ? `"${a}"` : a)).join(' ')}`)

  return execFileSync(command, argv, { cwd: REPO_ROOT, stdio: 'inherit', shell: process.platform === 'win32' })
}

function exportRequirements() {
  // `--extra all` mirrors the release build: the desktop shell drives every
  // surface, so every optional dependency has to be resolvable offline.
  run('uv', [
    'export',
    '--format',
    'requirements-txt',
    '--extra',
    'all',
    '--no-emit-project',
    '-o',
    REQUIREMENTS
  ])

  say(`requirements.txt written (${Math.round(fs.statSync(REQUIREMENTS).size / 1024)} KB)`)
}

function downloadWheels() {
  fs.mkdirSync(WHEELHOUSE, { recursive: true })

  try {
    run('python', [
      '-m',
      'pip',
      'download',
      '--require-hashes',
      '--only-binary=:all:',
      '--python-version',
      PYTHON_VERSION,
      '-r',
      REQUIREMENTS,
      '-d',
      WHEELHOUSE
    ])
  } catch {
    // Same tolerance as CI: an incomplete platform keeps the PyPI fallback at
    // install time rather than breaking the build that produces the package.
    say('WARNING: wheel download did not complete; the install will resolve from PyPI')
  }

  downloadBuildBackend()
}

/**
 * The build backend is NOT part of the exported requirements.
 *
 * `uv export --no-emit-project` drops the project itself, and with it the
 * [build-system] requires — so the wheelhouse used to arrive without
 * setuptools/wheel. install.ps1 finishes with
 * `uv pip install --no-deps --no-build-isolation -e .`, which then has no
 * backend to call: it fails, and because that failure only downgrades the
 * "plobi.exe exists" nicety, the backend kept running the *previous*
 * version's files while every version string in the UI said the new one.
 * Measured 2026-11-12: install tree stamped 0.3.1, venv site-packages held a
 * physical plobi-agent-0.3.0 copy, and no .pth/editable finder existed.
 *
 * Downloaded without --require-hashes (these pins live in pyproject.toml, not
 * in the hashed requirements file) and through execFileSync without a shell,
 * so a pin like `setuptools>=77.0,<83` cannot be eaten by cmd.exe as `<`
 * redirection.
 */
function downloadBuildBackend() {
  const requires = readBuildRequires()
  if (requires.length === 0) {
    say('WARNING: no [build-system] requires found; editable install will need a network')
    return
  }
  const argv = [
    '-m', 'pip', 'download',
    '--only-binary=:all:',
    '--python-version', PYTHON_VERSION,
    ...requires,
    '-d', WHEELHOUSE,
  ]
  say(`$ python ${argv.join(' ')}`)
  try {
    execFileSync('python', argv, { cwd: REPO_ROOT, stdio: 'inherit' })
  } catch {
    say('WARNING: build backend download failed; editable install will need a network')
  }
}

/** Read the `requires = [...]` list out of the root pyproject.toml [build-system]. */
function readBuildRequires() {
  try {
    const text = fs.readFileSync(path.join(REPO_ROOT, 'pyproject.toml'), 'utf8')
    const block = text.match(/\[build-system\]([\s\S]*?)(\n\[|$)/)
    if (!block) return []
    const list = block[1].match(/requires\s*=\s*\[([\s\S]*?)\]/)
    if (!list) return []
    return [...list[1].matchAll(/["']([^"']+)["']/g)].map(m => m[1])
  } catch {
    return []
  }
}

function main() {
  if (CHECK_ONLY) {
    const problems = bundleState()

    if (problems.length > 0) {
      say(`bundle incomplete: ${problems.join('; ')}`)
      process.exit(1)
    }

    say(`bundle ready: ${wheels().length} wheels, ${megabyte(wheelhouseBytes())}`)

    return
  }

  const hash = inputsHash()
  const stamp = readStamp()
  const current = stamp?.hash === hash && bundleState().length === 0

  if (current && !FORCE) {
    say(`wheelhouse is current (${wheels().length} wheels, ${megabyte(wheelhouseBytes())}) -- skip`)

    return
  }

  fs.mkdirSync(BUILD_DIR, { recursive: true })
  exportRequirements()

  // Stale wheels beside fresh ones are worse than no wheels: `--find-links`
  // would happily install last version's build of a dependency.
  if (fs.existsSync(WHEELHOUSE) && stamp?.hash !== hash) {
    say('lockfile changed -- clearing the old wheelhouse first')
    fs.rmSync(WHEELHOUSE, { recursive: true, force: true })
  }
  if (FORCE && fs.existsSync(WHEELHOUSE)) {
    fs.rmSync(WHEELHOUSE, { recursive: true, force: true })
  }

  downloadWheels()

  const problems = bundleState()
  fs.writeFileSync(
    STAMP,
    JSON.stringify({ hash, wheels: wheels().length, bytes: wheelhouseBytes(), at: new Date().toISOString() }, null, 2)
  )

  if (problems.length > 0) {
    say(`WARNING: ${problems.join('; ')} -- first launch will resolve from PyPI`)
  } else {
    say(`wheelhouse ready: ${wheels().length} wheels, ${megabyte(wheelhouseBytes())}`)
  }
}

main()
