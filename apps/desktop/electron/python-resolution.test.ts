/**
 * Tests for electron/python-resolution.ts — R-051's ONE interpreter ladder.
 *
 * Run with: npm run test:desktop:platforms
 * (node --import tsx --test electron/*.test.ts)
 *
 * Everything here is a pure function with `exists` / `usable` / `platform` /
 * `env` injected. No Electron, no real filesystem, no subprocess.
 */

import assert from 'node:assert/strict'
import path from 'node:path'
import test from 'node:test'

import {
  DEFAULT_MAX_WALK_UP,
  describeInterpreterFailure,
  interpreterCandidates,
  interpreterRoots,
  resolveProjectInterpreter,
  VENV_DIR_NAMES,
  venvInterpreterCandidates
} from './python-resolution'

const POSIX = 'darwin' as NodeJS.Platform

/** Build an `exists` stub from an explicit list of paths that are on disk. */
function existsOnly(present: string[]) {
  const set = new Set(present)

  return (candidate: string) => set.has(candidate)
}

test('venvInterpreterCandidates: POSIX uses bin/, extensionless first', () => {
  const candidates = venvInterpreterCandidates('/repo/.venv', { platform: POSIX, pathModule: path.posix })

  assert.deepEqual(candidates, ['/repo/.venv/bin/python', '/repo/.venv/bin/python3'])
})

test('venvInterpreterCandidates: Windows uses Scripts/ and python.exe first', () => {
  // Extension-first on Windows: an extensionless `python` there can be a Git-Bash
  // shell script, which execFileSync cannot spawn the way we need.
  const candidates = venvInterpreterCandidates('C:\\repo\\.venv', { platform: 'win32', pathModule: path.win32 })

  assert.equal(candidates[0], 'C:\\repo\\.venv\\Scripts\\python.exe')
  assert.equal(candidates[1], 'C:\\repo\\.venv\\Scripts\\python')
})

test('interpreterRoots: walks up from the subsystem checkout, then appends extra roots', () => {
  const roots = interpreterRoots({
    extraRoots: ['/home/me/.plobi/plobi-agent'],
    maxWalkUp: 2,
    pathModule: path.posix,
    startDir: '/repo/aigw'
  })

  assert.deepEqual(roots, ['/repo/aigw', '/repo', '/', '/home/me/.plobi/plobi-agent'])
})

test('interpreterRoots: stops at the filesystem root instead of looping', () => {
  const roots = interpreterRoots({ maxWalkUp: 20, pathModule: path.posix, startDir: '/aigw' })

  assert.equal(roots[roots.length - 1], '/')
  assert.equal(roots.filter(root => root === '/').length, 1)
})

test('interpreterCandidates: the aigw checkout reaches the repo root venv (R-051)', () => {
  // This is the exact regression. `resolveAigwPython` used to probe only
  // <aigwDir>/.venv, which on this macOS checkout contains a Windows Scripts/
  // tree copied off the decommissioned box — so it fell through to
  // /usr/bin/python3 and the child died on `ModuleNotFoundError: httpx`.
  const candidates = interpreterCandidates({
    env: {},
    platform: POSIX,
    pathModule: path.posix,
    startDir: '/repo/aigw'
  })

  const repoVenv = path.posix.join('/repo', '.venv', 'bin', 'python')

  assert.ok(candidates.includes(repoVenv), `expected ${repoVenv} in the ladder`)
  assert.ok(candidates.indexOf(repoVenv) < candidates.length, 'repo-root venv must be reachable')
})

test('interpreterCandidates: $PLOBI_DESKTOP_PYTHON wins over every venv rung', () => {
  const candidates = interpreterCandidates({
    env: { PLOBI_DESKTOP_PYTHON: '/opt/custom/python' },
    platform: POSIX,
    pathModule: path.posix,
    startDir: '/repo'
  })

  assert.equal(candidates[0], '/opt/custom/python')
})

test('interpreterCandidates: .venv is probed before venv for every root', () => {
  // Both names are what the installers actually produce; the order must stay
  // stable or a stale `venv/` can shadow the live `.venv/`.
  assert.deepEqual(VENV_DIR_NAMES, ['.venv', 'venv'])

  const candidates = interpreterCandidates({
    env: {},
    maxWalkUp: 0,
    platform: POSIX,
    pathModule: path.posix,
    startDir: '/repo'
  })

  assert.deepEqual(candidates, [
    '/repo/.venv/bin/python',
    '/repo/.venv/bin/python3',
    '/repo/venv/bin/python',
    '/repo/venv/bin/python3'
  ])
})

