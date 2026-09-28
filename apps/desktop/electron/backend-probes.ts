/**
 * backend-probes.ts
 *
 * Cheap "does this candidate backend actually work" checks used by
 * resolvePlobiBackend (main.ts). The resolver walks a ladder of
 * candidates -- bootstrap marker, `plobi` on PATH, system Python with
 * plobi_cli installed -- and historically returned the first candidate
 * whose binary existed on disk. That assumption breaks when a user has
 * a pre-installed Python 3.11-3.13 (so findSystemPython() returns a
 * path) but no plobi_cli in its site-packages: the resolver hands back
 * a backend the spawn step can't actually run, and the user gets a
 * dead-on-arrival "ModuleNotFoundError: No module named 'plobi_cli'"
 * instead of the first-launch installer.
 *
 * These probes give the resolver a way to verify a candidate before
 * trusting it. Failure (non-zero exit, exception, timeout) means "skip
 * this rung, try the next one"; success means "spawn this for real."
 * Falling off the bottom of the ladder lands on the bootstrap-needed
 * sentinel, which is exactly what we want when nothing pre-existing
 * actually works.
 *
 * Both probes are deliberately fast and forgiving:
 *   - 5s timeout (a hung interpreter beats forever, but we still give
 *     slow disks / cold caches room to breathe)
 *   - stdio ignored (we only care about exit code; stdout/stderr are
 *     not surfaced to the user, just to recentPlobiLog for forensics
 *     via the caller's catch block if it chooses)
 *   - any throw -> false (never propagate -- resolver wants a boolean)
 *
 * Kept in a standalone ts module so it can be unit-tested with
 * `node --test` without dragging in the electron runtime (same pattern
 * as bootstrap-platform.ts and hardening.ts).
 */

import { execFileSync } from 'node:child_process'

const PROBE_TIMEOUT_MS = 5000

/**
 * Return the Python snippet used to verify Plobi can import far enough to
 * launch the CLI. Kept exported for tests so dependency regressions are
 * caught without needing a real broken venv fixture.
 *
 * @returns {string}
 */
function plobiRuntimeImportProbe() {
  return 'import yaml; import dotenv; import plobi_cli.config'
}

/**
 * Shared import-probe body: run `<python> -c <code>` and report only whether it
 * exited 0. Never throws -- the resolver treats an exception the same as a
 * non-zero exit ("skip this rung, try the next one").
 *
 * @param {string} pythonPath - Absolute path to a python.exe / python.
 * @param {string} code - Import snippet to execute.
 * @param {object} [opts]
 * @param {string} [opts.cwd] - Working directory for the probe.
 * @param {object} [opts.env] - Additional environment for the probe.
 * @returns {boolean}
 */
function importProbePasses(
  pythonPath: string,
  code: string,
  opts: { cwd?: string; env?: Record<string, string> } = {}
) {
  if (!pythonPath || !code) {
    return false
  }

  try {
    execFileSync(pythonPath, ['-c', code], {
      cwd: opts.cwd || undefined,
      env: { ...process.env, ...(opts.env || {}) },
      stdio: 'ignore',
      timeout: PROBE_TIMEOUT_MS,
      windowsHide: true
    })

    return true
  } catch {
    return false
  }
}

/**
 * Return true iff the Plobi runtime import probe exits 0.
 *
 * Used to gate the "fallback to system Python with plobi_cli installed"
 * rung of resolvePlobiBackend. Without this, a system Python 3.11-3.13
 * registered in PEP 514 makes findSystemPython() succeed regardless of
 * whether plobi_cli has actually been pip-installed into its
 * site-packages -- and the resolver returns a backend that immediately
 * dies on spawn.
 *
 * The probe intentionally imports plobi_cli.config, not just the top-level
 * package: a broken/empty Windows launcher venv can still see the source tree
 * through PYTHONPATH but lack PyYAML, then die on the first real CLI import.
 *
 * @param {string} pythonPath - Absolute path to a python.exe / python.
 * @param {object} [opts.env] - Additional environment for the probe.
 * @returns {boolean}
 */
