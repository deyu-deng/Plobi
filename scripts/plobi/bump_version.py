#!/usr/bin/env python3
"""把发布版本号改到七处 ＋ 改发布日：一次跑完，跑完自己验一遍。

为什么要有这个：版本号散在七处（`release_readiness.py` 的 PLACES 是唯一那份表），手改
就会漂——「发出去的包装的是上一版、说明写的是这一版」这种事只有装到别人机器上才看得见。
**这一刀把版本号收成一处真源**：改号只跑这一个脚本，跑完由就绪闸自己复核；漂了当场红，
不留到发版那天。

**不用 `scripts/release.py --bump`**：那是上游 electron-builder 那套脚本的移植，只动
`package.json`、还自带一套 release notes 生成器，与我们的七处 ＋ 两份手写说明对不上
（用户 11-07 令：版本号走手工那几处；`--publish` 会与流水线的 `gh release create` 撞车）。
这里只做「改号」这一件事，别的都不碰。

用法：
    python scripts/plobi/bump_version.py 0.3.3 --date 2026-11-18
    python scripts/plobi/bump_version.py --check          # 只看现在一致不一致，不改

退出码：
    0  改完（或核完）且七处一致
    1  改不动（形状对不上／缺文件）或改完仍不一致
    2  用法错
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import date as _date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from release_readiness import (  # noqa: E402  （这一份是两张脚本共用的真源）
    PLACES,
    RELEASE_DATE_PATTERN,
    RELEASE_DATE_REL,
    ROOT,
    version_problems,
)


def _replace_once(path: Path, pattern: str, new_value: str, *, label: str) -> bool:
    """按正则改一处，**要求恰好命中一次**；命中零次或多次都当场报错。

    命中零次＝这一处的形状变了（脚本不该猜）；命中多次＝这一处有歧义（也不该猜）。
    """
    text = path.read_text(encoding="utf-8")
    matches = list(re.finditer(pattern, text, re.M))
    if len(matches) != 1:
        raise ValueError(f"{label}：期望恰好命中 1 处，实际 {len(matches)} 处")
    m = matches[0]
    old = m.group(1)
    if old == new_value:
        return False
    replacement = m.group(0)[: m.group(0).rfind(old)] + new_value + m.group(0)[m.group(0).rfind(old) + len(old):]
    path.write_text(text[: m.start()] + replacement + text[m.end():], encoding="utf-8")
    return True


def bump(root: Path, version: str, date: str) -> list[str]:
    """把七处版本号 ＋ 发布日改掉。返回改过的文件清单。"""
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError(f"版本号 {version!r} 不是 X.Y.Z")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise ValueError(f"发布日 {date!r} 不是 YYYY-MM-DD")

    touched: set[str] = set()
    for place in PLACES:
        path = root / place.rel
        if not path.is_file():
            raise ValueError(f"改不了：{place.rel} 不在")
        if _replace_once(path, place.pattern, version, label=place.label):
            touched.add(place.rel)

    if _replace_once(root / RELEASE_DATE_REL, RELEASE_DATE_PATTERN, date, label="__release_date__"):
        touched.add(RELEASE_DATE_REL)

    return sorted(touched)


def verify_lock(root: Path) -> list[str]:
    """`uv.lock` 与 `pyproject.toml` 对得上吗——对不上，锁文件就不是这个版本。"""
    from shutil import which

    if which("uv") is None:
        return ["这台机器上没有 uv，跳过 uv lock --check（锁文件那一格是手改的）"]
    proc = subprocess.run(
        ["uv", "lock", "--check"], cwd=str(root), capture_output=True, text=True, timeout=300
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        return [f"uv lock --check 没过：{detail[-1] if detail else '（无输出）'}"]
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="改发布版本号（七处）＋ 发布日")
    parser.add_argument("version", nargs="?", default="", help="新版本号 X.Y.Z")
    parser.add_argument("--date", default="", help="发布日 YYYY-MM-DD（默认：今天）")
    parser.add_argument("--check", action="store_true", help="只核，不改")
    args = parser.parse_args(argv)

    if args.check:
        problems = version_problems(ROOT)
        if problems:
            print(f"RED — {len(problems)} 处：", file=sys.stderr)
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
            return 1
        print("GREEN — 七处版本号一致")
        return 0

    if not args.version:
        print("用法：bump_version.py X.Y.Z [--date YYYY-MM-DD] | --check", file=sys.stderr)
        return 2

    date = args.date or _date.today().isoformat()

    try:
        touched = bump(ROOT, args.version, date)
    except ValueError as exc:
        print(f"RED — {exc}", file=sys.stderr)
        return 1

    print(f"已改到 {args.version}（发布日 {date}）：")
    for rel in touched:
        print(f"  - {rel}")

    problems = version_problems(ROOT)
    if problems:
        print(f"RED — 改完仍不一致，{len(problems)} 处：", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    for problem in verify_lock(ROOT):
        print(f"WARN — {problem}", file=sys.stderr)

    print("GREEN — 七处版本号一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
