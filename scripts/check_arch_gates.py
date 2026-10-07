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


# 指令文件（AGENTS.md / CLAUDE.md）会整份进模型上下文，涨一份就等于每轮都贵一点。
# 做成棘轮而不是硬上限：已跟踪的只许缩不许涨，新建的从 300 行 / 20 KB 起算。
# 第一天就红的门和坏了的门看起来一模一样，所以压力要往后加，不挡今天的线。
NEW_INSTRUCTION_MAX_LINES = 300
NEW_INSTRUCTION_MAX_BYTES = 20_000
INSTRUCTION_GLOBS = ("*AGENTS.md", "*CLAUDE.md")


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(root), capture_output=True)


def _sizes(blob: bytes) -> tuple[int, int]:
    return len(blob), (blob.count(b"\n") + 1 if blob else 0)


def _instruction_budget_findings(root: Path) -> list[str]:
    """Return violations of the instruction-file size ratchet for `root`."""
    listed = _git(root, "ls-files", "-z", "--", *INSTRUCTION_GLOBS)
    if listed.returncode != 0:
        return [f"git ls-files 失败，棘轮无法执行：{listed.stderr.decode(errors='replace').strip()}"]
    tracked = [p.decode(errors="replace") for p in listed.stdout.split(b"\0") if p]

    added = _git(root, "diff", "--cached", "--name-only", "--diff-filter=A", "--", *INSTRUCTION_GLOBS)
    new_files = {p for p in added.stdout.decode(errors="replace").splitlines() if p}

    findings: list[str] = []
    for rel in tracked:
        # Compare the *index* blob, not the working tree: that is what the commit
        # will contain. On Windows `core.autocrlf` stores LF while the working tree
        # is CRLF, so judging the working tree would false-red on an untouched file.
        idx = _git(root, "show", f":{rel}")
        if idx.returncode != 0:
            continue  # removed from the index — shrinking is always allowed
        new = idx.stdout
        head = _git(root, "show", f"HEAD:{rel}")
        if head.returncode != 0:
            if rel in new_files:
                nb, nl = _sizes(new)
                if nl > NEW_INSTRUCTION_MAX_LINES or nb > NEW_INSTRUCTION_MAX_BYTES:
                    findings.append(
                        f"{rel}: 新建指令文件 {nl} 行 / {nb:,} B，超过上限 "
                        f"{NEW_INSTRUCTION_MAX_LINES} 行 / {NEW_INSTRUCTION_MAX_BYTES:,} B"
                    )
            continue  # no HEAD blob to shrink against
        old = head.stdout
        ob, ol = _sizes(old)
        nb, nl = _sizes(new)
        if nb > ob or nl > ol:
            findings.append(
                f"{rel}: 指令文件只许缩不许涨（{ol} 行 / {ob:,} B → {nl} 行 / {nb:,} B）。"
                f"要加内容先在这里删掉等量篇幅，或把它写成按需检索的 spec 而不是指令。"
            )
    return findings


def main() -> int:
    if os.environ.get("SKIP_ARCH_GATES", "").strip() in {"1", "true", "yes"}:
        print("SKIP_ARCH_GATES set — architecture gates skipped (do not use this as an Agent).")
        return 0

    py = _python()

    budget = _instruction_budget_findings(ROOT)
    if budget:
        print("Instruction budget gate FAILED — 指令文件体积棘轮：")
        for line in budget:
            print("  -", line)
        return 1

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

    # 本仓是 npm workspace 提升布局：依赖装在仓库根的 node_modules/，不在
    # apps/desktop/node_modules/ 下。旧判据查的正是后者里的 .package-lock.json，
    # 正常安装下永远不成立 —— 前端闸门被静默跳过，末尾却照样打印
    # "frontend + backend passed"。缺依赖是 RED 不是黄灯：没跑过的闸门
    # 不能声称自己跑过了（R-053）。
    if not (ROOT / "node_modules" / "vitest").is_dir():
        print(
            "ERROR: 前端架构闸门无法执行 —— 仓库根 node_modules/vitest 缺失。"
            "在仓库根跑 `npm ci`（workspace 依赖提升到根目录，不在 apps/desktop 下）。",
            file=sys.stderr,
        )
        return 1

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
