#!/usr/bin/env bash
# Thin POSIX wrapper for scripts/backup_repos.py — the single implementation.
# Lives inside the Code repo on purpose: a backup tool that only exists on the
# disk it protects is the failure mode R-042 is about.
#
# Interpreter probing is not decoration: on Windows `python3` is often the
# Microsoft Store forwarder stub, which exits non-zero and would make this
# wrapper report success for a run that never happened (ruling 72.7).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

for cand in python3 python py; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys' >/dev/null 2>&1; then
    exec "$cand" "$HERE/backup_repos.py" "$@"
  fi
done

echo "no usable Python interpreter (tried python3 / python / py) — nothing was backed up" >&2
exit 127
