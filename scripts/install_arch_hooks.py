#!/usr/bin/env python3
"""Copy scripts/git-hooks/* into .git/hooks/.

Two hooks, two different jobs:
  * ``pre-commit`` — docs discipline + architecture gates + 认领范围兜底（认不出前缀只判硬冲突）；
  * ``commit-msg`` — 从本笔消息文件读 ``[WP-XXX]`` 注入 ``WP_COMMIT``，越界即拦（裁定 80）。

Does not change git config. Safe to re-run. Existing hooks are overwritten.
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK_DIR = ROOT / "scripts" / "git-hooks"
HOOKS = ("pre-commit", "commit-msg")


def main() -> int:
    git_dir = ROOT / ".git"
    if not git_dir.exists():
        print(f"No .git at {ROOT} — run this from the Code repo.", file=sys.stderr)
        return 1

    if git_dir.is_file():
        print("This repo uses a gitfile (worktree). Open the main Code checkout to install hooks.", file=sys.stderr)
        return 1

    dest_dir = git_dir / "hooks"
    dest_dir.mkdir(parents=True, exist_ok=True)
    for name in HOOKS:
        src = HOOK_DIR / name
        if not src.is_file():
            print(f"Missing hook source: {src}", file=sys.stderr)
            return 1
        dest = dest_dir / name
        shutil.copy2(src, dest)
        mode = dest.stat().st_mode
        dest.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        print(f"Installed {dest}")
    print("Commit will now run check-docs.sh + check_arch_gates.py, then commit-msg 归属核对")
    print("Emergency skip: SKIP_ARCH_GATES=1  (Agents must never use this)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
