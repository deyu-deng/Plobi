#!/usr/bin/env bash
# Canonical test runner for plobi-agent. Run this instead of calling
# `pytest` directly to guarantee your local run matches CI behavior.
#
# What this script enforces:
#   * Per-file isolation via scripts/run_tests_parallel.py — each test
#     file runs in its own freshly-spawned `python -m pytest <file>`
#     subprocess. No xdist, no shared workers, no module-level leakage
#     between files.
#   * TZ=UTC, LANG=C.UTF-8, PYTHONHASHSEED=0 (deterministic)
#   * Env vars blanked (conftest.py also does this, but this
#     is belt-and-suspenders for anyone running pytest outside our
#     conftest path — e.g. on a single file)
#   * Proper venv activation (probes .venv, venv, then ~/.plobi/...)
#
# Usage:
#   scripts/run_tests.sh                            # full suite
#   scripts/run_tests.sh -j 4                       # cap parallelism
#   scripts/run_tests.sh tests/agent/               # discover only here
#   scripts/run_tests.sh tests/agent/ tests/acp/    # multiple roots
#   scripts/run_tests.sh tests/foo.py               # single file
#   scripts/run_tests.sh tests/foo.py -q            # path + bare pytest flag
#   scripts/run_tests.sh tests/foo.py -v --tb=long  # bare flags "just work"
#   scripts/run_tests.sh -k 'pattern'               # value flags pass through too
#   scripts/run_tests.sh tests/foo.py -- --tb=long  # explicit '--' still works
#
# Bare pytest flags (anything starting with '-' that isn't one of this
# runner's own options: -j/--jobs, --paths, --slice, --file-timeout, etc.)
# are forwarded to each per-file pytest invocation automatically — no '--'
# separator required. The explicit '--' form still works and stacks with
# bare flags. Positional path arguments override the default discovery
# root (tests/). A path that was mistakenly placed after '--' is lifted
# back into the discovery roots (with a note) rather than silently running
# the whole suite.

set -euo pipefail

# ── Locate repo root ────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ── Activate venv ───────────────────────────────────────────────────────────
# Interpreter layout differs by platform: POSIX venvs expose bin/python,
# Windows venvs expose Scripts/python.exe. Probing only bin/activate made this
# script unusable under Git Bash on Windows (it bailed with "no virtualenv
# found" even when .venv existed), which in turn blocked every slice whose
# acceptance command is `scripts/run_tests.sh`.
VENV=""
PYTHON=""
for candidate in "$REPO_ROOT/.venv" "$REPO_ROOT/venv" "$HOME/.plobi/plobi-agent/venv"; do
  if [ -x "$candidate/bin/python" ]; then
    VENV="$candidate"
    PYTHON="$candidate/bin/python"
    break
  fi
  if [ -x "$candidate/Scripts/python.exe" ]; then
    VENV="$candidate"
    PYTHON="$candidate/Scripts/python.exe"
    break
  fi
done

if [ -z "$VENV" ]; then
  echo "error: no virtualenv found in $REPO_ROOT/.venv, $REPO_ROOT/venv or $HOME/.plobi/plobi-agent/venv" >&2
  echo "       (probed both bin/python and Scripts/python.exe in each)" >&2
  exit 1
fi


# ── Live-gateway plugin (computed before we drop env) ───────────────────────
EXTRA_PYTHONPATH=""
EXTRA_PYTEST_PLUGINS=""
if [ -f "$HOME/.plobi/pytest_live_guard.py" ]; then
  EXTRA_PYTHONPATH="$HOME/.plobi"
  EXTRA_PYTEST_PLUGINS="pytest_live_guard"
fi


# ── Run in hermetic env ──────────────────────────────────────────────────────
# env -i: start with empty environment, opt-in only what we need.
# No credential var can leak — you'd have to explicitly add it here.
#
# HOME is NOT passed through. AGENTS.md promises "HOME / ~/.plobi → Temp dir
# per test", and tests/conftest.py's _isolate_plobi_home honours that for code
# going through get_plobi_home(). It does not cover import-time module
# constants, the logging setup, or anything that reads Path.home() — a full
# suite run was observed creating a real ~/.plobi with config.yaml, logs,
# cron/jobs.json, state.db and an auth.json holding a live `gh` token (R-041).
# That both pollutes the developer's machine and makes "fails closed without
# credentials" tests depend on what a previous run left behind. Redirecting
# HOME here closes the hole for every path, whatever a module does at import.
SCRATCH_HOME="$(mktemp -d "${TMPDIR:-/tmp}/plobi-tests-home.XXXXXX")"
mkdir -p "$SCRATCH_HOME/.plobi"
cleanup_scratch_home() { rm -rf "$SCRATCH_HOME"; }
trap cleanup_scratch_home EXIT INT TERM

echo "▶ running per-file parallel test suite via run_tests_parallel.py"
echo "  (TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0; clean env; HOME=$SCRATCH_HOME)"

cd "$REPO_ROOT"

# MIND_ROOT is a location pointer, not a credential: a few tests reuse the
# brain's own gate script (Loom/scripts/verifier.py), and the vault may live
# outside the repo. Stripped, those tests skip and still report green.
env -i \
  PATH="$PATH" \
  HOME="$SCRATCH_HOME" \
  USERPROFILE="$SCRATCH_HOME" \
  PLOBI_HOME="$SCRATCH_HOME/.plobi" \
  TZ=UTC \
  LANG=C.UTF-8 \
  LC_ALL=C.UTF-8 \
  PYTHONHASHSEED=0 \
  PYTHONDONTWRITEBYTECODE=1 \
  ${MIND_ROOT:+MIND_ROOT="$MIND_ROOT"} \
  ${PLOBI_RUN_SLOW_PET_TESTS:+PLOBI_RUN_SLOW_PET_TESTS="$PLOBI_RUN_SLOW_PET_TESTS"} \
  ${EXTRA_PYTHONPATH:+PYTHONPATH="$EXTRA_PYTHONPATH"} \
  ${EXTRA_PYTEST_PLUGINS:+PYTEST_PLUGINS="$EXTRA_PYTEST_PLUGINS"} \
  "$PYTHON" "$SCRIPT_DIR/run_tests_parallel.py" "$@"
STATUS=$?
cleanup_scratch_home
trap - EXIT INT TERM
exit $STATUS
