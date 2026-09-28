#!/usr/bin/env python3
"""Copy scripts/git-hooks/pre-commit into .git/hooks/pre-commit.

Does not change git config. Safe to re-run. Existing hook is overwritten
with the chained docs + architecture gate hook (docs check is still called).
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "scripts" / "git-hooks" / "pre-commit"


def main() -> int:
    git_dir = ROOT / ".git"
    if not git_dir.exists():
        print(f"No .git at {ROOT} — run this from the Code repo.", file=sys.stderr)
        return 1

    if git_dir.is_file():
        print("This repo uses a gitfile (worktree). Open the main Code checkout to install hooks.", file=sys.stderr)
        return 1

    dest = git_dir / "hooks" / "pre-commit"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SRC, dest)
    mode = dest.stat().st_mode
    dest.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"Installed {dest}")
    print("Commit will now run check-docs.sh + check_arch_gates.py")
    print("Emergency skip: SKIP_ARCH_GATES=1  (Agents must never use this)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
