#!/usr/bin/env python3
"""Bundle all four repositories of this workspace into ``bundles/keep/``.

Why four: the brain (``MIND_ROOT``) was ruled data (ruling 72) — it cannot be
regenerated from code — yet every backup tool here only ever covered the parts
that *can* be regenerated. A backup set that skips the irreplaceable repo is not
a backup set. The brain's location is read from ``MIND_ROOT`` only; nothing in
this file hardcodes a drive letter.

Exit is non-zero when any repo is missing or any bundle fails ``git bundle
verify`` — a bundle that does not verify is not a backup.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# Code/scripts/backup_repos.py -> Code -> Plobi-Desktop -> workspace root.
CODE = Path(__file__).resolve().parents[1]
DESKTOP = CODE.parent
WORKSPACE = DESKTOP.parent

MIN_ENV_VAR = "MIND_ROOT"


def repos() -> dict[str, Path | None]:
    mind = os.environ.get(MIN_ENV_VAR, "").strip()
    return {
        "code": CODE,
        "docs": DESKTOP / "Docs",
        "harmony": WORKSPACE / "Plobi-Harmony-App",
        # The brain has no fallback path on purpose: guessing where the repo happens
        # to sit is what let it hide inside the code checkout in the first place.
        "mind": Path(mind) if mind else None,
    }


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1800,
    )


def bundle_one(name: str, repo: Path | None, dest: Path, stamp: str) -> tuple[bool, str, str]:
    """Create and verify one bundle.

    Returns ``(ok, one-line status, cause)`` where ``cause`` is ``""`` on success
    and one of ``unset`` / ``missing-dir`` / ``no-git`` / ``create`` / ``verify``.
    The cause is not decoration: "the brain was not configured" and "the brain is
    configured but its directory is gone" need different fixes, and reporting them
    with one sentence is how a machine gets debugged by guessing (ruling 81).
    """
    if repo is None:
        return False, f"{name}: not configured ({MIN_ENV_VAR} is empty)", "unset"
    if not repo.is_dir():
        return False, f"{name}: directory missing at {repo}", "missing-dir"
    if not (repo / ".git").exists():
        return False, f"{name}: no .git at {repo}", "no-git"

    out = dest / f"plobi-{name}-{stamp}.bundle"
    created = git(repo, "bundle", "create", str(out), "--branches", "--tags")
    if created.returncode != 0 or not out.is_file():
        detail = (created.stderr or created.stdout or "").strip().splitlines()
        out.unlink(missing_ok=True)
        return False, f"{name}: bundle create failed -> {detail[-1] if detail else 'no message'}", "create"

    verified = git(repo, "bundle", "verify", str(out))
    if verified.returncode != 0:
        detail = (verified.stderr or verified.stdout or "").strip().splitlines()
        return False, f"{name}: bundle verify FAILED -> {detail[-1] if detail else 'no message'}", "verify"

    size_mb = out.stat().st_size / (1024 * 1024)
    tip = git(repo, "rev-parse", "--short", "HEAD")
    tip_txt = (tip.stdout or "").strip() if tip.returncode == 0 else "?"
    return True, f"{name}: {out.name} {size_mb:.1f} MB @ {tip_txt} (verified)", ""


def rotate(dest: Path, name: str, keep: int) -> list[str]:
    """Prune this script's own bundles for ``name`` down to the newest ``keep``.

    Naming carries the timestamp (``plobi-<name>-YYYYmmdd-HHMM.bundle``), so a
    lexicographic sort is a chronological one. Only that exact pattern is touched:
    ``keep/data/*.enc`` archives, ``docs-*.bundle`` from the off-disk script and
    ``vaelis-*.bundle`` from the dead machine all survive untouched.
    """
    if keep <= 0:
        return []
    ours = sorted(dest.glob(f"plobi-{name}-*.bundle"))
    removed = []
    for old in ours[:-keep] if len(ours) > keep else []:
        try:
            old.unlink()
            removed.append(old.name)
        except OSError as exc:
            print(f"rotate: could not prune {old.name}: {exc}", file=sys.stderr)
    return removed


def log_run(dest: Path, stamp: str, results: list[tuple[str, bool]], removed: list[str]) -> None:
    """Append one durable line per run, so a scheduled run that failed leaves evidence.

    The scheduled task's stdout goes nowhere; a backup nobody can prove ran is the
    failure this whole knife is about (ruling 81: 坏了要可见).
    """
    line = " ".join(f"{name}={'ok' if ok else 'FAIL'}" for name, ok in results)
    pruned = f" pruned={len(removed)}" if removed else ""
    try:
        with (dest / "backup-run.log").open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(f"{stamp} {line}{pruned}\n")
    except OSError as exc:
        print(f"FAIL cannot write backup-run.log: {exc}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dest", type=Path, default=DESKTOP / "bundles" / "keep",
                    help="bundle landing directory (default: Plobi-Desktop/bundles/keep)")
    ap.add_argument("--only", action="append", choices=sorted(repos()),
                    help="bundle just these repos (repeatable); default is all four")
    ap.add_argument("--keep", type=int, default=14,
                    help="how many newest bundles to keep per repo (0 disables pruning)")
    args = ap.parse_args(argv)

    if shutil.which("git") is None:
        print("FAIL git executable not found on PATH", file=sys.stderr)
        return 2

    stamp = time.strftime("%Y%m%d-%H%M")
    selected = repos()
    if args.only:
        selected = {k: v for k, v in selected.items() if k in args.only}

    args.dest.mkdir(parents=True, exist_ok=True)
    failures: list[tuple[str, str]] = []
    results: list[tuple[str, bool]] = []
    for name, repo in selected.items():
        ok, line, cause = bundle_one(name, repo, args.dest, stamp)
        print(("ok   " if ok else "FAIL ") + line)
        results.append((name, ok))
        if not ok:
            failures.append((cause, line))

    # Prune only what this run actually produced history for: a repo that failed
    # today must keep the older bundles it has.
    removed: list[str] = []
    for name, ok in results:
        if ok:
            removed += rotate(args.dest, name, args.keep)
    if removed:
        print(f"pruned {len(removed)} older bundle(s) (keep={args.keep}): "
              + ", ".join(removed))

    log_run(args.dest, stamp, results, removed)

    if failures:
        print("", file=sys.stderr)
        for _cause, line in failures:
            print(f"not backed up: {line}", file=sys.stderr)
        brain_cause = next((c for c, l in failures if l.startswith("mind:")), "")
        if brain_cause:
            why = {
                "unset": f"{MIN_ENV_VAR} is not set — this machine never registered where the brain lives",
                "missing-dir": f"{MIN_ENV_VAR} points at a directory that is not there",
                "no-git": f"the directory {MIN_ENV_VAR} points at is not a git repository",
            }.get(brain_cause, f"the brain could not be bundled ({brain_cause})")
            print(f"brain not in place: {why}. It is the one repo that cannot be rebuilt from code"
                  " (ruling 72) — fix the pointer, then re-run.", file=sys.stderr)
        return 1

    print(f"\nall {len(selected)} repos bundled and verified into {args.dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