test('resolveProjectInterpreter: returns the first candidate the probe accepts', () => {
  // The old `findPythonForRoot` returned the first candidate that merely EXISTED.
  // R-051 requires the caller's usability probe to gate every rung, so a venv
  // that is present but cannot import the subsystem must be skipped, not spawned.
  const broken = '/repo/.venv/bin/python'
  const working = '/repo/venv/bin/python'

  const resolution = resolveProjectInterpreter({
    env: {},
    exists: existsOnly([broken, working]),
    maxWalkUp: 0,
    platform: POSIX,
    startDir: '/repo',
    usable: (candidate: string) => candidate === working
  })

  assert.equal(resolution.python, working)
  assert.equal(resolution.reason, null)
})

test('resolveProjectInterpreter: never returns an interpreter that does not exist', () => {
  const present = ['/repo/.venv/bin/python']

  const resolution = resolveProjectInterpreter({
    env: {},
    exists: existsOnly(present),
    maxWalkUp: 0,
    platform: POSIX,
    startDir: '/repo',
    usable: () => true
  })

  assert.ok(resolution.python)
  assert.ok(present.includes(resolution.python as string))
})

test('resolveProjectInterpreter: a system Python is NOT a fallback rung', () => {
  // There is no PATH lookup in this ladder at all. `/usr/bin/python3` exists on
  // every macOS box and imports nothing — returning it was the bug.
  const resolution = resolveProjectInterpreter({
    env: {},
    exists: () => false,
    platform: POSIX,
    startDir: '/repo/aigw',
    usable: () => true
  })

  assert.equal(resolution.python, null)
  assert.ok(!String(resolution.python).includes('python3'))
})

test('resolveProjectInterpreter: failure carries a human-readable reason, not an argv', () => {
  const resolution = resolveProjectInterpreter({
    env: {},
    exists: () => false,
    label: 'the aigw gateway',
    platform: POSIX,
    startDir: '/repo/aigw',
    usable: () => true
  })

  assert.equal(resolution.python, null)
  assert.equal(typeof resolution.reason, 'string')
  assert.ok((resolution.reason ?? '').length > 20, 'reason must be displayable, not a sentinel')
  assert.match(resolution.reason ?? '', /uv sync/)
  assert.match(resolution.reason ?? '', /aigw gateway/)
})

test('describeInterpreterFailure: distinguishes "no venv" from "venv cannot import"', () => {
  const missing = describeInterpreterFailure({
    existingCandidates: [],
    label: 'the aigw gateway',
    probeFailed: false,
    roots: ['/repo']
  })

  const stale = describeInterpreterFailure({
    existingCandidates: ['/repo/.venv/bin/python'],
    label: 'the aigw gateway',
    probeFailed: true,
    roots: ['/repo']
  })

  // A copied-off-another-machine venv (Scripts/ on macOS) has no OS-correct
  // candidate to probe, so it must read as "create one", not "re-sync it".
  assert.match(missing, /No Plobi project Python/)
  assert.match(stale, /cannot import/)
  assert.notEqual(missing, stale)
})

test('describeInterpreterFailure: names the override when it was not usable', () => {
  const reason = describeInterpreterFailure({
    env: { PLOBI_DESKTOP_PYTHON: '/opt/broken/python' },
    existingCandidates: [],
    probeFailed: false,
    roots: ['/repo']
  })

  assert.match(reason, /PLOBI_DESKTOP_PYTHON=\/opt\/broken\/python/)
})

test('interpreterCandidates: default walk depth is shared by roots and candidates', () => {
  const roots = interpreterRoots({ maxWalkUp: DEFAULT_MAX_WALK_UP, pathModule: path.posix, startDir: '/a/b/c/d/e' })

  assert.equal(roots.length, DEFAULT_MAX_WALK_UP + 1)
  assert.ok(DEFAULT_MAX_WALK_UP >= 1, 'a subsystem checkout must be able to reach its repo root')
})
