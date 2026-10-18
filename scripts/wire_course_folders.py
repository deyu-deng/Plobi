#!/usr/bin/env python3
"""把课程 Agent 接上 ``D:\\Cloud\\Courses`` 里的课程文件夹（WP-AGENT-CREATE-UPGRADE ③）。

用户 2026-10-16 原话：「而且这么多课程都没有配文件夹。文件夹就在 Cloud/Courses 下面
都有这个学期对应的。」

**默认 dry-run**：只报配对结果，不写任何东西。加 ``--apply`` 才落盘（写前留 .bak）。

匹配规则（**全等，不子串**——子串匹配正是 WP-COURSE-MATCH-ONE-SOURCE 要杀的串台形状，
这里不另造第二套）：
  1. 两边都剥学期前缀（``2026-2027秋冬-`` 这种，可多重）；
  2. 全角→半角（``（甲）``→``(甲)``、``Ⅱ``→``II``）；
  3. 剥 agent 名里的节次/年份尾巴（``大学物理实验2026周四345``→``大学物理实验``）；
  4. 归一后必须**完全相等**；多个候选时优先本学期、其次最新学期。

配不上的（本学期没有对应文件夹）**一条不动**，列清单——不猜。这是用户数据动作：
projects.yaml 写前留 .bak，profile 的 terminal.cwd 只在目录真实存在时才写。
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plobi.agents.registry import load_registry  # noqa: E402

DEFAULT_COURSES_ROOT = Path(r"D:\Cloud\Courses")


def _strip_semester_prefix(text: str) -> str:
    """剥掉『2026-2027秋冬-』这类学期前缀（可多重，如形势与政策那种双前缀）。"""
    out = text.strip()
    while True:
        nxt = re.sub(r"^\d{4}-\d{4}[^-]*-", "", out)
        if nxt == out:
            return out
        out = nxt


def _norm(text: str) -> str:
    """归一：剥学期前缀 + 全角转半角 + 罗马数字 + 剥节次尾巴 + 去空白。"""
    t = _strip_semester_prefix(text)
    t = t.replace("（", "(").replace("）", ")")
    t = t.replace("Ⅱ", "II").replace("Ⅰ", "I").replace("Ⅲ", "III")
    t = re.sub(r"\d{4}\s*周[一二三四五六日]+\s*\d*$", "", t)  # 大学物理实验2026周四345
    t = re.sub(r"\s+", "", t)
    return t


def _semester_of(folder_name: str) -> str:
    """文件夹名的学期标签（剥不掉的原文前缀）。"""
    m = re.match(r"^(\d{4}-\d{4}[^-]*-)", folder_name.strip())
    return m.group(1) if m else ""


def _current_semester_label(today: date | None = None) -> str:
    """本学期的学期标签（这台学校的口径：9 月–1 月秋冬、2–6 月春夏、7–8 月暑）。"""
    day = today or date.today()
    year = day.year if day.month >= 9 else day.year - 1
    if day.month in (2, 3, 4, 5, 6):
        term = "春"
    elif day.month in (7, 8):
        term = "暑"
    else:
        term = "秋冬"
    return f"{year}-{year + 1}{term}-"


def _semester_sort_key(label: str) -> tuple[int, str]:
    """学期新旧排序键：年份大的新；同年内 秋冬 > 春 > 暑 粗略按字面（够用）。"""
    m = re.match(r"^(\d{4})-", label)
    return (int(m.group(1)) if m else 0, label)


def match_courses(courses_root: Path, today: date | None = None) -> dict[str, list[Path]]:
    """Cloud/Courses 下按归一名分组的文件夹（只收目录）。"""
    if not courses_root.is_dir():
        return {}
    groups: dict[str, list[Path]] = {}
    for entry in sorted(courses_root.iterdir()):
        if entry.is_dir():
            groups.setdefault(_norm(entry.name), []).append(entry)
    return groups


def pick_folder(
    name: str, groups: dict[str, list[Path]], today: date | None = None
) -> Path | None:
    """给一门课挑文件夹：归一全等 + 本学期优先 + 其次最新学期。没有就 None（不猜）。"""
    candidates = groups.get(_norm(name)) or []
    if not candidates:
        return None
    current = _current_semester_label(today)
    for folder in candidates:
        if folder.name.startswith(current):
            return folder
    return max(candidates, key=lambda f: _semester_sort_key(_semester_of(f.name)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="落盘（默认 dry-run 不写）")
    parser.add_argument("--root", default=str(DEFAULT_COURSES_ROOT), help="课程文件夹根目录")
    args = parser.parse_args(argv)

    root = Path(args.root)
    registry = load_registry()
    courses = [e for e in registry.agents.values() if e.category == "courses" and not e.archived]
    groups = match_courses(root)
    current = _current_semester_label()

    print(f"课程 Agent {len(courses)} 个 · 文件夹组 {len(groups)} 组 · 本学期标签 {current!r}")
    if not groups:
        print(f"读不到课程文件夹根：{root}——什么都不做。")
        return 1

    matched: list[tuple[str, Path]] = []
    unmatched: list[str] = []
    for entry in sorted(courses, key=lambda e: e.name):
        folder = pick_folder(entry.name, groups)
        if folder is None:
            unmatched.append(entry.name)
            print(f"  未配上  {entry.name}")
        else:
            matched.append((entry.name, folder))
            print(f"  配上    {entry.name} → {folder.name}")

    if not args.apply:
        print("\n(dry-run——加 --apply 才落盘；projects.yaml 写前会留 .bak)")
        return 0

    if not matched:
        print("\n没有可接的——不写。")
        return 0

    # 用户数据：写前留底（同目录 .bak，带时刻）。
    reg_path = registry.path
    backup = reg_path.with_suffix(f".{date.today().isoformat()}.bak")
    shutil.copy2(reg_path, backup)
    print(f"\n备份 → {backup}")

    from dataclasses import replace

    for name, folder in matched:
        entry = registry.agents[name]
        registry.agents[name] = replace(
            entry, project_path=str(folder), project_paths=(str(folder),)
        )
        # 接上名字册那一行就是生效的全部动作：分身的工作目录由它读时算
        # （``plobi.agents.registry.profile_config_overlay``），不再往分身家写 ``terminal.cwd``
        # （裁 166 / ADR-0013：那份文件从此只有一份）。
        print(f"  已接    {name} → {folder.name}")

    registry.save()
    print(f"\n已写 {reg_path}（{len(matched)} 条接上，{len(unmatched)} 条未动）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
