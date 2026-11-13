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


def _tsc() -> str:
    """仓库根那把 tsc（web 工程的类型检查用它）。"""
    if os.name == "nt":
        return str(ROOT / "node_modules" / ".bin" / "tsc.cmd")
    return str(ROOT / "node_modules" / ".bin" / "tsc")


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
            # 概念退役那两道牙（WP-PROFILE-CANCEL 第六轮＋11-06 收尾）：命令行那句说明书
            # 不许再教「profile」，人读面与两个建家入口必须关着。原来它们只在常规套件里
            # 跑（跑套件就咬），但 pre-commit 是**最后一道**——今天这一笔不该是绕过牙的那
            # 一步（2026-11-06 用户令接手这刀时补上；那格原属 WP-REPO-MAP，见登记册那一行）。
            "tests/plobi_cli/test_cli_help_says_agent_not_profile.py",
            "tests/plobi/test_identity_surfaces_readable.py",
            "-q",
            # basetemp 换档（11-13 主人授权接手这一行）：旧的 `.pytest-run` 那档的 ACL 坏了 ——
            # 它「存在」却 stat 不到，pytest 对这种目录只会 `mkdir` → `FileExistsError` ⇒ 这道门
            # 必红 ⇒ pre-commit 挡住**全仓所有** Code 提交（不止这一刀）。换成一档干净的新目录：
            # 名字带 `.pytest-` 前缀，`.gitignore` 第 175 行 `.pytest-*/` 已经收着，不进仓。
            # 那一档坏掉的原因记在这儿，别当随手改：它是 11-13 一次 `rm --` 清理尝试改坏的，
            # 只有管理员权限能修（见 Docs 登记册 WP-TURTLE-LOADING 那一格）。
            "--basetemp=.pytest-arch-gate",
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

    # 编译产物 .js 躲在源码旁边时，vitest 读的是上一版 —— 症状是"刚导出的函数不存在"
    # 或整页白屏，而 `git status` 干净（.gitignore 整类忽略它们）。10-13 一天两次，
    # 所以这道闸门先看容器再看测试：影子在，就直接红，并把清理命令写给人。
    shadow = _run(
        [py, "scripts/check-windows-footguns.py", "--assert-no-js-shadows"],
        ROOT,
    )
    if shadow != 0:
        print(
            "\nArchitecture gate FAILED (js shadow): clean the build products first — "
            "`cd apps/desktop && npx tsc -b . --clean` — then re-run. "
            "Do not 'fix' the code or the test that the stale .js is contradicting."
        )
        return shadow

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

    # web 仪表盘（另一个前端工程，在 `web/`）：它的**类型真源**是 `web/src/i18n/types.ts`，
    # 16 份语言文件必须与它对得上——键名一改就对不上（11-06 出过一次事故：四份语言文件把
    # i18n 的键名也一起改了，西班牙语 `agentes`、土耳其语 `ajanes`…，而类型定义没跟着改
    # ⇒ CI 的 web 构建当场炸）。
    #
    # ⚠️ 跑的是**真那条**：`web/tsconfig.json` 是 solution 文件（`files: []`），所以
    # `tsc -p . --noEmit` **什么都查**（本地那正是它当时的假绿）；真检查在
    # `tsconfig.app.json` 里，那才是应用代码。
    wt = ROOT / "web"
    if not (ROOT / "node_modules" / ".bin" / ("tsc.cmd" if os.name == "nt" else "tsc")).is_file():
        print(
            "ERROR: web 工程的类型检查无法执行 —— 仓库根 node_modules/.bin/tsc 缺失。"
            "在仓库根跑 `npm ci`（workspace 依赖提升到根目录）。",
            file=sys.stderr,
        )
        return 1
    we = _run([_tsc(), "-p", "tsconfig.app.json", "--noEmit"], wt)
    if we != 0:
        print(
            "\nArchitecture gate FAILED (web typecheck). "
            "The locale files must match the `Translations` interface in web/src/i18n/types.ts "
            "— if you renamed a key, rename it in every locale AND in that interface."
        )
        return we

    print("Architecture gates passed (frontend + backend + web).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
