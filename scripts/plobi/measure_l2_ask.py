"""Measure the L1→L2 question leg: does an L2 finish *inside* the ceiling `plobi_agent_ask` gives it?

Why this exists (`WP-L2-ROUTE-TOOLS`, 裁定 145 的必派清单要靠这条腿): 立案时说的是"那条腿不带 tools、所以撞 180 秒"。
10-16 现核把两件事分开了 —— 那条腿**带 tools**（一个 L2 回合里真跑出 24 次工具调用、rc=0），
而它**跑完要 194.9 秒**，`timeout_seconds()` 现读 180.0 ⇒ 工具在答案落地前 15 秒把回合杀了。
所以"撞 180s"是真的，"因为不带 tools"是假的。判据要能事后核，故留这支表：

    # 只读，不花钱：打印目标、cwd、argv、当前预算与上限
    .venv/Scripts/python.exe scripts/plobi/measure_l2_ask.py --plan

    # 真跑一次（会花额度：整整一个 L2 回合），打印耗时 / rc / 工具计数 / 与预算的差
    .venv/Scripts/python.exe scripts/plobi/measure_l2_ask.py --run [--profile l2-course-chmodern]

默认只 --plan：花钱那一支必须显式 --run，别让下一次复跑的人以为这是免费探针。
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

_QUESTION = "这个项目现在什么情况？请用你的工具真去看一眼（读你自己的项目笔记，或查课表/作业表），不要凭印象回答。"


def _load_agent_ask():
    """被量对象自己就是这条腿，所以用它自己的 resolve/compose/build/timeout，不另写一份。"""
    spec = importlib.util.spec_from_file_location(
        "agent_ask", str(REPO / "plugins" / "plobi-north-star" / "agent_ask.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tool_counts(db: Path) -> list[tuple]:
    import sqlite3

    with sqlite3.connect(str(db), timeout=2.0) as conn:
        return conn.execute(
            "select tool_name, count(*) from messages where session_id="
            "(select id from sessions order by rowid desc limit 1) and tool_name is not null "
            "group by tool_name order by 2 desc"
        ).fetchall()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true", help="只打印会跑什么（不花钱）")
    mode.add_argument("--run", action="store_true", help="真跑一个 L2 回合（会花额度）")
    parser.add_argument("--profile", default="", help="点名某个分身；默认取第一个可问的")
    args = parser.parse_args(argv)

    aa = _load_agent_ask()
    entries = aa.askable_entries()
    if not entries:
        print("没有可问的分身：必派清单连对象都没有")
        return 2
    entry = next((e for e in entries if getattr(e, "profile_name", "") == args.profile), None)
    if args.profile and entry is None:
        print(f"「{args.profile}」不在可问名册里。可问的：{[aa._label(e) for e in entries][:12]}")
        return 2
    entry = entry or next((e for e in entries if getattr(e, "category", "") == "courses"), entries[0])

    ceiling = aa.timeout_seconds()
    prompt = aa.compose_question(_QUESTION, entry=entry, asker="measure_l2_ask")
    child_argv, env, cwd = aa.build_child_command(entry, prompt, asked_by="measure")
    print(f"目标分身 : {aa._label(entry)}  profile={entry.profile_name}")
    print(f"它的项目目录 : {cwd or '（未绑定路径）'}")
    print(f"这条腿的预算 : timeout_seconds()={ceiling}s（config 旋钮，代码里硬夹上限）")
    print(f"子进程 argv : {' '.join(str(a) for a in child_argv[:7])} …")
    if args.plan:
        print("（--plan：什么都没跑，不花额度）")
        return 0

    t0 = time.monotonic()
    killed = False
    try:
        proc = subprocess.run(child_argv, env=env, cwd=cwd or None, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=ceiling + 120)
        rc, out = proc.returncode, proc.stdout or ""
    except subprocess.TimeoutExpired as exc:
        killed = True
        rc, out = None, (exc.stdout or "") if isinstance(exc.stdout, str) else ""
    dt = time.monotonic() - t0

    db = aa._child_state_db(entry)
    counts = _tool_counts(Path(str(db))) if db and os.path.exists(str(db)) else []
    verdict = "在预算内" if (not killed and dt <= ceiling) else ("超出预算——工具会在这一刻把回合杀掉" if not killed else f"连 +120s 的宽限都没跑完")
    print(f"耗时 : {dt:.1f}s   rc={rc}   stdout {len(out)} 字")
    print(f"它真用了的工具 : {dict(counts) or '一次都没用'}（合计 {sum(n for _, n in counts)} 次）")
    print(f"对照 : {dt:.1f}s vs 预算 {ceiling}s ⇒ {verdict}")
    print("读数口径：这条腿带不带 tools 由'工具计数'那一行判，不由回包措辞判；预算够不够由'对照'那一行判。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
