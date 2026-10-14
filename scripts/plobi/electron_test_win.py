#!/usr/bin/env python3
"""这台 Windows 的桌面/electron 测试族补尺（WP-ELECTRON-TEST-BASELINE-WIN）。

为什么要有它（裁定 117 二、任务书 `Docs/tasks/electron-test-baseline-win-task.md`）：
Python 侧早就有 `scripts/plobi/check_test_baseline.py` 那把尺——**只认新增红文件**、
基线跟着代码走、落后太多就停下来提醒重登。`apps/desktop/electron/*.test.ts` 这一族没有，
所以桌面侧任何"0 红"的交账只能按"未验"记。本脚本补的就是这一句。

三条子命令（**没有 `--write`**：洗新增红这件事在这个仓里不被允许，Python 侧同规矩）：

    run              串行逐档跑 electron/*.test.ts，产出机器可读日志（默认写临时区）
    check            拿日志比基线，报「新增红」，并判基线是不是落后 HEAD 太多
    print-baseline   把一次 run 的读数打成基线 JSON 到 **stdout**（注册基线靠人工落纸 + 提交，
                     不许脚本就地改写已提交的基线）

形状为什么是"逐档跑"：`--test-reporter=tap` 一次性跑 42 档时，node 会把叶子用例平铺成
`ok N - 用例名`、**丢掉文件名**（实测 514 条里没有一条带 `electron/`），而判据要的是
「文件 → 用例名 → pass/fail」。逐档跑一遍约 90 秒，换来每条结果都有归属，并且天然串行
（裁定 104 补：并发档读数只作参考）。

落点一律走 `tempfile.gettempdir()`——本仓裁定 79：这台 Python 会把 `/tmp` 按当前盘解析，
写死 POSIX 路径会凭空造出 `D:\\tmp`。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DESKTOP = os.path.join(REPO, "apps", "desktop")
ELECTRON_DIR = os.path.join(DESKTOP, "electron")
BASELINE = os.path.join(REPO, "scripts", "plobi", "electron_test_baseline_win.json")
# 基线陈旧判定：提交数与年龄两个轴，任一越界就提醒（阈值取基线 JSON 里的字段，可改）
DEFAULT_STALE_AFTER_COMMITS = 80
DEFAULT_STALE_AFTER_DAYS = 14

_TAP_LEAF = re.compile(r"^(?P<kind>ok|not ok) (?P<n>\d+) - (?P<name>.*)$")
_TAP_COUNTS = re.compile(r"^# (?P<key>tests|pass|fail|cancelled|skipped|todo) (?P<val>\d+)$")


def _git(*args: str) -> str:
    out = subprocess.run(("git", "-C", REPO) + args, capture_output=True, text=True, encoding="utf-8")
    return out.stdout.strip()


def _test_files() -> list[str]:
    # 键一律用正斜杠：基线 JSON 要进仓、跨平台可比，`os.path.join` 会在 Windows 上打成
    # `electron\x.test.ts`，到 Linux 上就对不上号。
    return sorted("electron/" + f for f in os.listdir(ELECTRON_DIR) if f.endswith(".test.ts"))


def _parse_tap(text: str) -> tuple[dict[str, str], dict[str, int]]:
    """TAP → ({用例名: pass|fail|skip}, 汇总计数)。

    skip 的识别：node 把跳过的档打成 `ok N - name` 后跟一个 `---` 块，块里有 `skip:`。
    """
    cases: dict[str, str] = {}
    counts: dict[str, int] = {}
    lines = text.splitlines()
    for index, line in enumerate(lines):
        m = _TAP_COUNTS.match(line)
        if m:
            counts[m.group("key")] = int(m.group("val"))
            continue
        m = _TAP_LEAF.match(line)
        if not m:
            continue
        name = m.group("name").strip()
        status = "fail" if m.group("kind") == "not ok" else "pass"
        # 行尾指令也要认：这台有两档 symlink 用例打成 `ok N - name # SKIP ...（EPERM）`，
        # 只查 YAML 块会把它们读成 pass，基线就漏掉"这台的存量跳"（实测 19:3x 抓到）。
        directive = re.search(r"#\s*(SKIP|TODO)\b", name, re.I)
        if directive:
            name = name[: directive.start()].strip()
            status = "skip"
        # 往后看 YAML 块，够不到就只判前几行——块总在紧接的若干行里
        window = "\n".join(lines[index + 1: index + 12])
        if status == "pass" and re.search(r"^ +(skip|todo):", window, re.M):
            status = "skip"
        # 同名用例（同档内 it() 重名）不合并：后一条带 #2 后缀，红了就单条出声
        if name in cases:
            name = "%s (#%d)" % (name, sum(1 for k in cases if k.startswith(name)) + 1)
        cases[name] = status
    return cases, counts


def cmd_run(args: argparse.Namespace) -> int:
    files = _test_files()
    report: dict[str, object] = {
        "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "head": _git("rev-parse", "HEAD"),
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty_worktree": bool(_git("status", "--porcelain")),
        "command": "python scripts/plobi/electron_test_win.py run",
        "files": {},
    }
    totals = {"tests": 0, "pass": 0, "fail": 0, "skip": 0, "red_files": 0, "errored_files": 0}
    for rel in files:
        proc = subprocess.run(
            ("node", "--import", "tsx", "--test", "--test-reporter=tap", rel),
            cwd=DESKTOP,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        cases, counts = _parse_tap(proc.stdout or "")
        entry = {
            "exit_code": proc.returncode,
            "cases": cases,
            "counts": counts,
        }
        if not cases:
            # 一档一条用例都没解析出来 = 这档没跑起来（加载失败/超时），按红算并留原文尾巴
            entry["error_tail"] = (proc.stderr or proc.stdout or "").strip()[-400:]
            totals["errored_files"] += 1
        reds = [name for name, status in cases.items() if status == "fail"]
        if reds or proc.returncode != 0 or not cases:
            totals["red_files"] += 1
        totals["tests"] += len(cases)
        totals["pass"] += sum(1 for s in cases.values() if s == "pass")
        totals["fail"] += len(reds)
        totals["skip"] += sum(1 for s in cases.values() if s == "skip")
        report["files"][rel] = entry
        print("%-46s cases=%-4d fail=%-3d skip=%-2d rc=%d" % (
            rel, len(cases), len(reds),
            sum(1 for s in cases.values() if s == "skip"), proc.returncode), flush=True)
    report["totals"] = totals
    out = args.out or os.path.join(
        tempfile.gettempdir(), "plobi-electron-win-%s.json" % _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    )
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1, sort_keys=True)
    print("\n日志：%s" % out)
    print("读数：文件 %d，用例 %d，fail %d，skip %d，红档 %d，起不来的档 %d，HEAD %s，工树%s" % (
        len(files), totals["tests"], totals["fail"], totals["skip"], totals["red_files"],
        totals["errored_files"], str(totals["tests"] and report["head"])[:8],
        "脏（别人的在飞改动在场）" if report["dirty_worktree"] else "干净"))
    return 0 if totals["fail"] == 0 and totals["errored_files"] == 0 else 1


def _load_baseline(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _staleness(baseline: dict) -> list[str]:
    notes: list[str] = []
    base_head = str(baseline.get("sampled_head", ""))
    now_head = _git("rev-parse", "HEAD")
    if base_head and base_head != now_head:
        behind = _git("rev-list", "--count", "%s..HEAD" % base_head)
        if not behind.isdigit():
            notes.append("基线取样的 HEAD %s… 在本地历史里找不到（被 rebase/squash 过？）——按重登处理" % base_head[:8])
        else:
            limit = int(baseline.get("stale_after_commits", DEFAULT_STALE_AFTER_COMMITS))
            if int(behind) > limit:
                notes.append("基线落后 HEAD %s 笔（阈值 %d）——停下来重登，别拿旧账当新尺" % (behind, limit))
    sampled = str(baseline.get("sampled_at", ""))[:10]
    if sampled:
        try:
            age = (_dt.date.today() - _dt.date.fromisoformat(sampled)).days
            limit_days = int(baseline.get("stale_after_days", DEFAULT_STALE_AFTER_DAYS))
            if age > limit_days:
                notes.append("基线取样已 %d 天（阈值 %d）——重登" % (age, limit_days))
        except ValueError:
            notes.append("基线里的 sampled_at 不是 ISO 日期：%r" % sampled)
    else:
        notes.append("基线没有 sampled_at——日期一律现取，不许硬编（check_test_baseline.py:76 那颗雷）")
    return notes


def cmd_check(args: argparse.Namespace) -> int:
    with open(args.from_log, encoding="utf-8") as fh:
        report = json.load(fh)
    baseline = _load_baseline(args.baseline)
    known = baseline.get("files", {})
    new_reds: list[str] = []
    gone: list[str] = []
    for rel, entry in report.get("files", {}).items():
        base_entry = known.get(rel, {})
        base_fail = set(base_entry.get("fail_cases", []))
        now_fail = {name for name, status in entry.get("cases", {}).items() if status == "fail"}
        for name in sorted(now_fail - base_fail):
            new_reds.append("%s :: %s" % (rel, name))
        if not entry.get("cases") or entry.get("exit_code") != 0:
            if "（档起不来）" not in base_fail:
                new_reds.append("%s :: （档起不来或退出码非 0）" % rel)
        for name in sorted(base_fail - now_fail):
            gone.append("%s :: %s" % (rel, name))
    for rel in sorted(set(known) - set(report.get("files", {}))):
        gone.append("%s :: （这一档现在不存在了——被删名或改名的档）" % rel)

    print("日志 HEAD %s / 基线 HEAD %s" % (
        str(report.get("head", ""))[:8], str(baseline.get("sampled_head", ""))[:8]))
    print("新增红文件 %d 条；基线里已消失/改名 %d 条" % (
        len({line.split(" :: ")[0] for line in new_reds}), len(gone)))
    for line in new_reds:
        print("  新增红：%s" % line)
    for line in gone[:20]:
        print("  已不复现：%s" % line)
    for note in _staleness(baseline):
        print("  ⚠️ %s" % note)
    if new_reds:
        print("\n判据不过：这台只认新增红。")
        return 1
    print("\n判据过：新增红 0。")
    return 0


# 存量非 pass 用例的"机制一句话"（任务书判据：每条都要能被随机抽出来复现，不是含糊标签）。
MECHANISMS = {
    "electron/fs-read-dir.test.ts :: readDirForIpc marks a symlink to a directory as a directory":
        "建 symlink 需要特权：这台非开发者模式/非管理员时 CreateSymbolicLink 返回 EPERM，"
        "用例自己 `t.skip('symlink creation is not permitted on this platform')`。"
        "复现：`node --import tsx --test electron/fs-read-dir.test.ts` 看那条 `# SKIP ...（EPERM）`。",
    "electron/hardening.test.ts :: resolveReadableFileForIpc blocks symlinks whose realpath is sensitive":
        "同一机制（symlink 造不出来 ⇒ 前置条件不成立就跳，不是断言放宽）。"
        "复现命令同上，文件换 electron/hardening.test.ts。",
}

# 10-10 立案那三档存量红现在的状态（现核，别当"从来没有过"读）。
NOTE_AT_SAMPLING = (
    "立案时（10-10 20:0x，治理线自跑）这族是 474 用例 / 469✓ / 3✗，三条红是 "
    "`git-worktree-ops.test.ts` 两处断 8.3 短名 `C:\\Users\\TURTLE~1\\…` 与 `update-relaunch.test.ts` "
    "一处喂 POSIX 路径。10-14 现核：那三档**已不复现**——Code `1216ca01`"
    "（`[WP-DESKTOP-POOL-GHOST-PROFILE]`「修掉这颗新闸门查出来的三颗 Windows 红」）把它们修掉了，"
    "本基线因此是**空红单**，不是漏登。尺子的价值从今往后在'只认新增红'与'基线落后就停'两条上。"
)


def cmd_print_baseline(args: argparse.Namespace) -> int:
    with open(args.from_log, encoding="utf-8") as fh:
        report = json.load(fh)
    files: dict[str, object] = {}
    for rel, entry in sorted(report.get("files", {}).items()):
        cases = entry.get("cases", {})
        files[rel] = {
            "cases": len(cases),
            "fail_cases": sorted(n for n, s in cases.items() if s == "fail"),
            "skip_cases": sorted(n for n, s in cases.items() if s == "skip"),
            "ran": bool(cases),
            "exit_code": entry.get("exit_code"),
        }
    out = {
        "scope": "Code/apps/desktop/electron/*.test.ts（逐档串行 --test-reporter=tap）",
        "command": "python scripts/plobi/electron_test_win.py run && ... check --from-log <日志>",
        "sampled_head": report.get("head"),
        "sampled_at": report.get("generated_at"),
        "stale_after_commits": DEFAULT_STALE_AFTER_COMMITS,
        "stale_after_days": DEFAULT_STALE_AFTER_DAYS,
        "note_at_sampling": NOTE_AT_SAMPLING,
        "mechanisms": MECHANISMS,
        "totals": report.get("totals"),
        "files": files,
    }
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1, sort_keys=True)
    sys.stdout.write("\n")
    print("# 这份只打到 stdout：注册基线=人把它落进 scripts/plobi/electron_test_baseline_win.json 并提交，"
          "脚本自己不改已提交的基线", file=sys.stderr)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    run_p = sub.add_parser("run", help="串行逐档跑，产出机器可读日志")
    run_p.add_argument("--out", default=None, help="日志落点（默认临时区）")
    run_p.set_defaults(func=cmd_run)
    check_p = sub.add_parser("check", help="比基线，只认新增红")
    check_p.add_argument("--from-log", required=True)
    check_p.add_argument("--baseline", default=BASELINE)
    check_p.set_defaults(func=cmd_check)
    pb = sub.add_parser("print-baseline", help="把一次 run 打成基线 JSON 到 stdout")
    pb.add_argument("--from-log", required=True)
    pb.set_defaults(func=cmd_print_baseline)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
