#!/usr/bin/env python3
"""Compare a pytest suite run against ``ci/known-test-failures.yml``.

Red means exactly two things:

* a **new** failing file appeared (it is not in the baseline), or
* the suite **did not complete** (no ``=== Summary:`` line, no recorded exit
  code, or a failure count that disagrees with the per-file list).

Anything else is reported, not failed. A gate that can never go green gets
ignored, and an ignored gate is how ``arch-gates`` stayed dead for weeks.

This used to be an inline heredoc in ``.github/workflows/test-suite.yml``. It
lives in a file now because the workflow runs the suite in **slices** (a single
35-minute job kept getting SIGTERM'd by the runner, and a killed job takes the
comparison step down with it — 2026-11-23, run 45089592119, exit code 143), and
a sliced comparison is subtle enough that it has to be testable off-CI:

* a baseline entry whose file lives in *another* slice must never be reported
  as "fixed" — that slice simply did not run it;
* a new failure is still a new failure no matter which slice found it.

Exit status: 0 = nothing to report, 1 = red (with a ``::error::`` explaining
which of the two reasons above applies).
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys

SUMMARY_RE = re.compile(
    r"===\s*Summary:\s*(\d+)\s+files?,\s*(\d+)\s+tests?\s+passed,\s*(\d+)\s+failed"
    r"(?:\s+\(\d+% complete\))?\s+in\s+([\d.]+)s"
)
# run_tests_parallel.py lists each failing file as
#   "  tests/foo.py (3 tests failed)"
# 路径分隔符要两种都认：CI 是 Linux（`tests/x.py`），本地跑是 Windows
# （`tests\x.py`）。只认正斜杠的话本地日志解析出 0 条，接着就会拿"汇总数 23"去撞
# "清单加起来 0" —— 报一句"比对结果不可信"，把真的失败埋掉。
FAILED_FILE_RE = re.compile(
    r"^\s{2,}(tests[\\/]\S+?\.py)\s+\((\d+)\s+tests?\s+failed\)", re.M
)


def _norm(path: str) -> str:
    """统一成仓库里用的正斜杠写法。"""
    return path.replace("\\", "/")


def _parse_slice(spec: str) -> tuple[int, int]:
    index_s, count_s = spec.split("/", 1)
    return int(index_s), int(count_s)


def _slice_file_set(slices_json: pathlib.Path, index: int) -> set[str]:
    """Files belonging to slice *index*, as ``--generate-slices`` emitted them.

    ``files`` is a single colon-joined string (the runner prints one JSON object
    on one line), so split it rather than assuming a list.
    """
    data = json.loads(slices_json.read_text(encoding="utf-8"))
    for entry in data.get("slice") or []:
        if int(entry.get("index", 0)) == index:
            raw = entry.get("files") or ""
            items = raw if isinstance(raw, list) else str(raw).split(":")
            return {_norm(str(item)) for item in items if item}
    raise ValueError(f"{slices_json} 里没有 slice {index} 这一片")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", default="/tmp/suite.log", type=pathlib.Path)
    parser.add_argument("--rc-file", default="/tmp/suite.rc", type=pathlib.Path)
    parser.add_argument(
        "--baseline", default="ci/known-test-failures.yml", type=pathlib.Path
    )
    parser.add_argument(
        "--slice",
        default="",
        help="本片序号，形如 1/4；留空表示这是整跑（没有分片）",
    )
    # 这两个的默认值必须是 None，不能是 ""：`type=pathlib.Path` 会把空串变成
    # `Path("")`，也就是当前目录 —— 一个 truthy 值，于是"没传这个参数"看起来像
    # "传了一个目录"，最后在写 verdict 时变成 PermissionError: '.'。
    parser.add_argument(
        "--slices-json",
        default=None,
        type=pathlib.Path,
        help="run_tests_parallel.py --generate-slices 的输出，分片时用来界定本片文件",
    )
    parser.add_argument("--verdict-out", default=None, type=pathlib.Path)
    args = parser.parse_args(argv)

    label = f"分片 {args.slice}" if args.slice else "整跑"

    def emit(text: str) -> None:
        # 只落文件：上面已经逐行 print 过，再 print 一遍会在日志里出现两份。
        if args.verdict_out is not None:
            args.verdict_out.write_text(text, encoding="utf-8")

    if not args.log.exists():
        print(
            f"::error::找不到 {args.log} —— Run suite 步骤没有产出日志，"
            "套件未运行或日志写入失败"
        )
        emit(f"# 测试套件（{label}）：无法判定\n\n- 缺少 `{args.log}`\n")
        return 1

    text = args.log.read_text(encoding="utf-8", errors="replace")

    if not args.rc_file.exists():
        print(
            f"::error::找不到 {args.rc_file} —— Run suite 步骤未记录退出码"
            "（步骤被中断？）。套件未完成，不能算通过"
        )
        emit(f"# 测试套件（{label}）：无法判定\n\n- 缺少 `{args.rc_file}`\n")
        return 1

    rc_raw = args.rc_file.read_text(encoding="utf-8").strip()
    rc = int(rc_raw or "1")

    summary = SUMMARY_RE.search(text)
    failed: dict[str, int] = {}
    for match in FAILED_FILE_RE.finditer(text):
        # 同一文件不该在日志里出现两次；真出现就取大值，别静默相加。
        key = _norm(match.group(1))
        failed[key] = max(failed.get(key, 0), int(match.group(2)))

    import yaml  # 只在 CI 环境里需要；本地测逻辑时也走同一条路

    base = yaml.safe_load(args.baseline.read_text(encoding="utf-8")) or {}
    known = {_norm(entry["path"]): entry for entry in base.get("known_failures") or []}

    if not summary:
        # 143 / 137 是**被信号杀掉**，不是测试红：runner 回收、内存压力、
        # 或者有人手滑取消。把这句写出来，否则下次又要从头查一遍「为什么没结论」。
        hint = ""
        if rc in (137, 143):
            hint = (
                f"（退出码 {rc} = 进程被信号终止：runner 回收 / OOM / 取消，"
                "不是测试失败。分片后单片时长应远低于这一步的窗口）"
            )
        print(
            "::error::没能从输出里解析出 Summary 行 —— 套件没跑完或被中断，"
            f"退出码 {rc}。{hint}这种情况不能当成「通过」。"
        )
        emit(
            f"# 测试套件（{label}）：无法判定\n\n- 退出码 `{rc}`\n- 未找到 Summary 行{hint}\n"
        )
        return 1

    files, passed, failures, secs = summary.groups()
    files_n, passed_n, failures_n, secs_f = (
        int(files),
        int(passed),
        int(failures),
        float(secs),
    )

    slice_files: set[str] | None = None
    if args.slice:
        if args.slices_json is None:
            print("::error::分片模式必须同时给 --slices-json（否则无法界定本片文件）")
            return 1
        index, count = _parse_slice(args.slice)
        slice_files = _slice_file_set(args.slices_json, index)
        if not slice_files:
            print(f"::error::slice {index}/{count} 里一个文件都没有，分片没生效")
            return 1

    new = sorted(set(failed) - set(known))
    same = sorted(set(failed) & set(known))
    scope = set(known) & slice_files if slice_files is not None else set(known)
    fixed = sorted(scope - set(failed))

    print(
        f"套件（{label}）：{files_n} 文件 / {passed_n} 通过 / {failures_n} 失败 / {secs_f / 60:.1f} 分钟"
    )
    print(f"基线内 {len(same)} · 新增 {len(new)} · 已修好 {len(fixed)}")
    for path in new:
        print(f"::warning title=新增失败::{path}（{failed[path]} 个测试）")
    for path in fixed:
        print(
            f"::notice title=基线里的已消失::{path} —— 请把它从 "
            "ci/known-test-failures.yml 删掉，别让基线虚胖"
        )

    out = [
        f"# 测试套件（{label}）\n",
        f"- 文件 **{files_n}**，通过 **{passed_n}**，失败 **{failures_n}**，"
        f"用时 **{secs_f / 60:.1f} 分钟**",
        f"- 与基线一致 **{len(same)}** · **新增 {len(new)}** · 已修好 {len(fixed)}\n",
    ]
    for title, items in (
        ("新增失败", new),
        ("基线内仍失败", same),
        ("已修好（记得清理基线）", fixed),
    ):
        if not items:
            continue
        out.append(f"## {title}")
        for path in items:
            count = failed.get(path, known.get(path, {}).get("tests", "?"))
            note = known.get(path, {}).get("note", "")
            line = f"- `{path}` ({count} 个测试)"
            if note:
                line += f" — {note}"
            out.append(line)
        out.append("")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(out))

    emit("\n".join(out))

    if new:
        print(f"::error::有 {len(new)} 个新增失败文件，本次改动破坏了行为")
        return 1
    if failures_n != sum(failed.values()):
        print(
            "::error::汇总的失败数与逐文件清单不一致"
            f"（Summary 说 {failures_n}，清单加起来 {sum(failed.values())}），"
            "比对结果不可信"
        )
        return 1
    print("没有新增失败。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
