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
import datetime
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
#: Any per-file progress line, red or green.  Without this a fully green run looked
#: identical to "I could not parse this log" — the tool reported RED for success.
_PROGRESS_ANY = re.compile(r"^\[[^\]]+\] [✓✗] tests[\\/]", re.M)
_NONZERO = re.compile(r"^  (tests[^\s]+\.py)\s+\((\d+) passed\)", re.M)


def parse_log(path: str) -> dict:
    text = open(path, encoding="utf-8", errors="replace").read()
    files = {m.group(1): int(m.group(2)) for m in _SUMMARY.finditer(text)}
    if not files:                                    # 汇总段缺失时退回进度行
        files = {m.group(1): int(m.group(3)) for m in _PROGRESS.finditer(text)}
        files = {k: v for k, v in files.items() if v}
    # The runner pads its progress columns for alignment ("100.0% |   92/~92 | ✓111"),
    # so every separator has to tolerate runs of spaces — a strict single space made
    # a real green log parse as "totals unknown".
    totals = re.search(
        r"100\.0%\s*\|\s*(\d+)/~?\d+\s*\|\s*✓(\d+)\s*\|\s*✗\s*(\d+)\]", text
    )
    return {
        "files": dict(sorted(files.items(), key=lambda kv: (-kv[1], kv[0]))),
        "files_with_failures": len(files),
        "files_seen": len(_PROGRESS_ANY.findall(text)),
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


def worktree_dirty(limit: int = 40) -> list:
    """工作树里未提交的档（M/A/D/??）。

    为什么基线必须连这个一起记：`code_head` 只说"哪个提交"，说不了"跑的时候树是干净的还是
    带着别人在飞的改动"。这台多线共写一棵树（10-20 现读起跑即 19 档未提交），
    拿脏树的红名单 `--write` 就等于**把别人未提交的代码算进 HEAD 的账**——
    正是裁定 174 让人「绿要看在带着什么跑的」那条，以及 `WP-WIN-TEST-REBASELINE` 判据③
    「做不到就点名，不许 `--write` 洗」针对的形状。记下来不拦人，但让下一个用它的人看得见。
    """
    try:
        r = subprocess.run(["git", "-C", REPO_ROOT, "status", "--porcelain"],
                           capture_output=True, text=True, errors="replace", timeout=30)
    except Exception:
        return ["<git status 没跑成>"]
    out = []
    for line in r.stdout.splitlines()[:limit]:
        mark, path = line[:2].strip(), line[3:].split(" -> ")[-1].strip().strip('"')
        out.append(f"{path}（{mark or '?'}）")
    if len(r.stdout.splitlines()) > limit:
        out.append(f"…共 {len(r.stdout.splitlines())} 档")
    return out


def commits_between(old: str, new: str) -> str:
    """`old..new` 落了几笔；数不出来就返回空串，不拿猜的数字糊人。"""
    if not old or not new or old == new:
        return ""
    try:
        r = subprocess.run(["git", "-C", REPO_ROOT, "rev-list", "--count", "%s..%s" % (old, new)],
                           capture_output=True, text=True, errors="replace", timeout=20)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-log", required=True, help="scripts/run_tests.sh 的完整输出")
    ap.add_argument("--write", action="store_true", help="把这次结果登记为新基线")
    ap.add_argument("--started-at", default="",
                    help="这批日志起跑那一刻的 Code HEAD。全量要跑几十分钟，中间别人会落提交，"
                         "登记那一刻的 HEAD **不是**这批红的测量对象（10-08 22:2x 实际踩过的坑）")
    args = ap.parse_args()

    run = parse_log(args.from_log)
    if not run["files"]:
        if not run["files_seen"]:
            print("RED — 日志里既没有失败段，也没有任何进度行。先确认这是 run_tests.sh 的输出，"
                  "别把「解析不出来」当成「全绿」。")
            return 1
        # A genuinely green batch: progress lines exist, no file failed.
        if args.write:
            print(f"拒绝 --write —— 这一批 {run['files_seen']} 个文件全绿。0 红既可能是真绿，"
                   "也可能只是你挑了一小批；拿这种日志重登基线会把这台真实存在的 162 档红抹成空账。"
                   "要重登请给安静窗口里的全量日志。")
            return 1
        print(f"GREEN — 本次 {run['files_seen']} 个文件 / {run.get('tests_passed')} 条全绿"
              f"（0 红，Code HEAD {code_head()}）；无新增红可判。")
        return 0

    if args.write:
        dirty = worktree_dirty()
        at_write = code_head()
        head = args.started_at or at_write
        payload = {"date": datetime.date.today().isoformat(), "code_head": head or None,
                   "code_head_at_write": at_write or None,
                   "worktree_dirty_at_write": dirty,
                   "machine": "Windows (Cycle)", "entry": "scripts/run_tests.sh -q", **run}
        json.dump(payload, open(BASELINE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"基线已登记：{run['files_with_failures']} 个文件 / {run['tests_failed']} 条红 "
              f"（Code HEAD {payload['code_head']}）-> {os.path.relpath(BASELINE, REPO_ROOT)}")
        if args.started_at and at_write and at_write != args.started_at:
            n = commits_between(args.started_at, at_write)
            print(f"  测量对象=起跑那一刻的 {args.started_at}"
                  + ("，登记时 HEAD 已经是 %s（中间落了 %s 笔）⇒ 那几笔的红**没被这批日志测过**，"
                     "别把这份基线当成对它们负责。" % (at_write, n or "?") if n else
                     "，登记时 HEAD 已经是 %s。" % at_write))
        if dirty:
            print(f"⚠️ 这份基线**取自工作树**，起跑时还有 {len(dirty)} 档未提交：" + "、".join(dirty[:8])
                  + ("…" if len(dirty) > 8 else "")
                  + "\n   ⇒ 那几档的**新增红会被算进这份基线里**，别把它当 HEAD 的账用；"
                    "要一份只记 HEAD 的基线，得在干净树（或那几档都签了之后）重跑一次。")
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
    if base.get("worktree_dirty_at_write"):
        # 基线自己承认是脏树取的：那"0 新增红"只证明"和当时那棵树一样"，不证明"HEAD 没问题"
        print(f"  ⚠️ 这份基线**取自工作树**（登记时 {len(base['worktree_dirty_at_write'])} 档未提交："
              + "、".join(base["worktree_dirty_at_write"][:6]) + ("…" if len(base["worktree_dirty_at_write"]) > 6 else "")
              + "）⇒ 拿它判出的「0 新增红」**不等于 HEAD 干净**，只等于「和那棵树一样」。"
                "判 HEAD 要在干净树重跑一次（这是 `WP-WIN-TEST-REBASELINE` 判据③ 的意思）。")
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
