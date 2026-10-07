#!/usr/bin/env python3
"""提交范围 = 认领范围 —— `Code` 侧的门（裁定 77；判据口径抄裁定 76 已上线的 Docs 半边）。

真源只有一个：`Docs/reference/agent-register.md` 的「认领的文件族」列。本仓不建第二份真源
（`Docs/README.md` §0 第 6 条：同一条事实只登记一处），所以这脚本往**上**找那份登记册。

判据分两档，与 Docs 版同一口径：
  * 认得出本笔的 ``[WP-XXX]`` 前缀（``WP_COMMIT`` 优先，其次 10 秒内新鲜的 ``COMMIT_EDITMSG``）
    → 暂存文件落在**别人**的在派认领族内即红；
  * 认不出前缀（编辑器提交 / 外部工具直接调 git）→ 只报「同一文件被两条在派线同时认领」这一种硬冲突。

登记册不在场（本仓被独立 clone）整段跳过并打印一行说明——那不是越界者，这扇门不许拦他。
红只留给确定错的事：一扇常红的门和一扇坏掉的门长得一样（R-053 那条血账）。

只读脚本：读一个小文件 + 一次 ``git diff --cached --name-only`` + 几行字符串比较。
任何路径都不写工作区、不动索引。

Emergency skip（与两道架构闸门同一套）：SKIP_ARCH_GATES=1 —— Agent 禁用。
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTER_RELPATH = Path("Docs") / "reference" / "agent-register.md"
# 「在派」以外都不算数：待派还没动手，待签已交出去，已闭/未登记更无从谈起认领。
STATUS_LIVE = "在派"
# 只有 10 秒内刚写过的 COMMIT_EDITMSG 才可信：`git commit`（开编辑器）时这文件是上一笔的残留，
# 拿它判越界会误报红——而一扇常红的门等于一扇坏了的门（本仓自己的口径）。
FRESH_MSG_SECONDS = 10.0
# 往上找几层登记册。`Code` 与 `Docs` 是同级（见工作区根 AGENTS.md），一层就够；
# 多探两层是给「仓被嵌得更深」留的余量，不是给「找不到也判越界」留的口子。
LOOKUP_LEVELS = 3

FAMILY_TOKEN = re.compile(r"`([^`]+)`")
WP_NAME = re.compile(r"\*\*(WP-[A-Z0-9-]+)\*\*")
WP_IN_TEXT = re.compile(r"(WP-[A-Z0-9-]+)")
WP_IN_SUBJECT = re.compile(r"\[(WP-[A-Z0-9-]+)\]")


def find_register(repo_root: Path) -> Path | None:
    """从本仓往上找 `Docs/reference/agent-register.md`；找不到返回 None（= 整段跳过）。"""
    for base in (repo_root, *list(repo_root.parents)[:LOOKUP_LEVELS]):
        candidate = base / REGISTER_RELPATH
        if candidate.is_file():
            return candidate
        if (base / REGISTER_RELPATH.parent).is_dir():
            # Docs/reference/ 在，但那页没了：同样是「登记册不在场」，别把报错当越界。
            break
    return None


def claim_families(reg_text: str) -> dict[str, list[str]]:
    """{WP 名: [族前缀...]} —— 只取「在派」的行。

    登记册里的族按"哪条线的仓"写（``Code/…`` 或 ``Docs/…``），本门只管 `Code` 仓：
    剥掉 ``Code/`` 前缀才等于 git 给出的仓内相对路径（**漏这一步就是假绿**——Docs 版第一版
    正是栽在这），``Docs/…`` 属于另一个仓，这里忽略。裸写法（如 ``package.json``）按仓内相对
    路径看待，与 Docs 版同一处理。
    """
    out: dict[str, list[str]] = {}
    for line in reg_text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 5 or not cells[0].startswith("**WP-"):
            continue
        if cells[4].replace("*", "").strip() != STATUS_LIVE:
            continue
        name = WP_NAME.match(cells[0])
        if not name:
            continue
        # 族列里混着中文说明（「的 dev 那一行」「配套测试」）与 `（…）` 注释，
        # 只取反引号里的 token，比按「、，,」切更准，也更不容易把一句白话认成文件族。
        keep: list[str] = []
        for token in FAMILY_TOKEN.findall(cells[3]):
            f = token.strip().strip("`").rstrip("/")
            if not f or f.startswith("（") or f.startswith("("):
                continue
            if f.startswith("Docs/"):
                continue
            if f.startswith("Code/"):
                f = f[len("Code/"):]
            if not ("/" in f or "." in f):
                continue  # 不像路径的 token 不参与比对
            keep.append(f)
        # 已知放过的一半：`plugins/x/{a.py,b.py}` 这种花括号写法这里不展开，判不出归属。
        # 宁松勿紧——误报一次就没人信这扇门了（与 Docs 版同一取舍）。
        out[name.group(1)] = keep
    return out


def covered(path: str, families: list[str]) -> bool:
    """暂存路径是否落在某个认领族内（族可以是文件、目录或 `dir/**`）——逻辑照 Docs 版。"""
    fl = path.replace("\\", "/")
    for a in families:
        a = a.replace("\\", "/")
        if a.endswith("/**"):
            if fl.startswith(a[:-3]):
                return True
        elif a.endswith("/"):
            if fl.startswith(a):
                return True
        elif fl == a or fl.startswith(a + "/"):
            return True
    return False


def commit_prefix(repo_root: Path, env: dict[str, str]) -> str | None:
    """本笔的 ``[WP-XXX]``：``WP_COMMIT`` 优先，其次 10 秒内新鲜的 COMMIT_EDITMSG。"""
    m = WP_IN_TEXT.search(env.get("WP_COMMIT", ""))
    if m:
        return m.group(1)
    msg = repo_root / ".git" / "COMMIT_EDITMSG"
    try:
        if msg.is_file() and time.time() - msg.stat().st_mtime < FRESH_MSG_SECONDS:
            found = WP_IN_SUBJECT.search(msg.read_text(encoding="utf-8", errors="replace"))
            if found:
                return found.group(1)
    except OSError:
        return None
    return None


def staged_files(repo_root: Path) -> list[str] | None:
    """暂存清单（只读）。拿不到（无 git / 非仓 / 抢锁失败）返回 None —— 判不了不判红。"""
    try:
        proc = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=str(repo_root),
            capture_output=True,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return [line for line in proc.stdout.decode(errors="replace").splitlines() if line.strip()]


def check(repo_root: Path, register: Path | None, env: dict[str, str]) -> tuple[list[str], str]:
    """返回 (违规列表, 一行核对说明)。违规非空 → 调用方退出 1。"""
    if register is None:
        return [], "登记册不在场，认领核对跳过（独立 clone 时这扇门不拦人）"

    fams_by_wp = claim_families(register.read_text(encoding="utf-8", errors="replace"))
    wp = commit_prefix(repo_root, env)
    staged = staged_files(repo_root)
    if staged is None:
        return [], "认领范围核对跳过：拿不到暂存清单（git 不可用），判不了不判红"

    violations: list[str] = []
    for f in staged:
        owners = [k for k, v in fams_by_wp.items() if covered(f, v)]
        if wp and owners and wp not in owners:
            violations.append(
                f"提交越界（裁定 77）：{f} 属于在派的 {'/'.join(sorted(owners))}，"
                f"而本笔前缀是 [{wp}] —— 撤手，或让认领方自己签"
            )
        elif not wp and len(owners) > 1:
            violations.append(
                f"同一文件被两条在派线同时认领：{f} → {'/'.join(sorted(owners))} —— 登记册必须先拆开"
            )

    note = (
        f"认领范围核对：staged {len(staged)} 个 / 在派认领方 {len(fams_by_wp)} 家"
        + (f" / 本笔 [{wp}]" if wp else " / 认不出前缀，只判硬冲突")
    )
    return violations, note


def main(argv: list[str] | None = None) -> int:
    if os.environ.get("SKIP_ARCH_GATES", "").strip() in {"1", "true", "yes"}:
        print("SKIP_ARCH_GATES set — 认领范围核对跳过（Agent 禁用这个）")
        return 0

    ap = argparse.ArgumentParser(description="提交范围 = 认领范围（裁定 77）")
    # 两个开关只为把测试接到临时仓 / 临时登记册上；钩子按默认值跑真实布局。
    ap.add_argument("--repo", default=str(ROOT), help="要核对的仓根（默认 = 本仓）")
    ap.add_argument("--register", default=None, help="登记册路径（默认 = 从仓根往上找）")
    args = ap.parse_args(argv)

    repo_root = Path(args.repo).resolve()
    register = Path(args.register).resolve() if args.register else find_register(repo_root)
    if register is not None and not register.is_file():
        register = None

    violations, note = check(repo_root, register, dict(os.environ))
    print(note)
    if violations:
        print(f"RED — 提交范围越出认领范围，{len(violations)} 处：", file=sys.stderr)
        for line in violations:
            print("  - " + line, file=sys.stderr)
        print(
            "\n真源是 Docs/reference/agent-register.md 的「认领的文件族」列：\n"
            "  要么别签这个文件，要么在自己那格里先把它登记成你的认领族（并与对方谈好串行）。\n"
            "  本脚本 = Code/scripts/check_claim_scope.py（裁定 77）",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
