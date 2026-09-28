#!/usr/bin/env python3
"""Run ARCH-UI-MASTER §3.5 architecture gates. Exit 1 on failure.

Used by git pre-commit and CI. Does not need the full plobi-agent install —
the pytest file only inspects the filesystem.

Skip (last resort, never for Agents): SKIP_ARCH_GATES=1
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "apps" / "desktop"


def _python() -> str:
    win = ROOT / ".venv" / "Scripts" / "python.exe"
    posix = ROOT / ".venv" / "bin" / "python"
    if win.is_file():
        return str(win)
    if posix.is_file():
        return str(posix)
    return sys.executable


def _npm() -> str:
    if os.name == "nt":
        return shutil.which("npm.cmd") or shutil.which("npm") or "npm.cmd"
    return shutil.which("npm") or "npm"


def _run(cmd: list[str], cwd: Path) -> int:
    print("+", " ".join(cmd), f"(cwd={cwd})", flush=True)
    return subprocess.call(cmd, cwd=str(cwd))


def main() -> int:
    if os.environ.get("SKIP_ARCH_GATES", "").strip() in {"1", "true", "yes"}:
        print("SKIP_ARCH_GATES set — architecture gates skipped (do not use this as an Agent).")
        return 0

    py = _python()
    be = _run(
        [
            py,
            "-m",
            "pytest",
            "tests/plobi/test_console_arch_guard.py",
            "-q",
            "--basetemp=.pytest-run",
        ],
        ROOT,
    )
    if be != 0:
        print("\nArchitecture gate FAILED (backend). Restore plobi/console to __init__.py + router.py only.")
        return be

    fe = 0
    if not (DESKTOP / "node_modules" / ".package-lock.json").is_file():
        print(
            "WARNING: apps/desktop/node_modules missing — skipping frontend arch gate. "
            "Run `npm ci` from the repo root to enable it.",
            file=sys.stderr,
        )
    else:
        fe = _run(
            [_npm(), "run", "test:arch"],
            DESKTOP,
        )
        if fe != 0:
            print(
                "\nArchitecture gate FAILED (frontend). "
                "Do not resurrect left-rail/right-rail/workbench or add a second ChatSurface."
            )
            return fe

    print("Architecture gates passed (frontend + backend).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
