import fs from 'node:fs'
import path from 'node:path'

// ─────────────────────────────────────────────────────────────────────────────
// R-051 — the ONE interpreter ladder for every Python the desktop spawns.
//
// Before this, two resolvers disagreed with each other:
//   * `findPythonForRoot()`  (main backend) — `<root>/.venv`, `<root>/venv`,
//     then an UNGATED `findSystemPython()`.
//   * `resolveAigwPython()`  (aigw gateway)  — `<aigwDir>/.venv`, then the
//     bootstrap clone's venv, then the same ungated `findSystemPython()`.
//
// Both last rungs are the bug this module removes. On a macOS checkout the
// project interpreter is `<repoRoot>/.venv` (uv-managed, 3.13, httpx present),
// while PATH's `python3` is `/usr/bin/python3` — CommandLineTools 3.9 with
// nothing installed into it. `resolveAigwPython` never looked at the repo root
// (the aigw checkout has no `.venv/bin`, only a Windows `.venv/Scripts` copied
// off the decommissioned box), so it fell straight through to 3.9 and the
// child died on `ModuleNotFoundError: No module named 'httpx'`. That is a
// guaranteed-failure fallback, not a fallback at all.
//
// The rule encoded here: prefer the project virtualenv (which is what `uv sync`
// owns), walking up from the subsystem checkout so `<repoRoot>/.venv` is found
// from `<repoRoot>/aigw`; only ever hand back an interpreter that the caller's
// usability probe accepts; and when nothing qualifies, return a *reason* the UI
// can show instead of a doomed argv.
//
// Pure + dependency-injected (exists / usable / platform / env) so it is
// unit-testable with `node --test` without Electron, like backend-env.ts and
// bootstrap-platform.ts.
// ─────────────────────────────────────────────────────────────────────────────

/** venv directory names the Plobi installer and `uv sync` both produce. */
const VENV_DIR_NAMES = ['.venv', 'venv']

/** How many parents to walk when a subsystem checkout has no venv of its own. */
const DEFAULT_MAX_WALK_UP = 4

function pathModuleFor(platform: NodeJS.Platform = process.platform) {
  return platform === 'win32' ? path.win32 : path.posix
}

/**
 * Interpreter paths inside one venv root, per OS layout:
 *   POSIX:   <venv>/bin/python{,3}
 *   Windows: <venv>/Scripts/python{.exe,}
 *
 * The extension-first order matters on Windows: `python.exe` is the real
 * binary, an extensionless `python` there can be a Git-Bash shell script.
 */
function venvInterpreterCandidates(
  venvRoot: string,
  { platform = process.platform, pathModule = pathModuleFor(platform) }: any = {}
): string[] {
  if (!venvRoot) {
    return []
  }

  return platform === 'win32'
    ? [pathModule.join(venvRoot, 'Scripts', 'python.exe'), pathModule.join(venvRoot, 'Scripts', 'python')]
    : [pathModule.join(venvRoot, 'bin', 'python'), pathModule.join(venvRoot, 'bin', 'python3')]
}

/**
 * Candidate roots in priority order: the subsystem checkout itself, then its
 * parents up to `maxWalkUp` (so `<repoRoot>/aigw` reaches `<repoRoot>`), then
 * any explicitly supplied extra roots (e.g. `$PLOBI_HOME/plobi-agent`).
 */
function interpreterRoots({
  startDir,
  extraRoots = [],
  maxWalkUp = DEFAULT_MAX_WALK_UP,
  pathModule = pathModuleFor()
}: any = {}): string[] {
  const roots: string[] = []

  const push = (candidate: string) => {
    const value = String(candidate || '').trim()

    if (value && !roots.includes(value)) {
      roots.push(value)
    }
  }

  let cursor = startDir

  for (let depth = 0; depth <= maxWalkUp && cursor; depth += 1) {
    push(cursor)

    const parent = pathModule.dirname(cursor)

    if (parent === cursor) {
      break
    }

    cursor = parent
  }

  for (const extra of extraRoots) {
    push(extra)
  }

  return roots
}

/**
 * The full ordered candidate list (not existence-filtered) — kept separate from
 * `resolveProjectInterpreter` so a test can assert the ladder's shape without
 * touching the filesystem. `$PLOBI_DESKTOP_PYTHON` is the documented operator
 * override and wins over everything; no new env var is introduced here.
 */
