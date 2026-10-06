#!/usr/bin/env python3
"""Windows 机测试基线核对（2026-10-06 立）。

为什么要有它：`Docs/ROADMAP.md` §3.5 指定唯一测试入口 `scripts/run_tests.sh`，而它给的通过/红数
基线是在 mac 量的（≈40092 通过 / 81 红）。这台 Windows 上 POSIX 那一类（chmod、worktree、
fcntl/termios、`/opt`、osascript/launchctl、符号链接特权）本来就成片红——**没有这台的红名单，
每个 Agent 都分不清「我改坏的」和「本来就红的」**，于是要么白修一天，要么把真回归当存量红放过去。

用法：
    scripts/run_tests.sh -q > <日志> 2>&1
    python scripts/plobi/check_test_baseline.py --from-log <日志>            # 比对：有新增红即 exit 1
    python scripts/plobi/check_test_baseline.py --from-log <日志> --write    # 重新登记基线（确认过红名单才对 Code 无害时）

基线文件 = 同目录的 `test_baseline_windows.json`，跟着代码走；口径正文见
`Docs/runbooks/environment.md`「这台 Windows 的测试基线」。
判据只到**文件级**（哪个 test 文件有红），不下到单条用例——用例级会天天变，文件级才是能长期钉的不变量。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BASELINE = os.path.join(HERE, "test_baseline_windows.json")
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))

_SUMMARY = re.compile(r"^  (tests[^\s]+\.py)\s+\((\d+) tests? failed\)", re.M)
_PROGRESS = re.compile(r"^\[[^\]]+\] ✗ (tests\\[^\s]+\.py) \((\d+)✓ (\d+)✗", re.M)
_NONZERO = re.compile(r"^  (tests[^\s]+\.py)\s+\((\d+) passed\)", re.M)


def parse_log(path: str) -> dict:
    text = open(path, encoding="utf-8", errors="replace").read()
    files = {m.group(1): int(m.group(2)) for m in _SUMMARY.finditer(text)}
    if not files:                                    # 汇总段缺失时退回进度行
        files = {m.group(1): int(m.group(3)) for m in _PROGRESS.finditer(text)}
        files = {k: v for k, v in files.items() if v}
    totals = re.search(r"100\.0% \| (\d+)/~?\d+ \| ✓(\d+) \| ✗\s*(\d+)\]", text)
    return {
        "files": dict(sorted(files.items(), key=lambda kv: (-kv[1], kv[0]))),
        "files_with_failures": len(files),
        "tests_failed": sum(files.values()),
        "tests_total": int(totals.group(1)) if totals else None,
        "tests_passed": int(totals.group(2)) if totals else None,
        "nonzero_but_all_passed": [m.group(1) for m in _NONZERO.finditer(text)],
    }


def code_head() -> str:
    try:
        return subprocess.run(["git", "-C", REPO_ROOT, "rev-parse", "--short=8", "HEAD"],
                              capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:
        return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-log", required=True, help="scripts/run_tests.sh 的完整输出")
    ap.add_argument("--write", action="store_true", help="把这次结果登记为新基线")
    args = ap.parse_args()

    run = parse_log(args.from_log)
    if not run["files"]:
        print("RED — 日志里解析不到任何失败文件，也没解析到进度行。先确认这是 run_tests.sh 的输出，"
              "别把「解析不出来」当成「全绿」。")
        return 1

    if args.write:
        payload = {"date": "2026-10-06", "code_head": code_head() or None,
                   "machine": "Windows (Cycle)", "entry": "scripts/run_tests.sh -q", **run}
        json.dump(payload, open(BASELINE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"基线已登记：{run['files_with_failures']} 个文件 / {run['tests_failed']} 条红 "
              f"（Code HEAD {payload['code_head']}）-> {os.path.relpath(BASELINE, REPO_ROOT)}")
        return 0

    if not os.path.isfile(BASELINE):
        print("RED — 还没有基线文件。先跑一次 `--write` 登记，别再让每个人自己猜这台的红数。")
        return 1

    base = json.load(open(BASELINE, encoding="utf-8"))
    known, now = set(base["files"]), set(run["files"])
    new = sorted(now - known)
    gone = sorted(known - now)
    print(f"基线 {base['files_with_failures']} 文件 / {base['tests_failed']} 条"
          f"（{base.get('date')} · Code HEAD {base.get('code_head')}）；"
          f"本次 {run['files_with_failures']} 文件 / {run['tests_failed']} 条")
    if gone:
        print(f"  变好了 {len(gone)} 个文件（基线该重登一次，别攒着）：")
        for f in gone[:20]:
            print(f"    - {f}")
    if new:
        print(f"  RED — 新增红 {len(new)} 个文件：")
        for f in new:
            print(f"    - {f}  ({run['files'][f]} 条)")
        print("  先证明不是你这刀带来的：`git stash` 之外用 `scripts/run_tests.sh <该文件> -q` "
              "在改动前后各跑一次。确属存量的再 --write 重登，别改判据。")
        return 1
    print("  GREEN — 没有新增红文件。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