function canImportPlobiCli(pythonPath: string, opts: { cwd?: string; env?: Record<string, string> } = {}) {
  return importProbePasses(pythonPath, plobiRuntimeImportProbe(), opts)
}

/**
 * Python snippet that proves an interpreter can actually load the aigw
 * gateway, as opposed to merely existing. `aigw.cli` is the module that pulls
 * in the OAuth providers and therefore httpx -- the exact import chain that
 * raised `ModuleNotFoundError: No module named 'httpx'` on macOS when the
 * desktop resolved the CommandLineTools 3.9 interpreter (R-051). Probing
 * `<python> -m aigw --help` would be slower and would also parse config; a
 * plain import is enough to separate "has the dependencies" from "does not".
 *
 * @returns {string}
 */
function aigwRuntimeImportProbe() {
  return 'import aigw.cli'
}

/**
 * Return true iff `pythonPath` can import aigw's CLI module, run with *cwd*
 * pointing at the aigw checkout so `-m aigw` / `import aigw` resolve the local
 * package exactly the way the gateway spawn does.
 *
 * Used by R-051's aigw interpreter resolution: a candidate venv that fails
 * this probe is skipped instead of spawned, turning a 20-second health-poll
 * timeout into an immediate, explainable `deferred` row.
 *
 * @param {string} pythonPath - Absolute path to a python binary.
 * @param {object} [opts]
 * @param {string} [opts.cwd] - Directory to probe from (the aigw checkout).
 * @param {object} [opts.env] - Extra environment for the probe.
 * @returns {boolean}
 */
function canImportAigwCli(pythonPath: string, opts: { cwd?: string; env?: Record<string, string> } = {}) {
  if (!pythonPath) {
    return false
  }

  try {
    execFileSync(pythonPath, ['-c', aigwRuntimeImportProbe()], {
      cwd: opts.cwd || undefined,
      env: { ...process.env, ...(opts.env || {}) },
      stdio: 'ignore',
      timeout: PROBE_TIMEOUT_MS,
      windowsHide: true
    })

    return true
  } catch {
    return false
  }
}

/**
 * Return true iff `<plobiCommand> --version` exits 0.
 *
 * Used to gate the "existing `plobi` on PATH" rung. Without this, a
 * stale plobi.cmd shim left behind by an uninstalled pip install (or
 * a half-built venv whose `plobi` entry-point points at a deleted
 * Python) survives findOnPath() and gets selected as the backend.
 *
 * We intentionally avoid invoking the command with the dashboard args
 * here -- `--version` is the cheapest "is this binary alive" smoke
 * test that every plobi_cli entry-point has supported since 0.1.
 *
 * @param {string} plobiCommand - Resolved absolute path to a plobi
 *   executable (or an interpreter+script wrapper).
 * @param {boolean} [opts.shell] - Whether to run through a shell. For
 *   .cmd/.bat shims on Windows execFileSync needs shell:true to find
 *   the cmd interpreter; mirrors the same flag isCommandScript() drives
 *   in resolvePlobiBackend.
 * @returns {boolean}
 */
function verifyPlobiCli(plobiCommand: string, opts?: { shell?: boolean }) {
  if (!plobiCommand) {
    return false
  }

  try {
    execFileSync(plobiCommand, ['--version'], {
      stdio: 'ignore',
      timeout: PROBE_TIMEOUT_MS,
      shell: Boolean(opts?.shell),
      windowsHide: true
    })

    return true
  } catch {
    return false
  }
}

export {
  aigwRuntimeImportProbe,
  canImportAigwCli,
  canImportPlobiCli,
  plobiRuntimeImportProbe,
  PROBE_TIMEOUT_MS,
  verifyPlobiCli
}
