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
 *   - a bounded timeout (a hung interpreter beats forever, but we still
 *     give slow disks / cold caches / cold Python imports room to breathe)
 *   - stdio ignored (we only care about exit code; stdout/stderr are
 *     not surfaced to the user, just to recentPlobiLog for forensics
 *     via the caller's catch block if it chooses)
 *   - any throw -> false (never propagate -- resolver wants a boolean)
 *
 * TIMEOUT IS NOT "BROKEN" (2026-11-06):
 *   The resolver reads "probe did not pass" as "this candidate is a
 *   broken/partial venv, fall through to bootstrap". That conflated two
 *   very different verdicts. A venv whose `pip install` died is *broken*:
 *   the interpreter exits non-zero within seconds. A venv that is merely
 *   *slow* (a scanned/loaded disk, a cold interpreter, a busy machine)
 *   trips our deadline -- but the interpreter was alive and working, and
 *   the fallback for "broken" is a full reinstall bootstrap, which on a
 *   Windows box costs 20+ minutes of uv/python/git/node stages.
 *   Measured on the reference box: idle probe 0.3s; under 92 parallel
 *   pytest processes the bare interpreter start took 14-21s and the full
 *   import probe 60-80s. With the old 15s deadline a HEALTHY install
 *   was reclassified as broken on every launch and the app fell into an
 *   endless bootstrap loop (the marker path re-probes too, so it never
 *   escaped). We now separate the verdicts: only a non-zero exit or a
 *   missing interpreter means "broken"; a deadline means "slow but
 *   alive", which we trust instead of reinstalling.
 *
 * Kept in a standalone ts module so it can be unit-tested with
 * `node --test` without dragging in the electron runtime (same pattern
 * as bootstrap-platform.ts and hardening.ts).
 */

import { execFileSync } from 'node:child_process'

/**
 * Deadline for a single probe run.
 *
 * Sized against two measured costs on the reference Windows box:
 *   - a BROKEN venv exits non-zero at roughly the interpreter's start
 *     cost (0.3s idle, 14-21s while the machine is loaded) -- it has to
 *     report INSIDE the budget, or we would trust a venv that cannot
 *     import plobi_cli and hand the user a dead-on-arrival backend;
 *   - a HEALTHY but slow venv costs 60-80s there, so it will always
 *     trip the deadline -- which is fine now that "slow" no longer
 *     means "broken" (see the TIMEOUT IS NOT "BROKEN" note above).
 *
 * 30s clears the failure cost with margin while capping the wait on a
 * loaded machine at half of what the old 60s idea would have cost.
 */
const PROBE_TIMEOUT_MS = 30000

/**
 * What a probe run actually told us.
 *
 *   'pass'    -- the interpreter exited 0
 *   'fail'    -- the interpreter exited non-zero, or isn't there at all
 *   'timeout' -- WE killed it at our own deadline; the interpreter had
 *                started and was still working
 */
type ProbeOutcome = 'pass' | 'fail' | 'timeout'

/**
 * Run `<command> <args>` and classify the result. Never throws.
 *
 * @param {string} command
 * @param {string[]} args
 * @param {object} [opts]
 * @param {string} [opts.cwd]
 * @param {object} [opts.env]
 * @param {number} [opts.timeoutMs] - Override the deadline (tests).
 * @param {boolean} [opts.shell]
 * @returns {ProbeOutcome}
 */
function runProbe(
  command: string,
  args: string[],
  opts: { cwd?: string; env?: Record<string, string>; timeoutMs?: number; shell?: boolean } = {}
): ProbeOutcome {
  if (!command) {
    return 'fail'
  }

  try {
    execFileSync(command, args, {
      cwd: opts.cwd || undefined,
      env: opts.env ? { ...process.env, ...opts.env } : process.env,
      stdio: 'ignore',
      timeout: opts.timeoutMs || PROBE_TIMEOUT_MS,
      shell: Boolean(opts.shell),
      windowsHide: true
    })

    return 'pass'
  } catch (error) {
    // ETIMEDOUT / SIGTERM => our deadline killed a process that had
    // already started. ENOENT => nothing to run. Anything else => the
    // interpreter ran and exited non-zero.
    const code = (error as NodeJS.ErrnoException)?.code
    const signal = (error as { signal?: string })?.signal

    if (code === 'ETIMEDOUT' || signal === 'SIGTERM') {
      return 'timeout'
    }

    return 'fail'
  }
}

/**
 * Memo for the Plobi import probe.
 *
 * The resolver asks the SAME question twice per boot (the bootstrap
 * marker path and the venv-on-disk path both probe the interpreter with
 * the same PYTHONPATH). On a loaded machine each probe costs up to the
 * full deadline, so we remember the verdict.
 *
 * Only verdicts that mean "use it" are cached. A 'fail' is NOT cached:
 * the Retry / "Repair install" flow re-probes on purpose, because the
 * venv may have been repaired since.
 */
const _plobiImportProbeCache = new Map<string, ProbeOutcome>()

function probeCacheKey(
  pythonPath: string,
  code: string,
  opts: { cwd?: string; env?: Record<string, string>; timeoutMs?: number }
): string {
  return [pythonPath, code, opts.cwd || '', opts.env?.PYTHONPATH || '', opts.timeoutMs || 0].join(' ')
}

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
 * Shared import-probe body: run `<python> -c <code>` and classify the
 * outcome. Never throws -- the resolver treats an exception the same as a
 * non-zero exit ("skip this rung, try the next one").
 *
 * @param {string} pythonPath - Absolute path to a python.exe / python.
 * @param {string} code - Import snippet to execute.
 * @param {object} [opts]
 * @param {string} [opts.cwd] - Working directory for the probe.
 * @param {object} [opts.env] - Additional environment for the probe.
 * @param {number} [opts.timeoutMs] - Override the deadline.
 * @returns {ProbeOutcome}
 */
function importProbeOutcome(
  pythonPath: string,
  code: string,
  opts: { cwd?: string; env?: Record<string, string>; timeoutMs?: number } = {}
): ProbeOutcome {
  if (!pythonPath || !code) {
    return 'fail'
  }

  return runProbe(pythonPath, ['-c', code], opts)
}

/**
 * How a Plobi probe verdict is read.
 *
 * 'timeout' is deliberately TRUSTED here. The fallback for "this venv is
 * broken" is a full reinstall bootstrap -- 20+ minutes of uv / python /
 * git / node stages on Windows -- so misreading a slow-but-healthy
 * interpreter as broken is far more expensive than starting a slow
 * backend. A genuinely broken venv exits NON-ZERO within seconds and is
 * still rejected.
 *
 * @param {ProbeOutcome} outcome
 * @returns {boolean}
 */
function plobiProbeVerdict(outcome: ProbeOutcome): boolean {
  return outcome === 'pass' || outcome === 'timeout'
}

/**
 * How an aigw probe verdict is read -- deliberately the OPPOSITE policy.
 *
 * This rung's fallback is an explainable `deferred` row (the gateway has
 * its own health poll), not a reinstall. So a timeout here means "don't
 * know" and we decline the candidate rather than spawn an interpreter
 * that may be hung.
 *
 * @param {ProbeOutcome} outcome
 * @returns {boolean}
 */
function aigwProbeVerdict(outcome: ProbeOutcome): boolean {
  return outcome === 'pass'
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
function canImportPlobiCli(
  pythonPath: string,
  opts: { cwd?: string; env?: Record<string, string>; timeoutMs?: number } = {}
) {
  if (!pythonPath) {
    return false
  }

  const code = plobiRuntimeImportProbe()
  const key = probeCacheKey(pythonPath, code, opts)
  const cached = _plobiImportProbeCache.get(key)

  if (cached) {
    // Only 'pass' and 'timeout' are ever cached, and both mean "use it".
    return true
  }

  const outcome = importProbeOutcome(pythonPath, code, opts)

  if (outcome !== 'fail') {
    _plobiImportProbeCache.set(key, outcome)
  }

  // 'timeout' means "slow but alive": the interpreter started and was still
  // working when our deadline hit. Trusting it is the right call here --
  // the alternative reading ("broken") sends the app into a full reinstall
  // bootstrap, which costs 20+ minutes, for an interpreter that was fine.
  return outcome === 'pass' || outcome === 'timeout'
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
  return 'import quota_hub.cli'
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
function canImportAigwCli(
  pythonPath: string,
  opts: { cwd?: string; env?: Record<string, string>; timeoutMs?: number } = {}
) {
  if (!pythonPath) {
    return false
  }

  return aigwProbeVerdict(importProbeOutcome(pythonPath, aigwRuntimeImportProbe(), opts))
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
function verifyPlobiCli(
  plobiCommand: string,
  opts?: { shell?: boolean; timeoutMs?: number }
) {
  if (!plobiCommand) {
    return false
  }

  // Same policy as the Plobi import probe: this rung's fallback is the
  // bootstrap installer, so "slow" must not be read as "broken".
  return plobiProbeVerdict(
    runProbe(plobiCommand, ['--version'], {
      timeoutMs: opts?.timeoutMs,
      shell: Boolean(opts?.shell)
    })
  )
}


export {
  aigwRuntimeImportProbe,
  aigwProbeVerdict,
  canImportAigwCli,
  canImportPlobiCli,
  plobiProbeVerdict,
  plobiRuntimeImportProbe,
  PROBE_TIMEOUT_MS,
  runProbe,
  verifyPlobiCli
}

export type { ProbeOutcome }