function interpreterCandidates(input: any = {}): string[] {
  const {
    startDir,
    extraRoots = [],
    maxWalkUp = DEFAULT_MAX_WALK_UP,
    env = process.env,
    platform = process.platform,
    pathModule = pathModuleFor(platform)
  } = input

  const override = String(env?.PLOBI_DESKTOP_PYTHON || '').trim()
  const candidates: string[] = override ? [override] : []

  for (const root of interpreterRoots({ startDir, extraRoots, maxWalkUp, pathModule })) {
    for (const venvName of VENV_DIR_NAMES) {
      candidates.push(...venvInterpreterCandidates(pathModule.join(root, venvName), { platform, pathModule }))
    }
  }

  return candidates.filter((candidate, index) => candidate && candidates.indexOf(candidate) === index)
}

/**
 * Human-readable "why nothing could be run", shaped for a UI row rather than a
 * stack trace. Distinguishes the two failure modes so the advice is actionable:
 *   1. no virtualenv on disk at all   → create one (`uv sync` / bootstrap)
 *   2. a virtualenv exists but cannot import the subsystem → it is stale or
 *      half-installed; re-sync it.
 * A copied-off another-machine venv (Scripts/ on macOS) is case 1, because the
 * OS-correct layout simply has no candidates to probe.
 */
function describeInterpreterFailure({
  label = 'the Plobi runtime',
  roots = [],
  existingCandidates = [],
  env = process.env,
  probeFailed = false
}: any = {}): string {
  const override = String(env?.PLOBI_DESKTOP_PYTHON || '').trim()
  const looked = roots.length ? roots.join(', ') : 'the default locations'
  const overrideNote = override ? ` ($PLOBI_DESKTOP_PYTHON=${override} was not usable)` : ''

  if (probeFailed && existingCandidates.length > 0) {
    return (
      `Found a Python virtualenv at ${existingCandidates.join(', ')} but it cannot import ` +
      `${label} — it is stale or only half installed${overrideNote}. Re-create it with \`uv sync\` ` +
      `in ${roots[0] || looked}. The desktop will not run a system Python for this, because that ` +
      `interpreter has none of Plobi's dependencies.`
    )
  }

  return (
    `No Plobi project Python under ${looked}${overrideNote}. Expected ` +
    `.venv/bin/python (POSIX) or .venv/Scripts/python.exe (Windows). Create it with \`uv sync\` in ` +
    `${roots[0] || looked}, or run the first-launch bootstrap install. The desktop will not fall ` +
    `back to a system Python for ${label}: on macOS that is /usr/bin/python3, which has none of the ` +
    `dependencies and dies on the first import.`
  )
}

/**
 * Walk the ladder and return the first interpreter that both exists on disk and
 * the caller's `usable` probe accepts.
 *
 * @returns {{python: null | string, roots: string[], existingCandidates: string[], reason: null | string}}
 */
function resolveProjectInterpreter({
  startDir,
  extraRoots = [],
  maxWalkUp = DEFAULT_MAX_WALK_UP,
  env = process.env,
  platform = process.platform,
  exists = (value: string) => fs.existsSync(value),
  usable = () => true,
  label = 'the Plobi runtime'
}: any): { existingCandidates: string[]; python: null | string; reason: null | string; roots: string[] } {
  const pathModule = pathModuleFor(platform)
  const roots = interpreterRoots({ startDir, extraRoots, maxWalkUp, pathModule })
  const candidates = interpreterCandidates({ startDir, extraRoots, maxWalkUp, env, platform, pathModule })
  const existingCandidates = candidates.filter(candidate => exists(candidate))

  for (const candidate of existingCandidates) {
    if (usable(candidate)) {
      return { existingCandidates, python: candidate, reason: null, roots }
    }
  }

  return {
    existingCandidates,
    python: null,
    reason: describeInterpreterFailure({
      env,
      existingCandidates,
      label,
      probeFailed: existingCandidates.length > 0,
      roots
    }),
    roots
  }
}

export {
  DEFAULT_MAX_WALK_UP,
  describeInterpreterFailure,
  interpreterCandidates,
  interpreterRoots,
  resolveProjectInterpreter,
  VENV_DIR_NAMES,
  venvInterpreterCandidates
}
