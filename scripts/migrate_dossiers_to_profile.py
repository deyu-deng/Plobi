#!/usr/bin/env python
"""把旧档案（md）抽成结构化档案（``dossier.json``）。

用户 11-09：「Plan现在占的位置太长了，本质还是md，没有UI设计。」「以后我们不需要md
文件的形式了。」「**抽成结构化并把 md 删掉。**」

这一刀做的是「抽」的那一半：

* 扫人读面上的 ``Dossiers/<类>/<名>/``，找出还带 ``plan.md`` 的那些档案；
* 把 ``plan.md`` 抽成**目标／现状／阶段／注册表**，把 ``progress.md`` 的正文留成**流水**；
* 写一份 ``dossier.json``（原子写）。

它**不删 md**：删文件要走可恢复删除（``rm --``）并单独提交——这一刀只负责把内容搬到
新形状，搬完由人/会话核过再删旧文件（内容仍在人读面那个 git 仓的历史里）。

用法::

    python scripts/migrate_dossiers_to_profile.py --dry-run
    python scripts/migrate_dossiers_to_profile.py

跨家边界（用户 11-08 14:5x）：落点由 :func:`plobi.mind.paths.real_target` 解析成哪个家，
就只动哪个家；**绝不从别的家搬数据**。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plobi.mind.dossier_profile import (  # noqa: E402  (路径要先摆好)
    PROFILE_FILENAME,
    parse_plan_markdown,
    parse_progress_markdown,
    profile_path,
    write_profile,
)
from plobi.mind.paths import MindUnavailable, real_target  # noqa: E402

DOSSIERS_LOGICAL = "Dossiers"
# 抽的是「档案状态」那两类：projects 与 research。
# courses 那两份（requirements／syllabus）是**文档**，不是档案状态 ⇒ 本刀不动。
MIGRATE_CATEGORIES = ("projects", "research")


def migrate(*, dry_run: bool = False, force: bool = False) -> int:
    base = real_target(DOSSIERS_LOGICAL)
    if not base.is_dir():
        raise SystemExit(f"dossiers root does not exist: {base}")

    changed = 0
    for category in MIGRATE_CATEGORIES:
        folder = base / category
        if not folder.is_dir():
            continue
        for entry in sorted(p for p in folder.iterdir() if p.is_dir()):
            plan_path = entry / "plan.md"
            if not plan_path.is_file():
                continue
            target = profile_path(entry)
            if target.is_file() and not force:
                print(f"skip   {category}/{entry.name}: {PROFILE_FILENAME} already exists")
                continue

            profile = parse_plan_markdown(
                plan_path.read_text(encoding="utf-8"),
                agent=entry.name,
                category=category,
            )
            progress_path = entry / "progress.md"
            if progress_path.is_file():
                profile.log = parse_progress_markdown(progress_path.read_text(encoding="utf-8"))

            if dry_run:
                print(
                    f"would  {category}/{entry.name}: goal={len(profile.goal)} "
                    f"status={len(profile.status)} stages={len(profile.stages)} "
                    f"log={len(profile.log)}"
                )
            else:
                write_profile(target, profile)
                print(
                    f"write  {category}/{entry.name}: goal={len(profile.goal)} "
                    f"status={len(profile.status)} stages={len(profile.stages)} "
                    f"log={len(profile.log)}"
                )
            changed += 1
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate legacy dossier markdown into dossier.json")
    parser.add_argument("--dry-run", action="store_true", help="只报将要做什么，不写盘")
    parser.add_argument("--force", action="store_true", help="已存在的 dossier.json 也重写")
    args = parser.parse_args()

    try:
        changed = migrate(dry_run=args.dry_run, force=args.force)
    except MindUnavailable as exc:
        print(f"mind surface unavailable: {exc}")
        return 1

    verb = "would migrate" if args.dry_run else "migrated"
    print(f"{verb} {changed} dossier(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
