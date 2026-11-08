#!/usr/bin/env python3
"""Migrate vault data from filesystem to database.

Usage:
    python scripts/migrate_vault_db.py [--force] [--dry-run]

Steps:
    1. 检查是否已有数据（避免重复插入）
    2. 扫描文件系统：notes/、dossiers/、diary/journal/
    3. 插入数据库（vault.db）
    4. 输出统计信息

注意事项：
    - 只读操作不会修改原文件
    - --force 会清空现有数据库记录再迁移
    - --dry-run 仅显示将要插入的记录数
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

# Add code directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from plobi.mind.vault import (
    connect,
    upsert_note,
    upsert_dossier,
    upsert_diary,
    VaultNote,
    AgentDossier,
    UserDiaryEntry,
)


def get_roots() -> Dict[str, Path]:
    """定位三份数据的真实落点。

    **不许手拼字面路径**（``WP-VAULT-MIND-TWOROOT`` 的铁律：手拼路径的假读数正是那一族的成因）。
    一律走被测代码自己的解析函数 ``plobi.mind.paths.real_target``——它认得逻辑前缀
    ``Surface/...``，并按 ``SURFACE_ROOT``／注册表／家目录的顺序解析。

    旧版本这里写的是 ``home / "vault" / ...``，**那个目录在盘上不存在**（数据在
    ``$SURFACE_ROOT`` 本体的 ``notes/``／``dossiers/``／``diary/`` 底下），
    于是每次扫描都扫到 0 条——那正是"迁移脚本跑完却什么都没迁"的成因。

    **跨家边界（用户 11-08 14:5x 拍定：「我们其实不需要把开发版的数据迁移过来。
    就让安装版保持什么都没有的全新状态。」）**：
    这一刀只做**同一个家内部**的「markdown 文件 → 表」索引，
    **绝不从别的家搬数据**。落点由 ``real_target()`` 解析成哪个家，就只动哪个家。
    开发态（``Plobi-Dev``）与装机态（``Plobi``）是**两个身份**，各自的数据互不相干。
    """
    from plobi.mind.paths import real_target

    def _target(logical: str) -> Path:
        try:
            return Path(real_target(logical))
        except Exception as exc:  # 脑/人读面不可用时要说清楚，不静默给假根
            raise SystemExit(f"无法解析 {logical!r}：{exc}")

    roots = {
        "notes": _target("Surface/notes"),
        "dossiers": _target("Surface/dossiers"),
        "diary": _target("Surface/diary"),
    }

    # **硬门：库必须与数据根在同一个家里**（用户 11-08 14:5x 拍定的跨家边界）
    # 开发态（Plobi-Dev）与装机态（Plobi）是两个身份，各自的数据互不相干。
    # 库落点由 `vault_db_path()` 解析（跟着同一个 shared root 走）；
    # 若它与数据根不在同一家 ⇒ 说明指针指错了，**当场停**，绝不把一个家的数据
    # 写进另一个家的库。
    from plobi.mind.vault import vault_db_path

    db_path = Path(vault_db_path()).resolve()
    surface_root = roots["notes"].resolve().parent  # .../Plobi-Dev 或 .../Plobi

    # 库落点的形状是 ``<家>/plobi/vault.db`` ⇒ 家就是它的上上级；
    # 若是 ``<家>/vault.db``（没有 plobi 那层）⇒ 家就是它的上级。
    db_parent = db_path.parent
    db_home = db_parent.parent if db_parent.name == "plobi" else db_parent

    if db_home != surface_root:
        raise SystemExit(
            "库与数据根不在同一个家，拒绝迁移：\n"
            f"  数据根（人读面）: {surface_root}\n"
            f"  库落点          : {db_path}\n"
            "开发态与装机态是两个身份，**绝不跨家搬数据**。"
        )

    return roots


def migrate_notes(roots: Dict[str, Path], dry_run: bool = False) -> int:
    """Migrate notes from filesystem to DB."""
    notes_root = roots["notes"]
    if not notes_root.exists():
        print(f"⚠️  Notes root not found: {notes_root}")
        return 0
    
    count = 0
    skipped = 0
    
    # Import notes library for building metadata
    from plobi.mind.notes_library import build_library, _net_chars, MIN_NOTE_NET_CHARS
    from plobi.mind.paths import real_target

    print(f"\n📚 Migrating notes from {notes_root}...")
    print("-" * 60)

    library = build_library()

    for note_data in library.get("notes", []):
        path = note_data.get("path", "")

        # Check minimum net chars threshold
        # **正文按逻辑路径解析**：`build_library()` 给的是逻辑路径（`Surface/notes/...`），
        # **没有 `fsPath` 这一格**——旧代码读 `note_data.get("fsPath", "")` 拿到空串，
        # 于是 `Path("")` 变成当前目录 `.`，一读就 PermissionError。
        # 落点仍走 `real_target()`，不手拼绝对路径。
        try:
            fs_path = Path(real_target(path))
        except Exception as exc:
            print(f"⚠️  无法解析 {path!r}：{exc}")
            skipped += 1
            continue
        if not fs_path.exists():
            print(f"⚠️  正文不存在：{path}")
            skipped += 1
            continue
        text = fs_path.read_text(encoding="utf-8", errors="replace")
        if _net_chars(text) < MIN_NOTE_NET_CHARS:
            skipped += 1
            continue

        note = VaultNote(
            path=path,
            title=note_data["title"],
            topic=note_data.get("topic", ""),
            tags=note_data.get("tags", []),
            belongs=note_data.get("belongs", []),
            relates=[],  # TODO: Parse from file content
            updated=note_data.get("updated", ""),
            chars=note_data.get("chars", 0),
            excerpt=note_data.get("excerpt", "")[:500]  # Truncate excerpt
        )
        
        if not dry_run:
            upsert_note(note)
        
        count += 1
    
    print(f"✅ Migrated {count} notes")
    if skipped > 0:
        print(f"⏭️ Skipped {skipped} notes (below MIN_NOTE_NET_CHARS threshold)")
    
    return count


def migrate_dossiers(roots: Dict[str, Path], dry_run: bool = False) -> int:
    """Migrate agent dossiers from filesystem to DB.

    档案也在**人读面**上（``Surface/dossiers/<品类>/<名>/{plan,progress}.md``），
    不在脑仓里 —— 所以这里只需要 dossiers 根，不需要 mind 根。
    """
    dossiers_root = roots["dossiers"]
    if not dossiers_root.exists():
        print(f"⚠️  Dossiers root not found: {dossiers_root}")
        return 0
    
    print(f"\n📁 Migrating agent dossiers from {dossiers_root}...")
    print("-" * 60)
    
    count = 0
    
    # Process each category folder
    # **目录名照盘上真实的写**（`AGENT_CATEGORIES` 那族：butler／courses／events／projects／research）
    for category_dir in ["projects", "courses", "research", "butler", "events"]:
        category_path = dossiers_root / category_dir
        if not category_path.exists():
            continue
        
        for entry in category_path.iterdir():
            if entry.name.startswith("."):
                continue
            
            agent_id = entry.stem if entry.is_dir() else entry.stem
            
            # Read plan.md and progress.md if they exist
            plan_content = None
            progress_content = None
            work_path = str(entry) if entry.is_dir() else None
            
            try:
                if (entry / "plan.md").exists():
                    plan_content = (entry / "plan.md").read_text(encoding="utf-8", errors="replace")
                
                if (entry / "progress.md").exists():
                    progress_content = (entry / "progress.md").read_text(encoding="utf-8", errors="replace")
                
                # Extract milestones from progress.md
                milestones_total = 0
                milestones_done = 0
                if progress_content:
                    for line in progress_content.splitlines():
                        stripped = line.lstrip()
                        if stripped.startswith(("- [ ]", "- [x]", "- [X]")):
                            milestones_total += 1
                            if stripped[3] in "xX":
                                milestones_done += 1
                
                # Get updated timestamp
                # **ISO 8601 字符串**，不是 mtime 浮点——前端那格是直接上屏的日期，
                # 浮点会显示成 1786... 这种一串数字（`agent_dossier._file_updated` 也是 ISO）
                updated = ""
                if (entry / "progress.md").exists():
                    updated = datetime.fromtimestamp(
                        (entry / "progress.md").stat().st_mtime
                    ).isoformat(timespec="seconds")
                
                dossier = AgentDossier(
                    agent_id=agent_id,
                    plan_content=plan_content,
                    progress_content=progress_content,
                    milestones_total=milestones_total,
                    milestones_done=milestones_done,
                    work_path=work_path,
                    updated=str(updated)
                )
                
                if not dry_run:
                    upsert_dossier(dossier)
                
                count += 1
                
            except Exception as e:
                print(f"❌ Error processing {agent_id}: {e}")
                continue
    
    print(f"✅ Migrated {count} agent dossiers")
    return count


def migrate_diary(roots: Dict[str, Path], dry_run: bool = False) -> int:
    """Migrate diary entries from filesystem to DB."""
    diary_root = roots["diary"]
    if not diary_root.exists():
        print(f"⚠️  Diary root not found: {diary_root}")
        return 0
    
    print(f"\n📔 Migrating diary entries from {diary_root}...")
    print("-" * 60)
    
    count = 0
    
    for entry_file in sorted(diary_root.glob("*.md")):
        # **日期键只取前 10 格**（`YYYY-MM-DD`）——文件名带星期（`2026-11-08-Sunday.md`），
        # 整个 stem 当键的话，DB 里的日期就与 `diary.read_entry(day)` 查的那个对不上
        #（那边用 `parse_day` 只认 `YYYY-MM-DD`）⇒ 读日记永远读不到。
        date = entry_file.stem[:10]
        if len(date) != 10:
            continue
        try:
            parsed = datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            continue

        try:
            content = entry_file.read_text(encoding="utf-8", errors="replace")
            updated = datetime.fromtimestamp(entry_file.stat().st_mtime).isoformat(timespec="seconds")
            
            diary_entry = UserDiaryEntry(
                date=date,
                content=content,
                updated=str(updated)
            )
            
            if not dry_run:
                upsert_diary(diary_entry)
            
            count += 1
            
        except Exception as e:
            print(f"❌ Error processing {date}: {e}")
            continue
    
    print(f"✅ Migrated {count} diary entries")
    return count


def check_existing(conn):
    """Check how many records already exist in the database."""
    cursor = conn.execute("SELECT COUNT(*) FROM vault_notes")
    notes_count = cursor.fetchone()[0]
    
    cursor = conn.execute("SELECT COUNT(*) FROM agent_dossiers")
    dossiers_count = cursor.fetchone()[0]
    
    cursor = conn.execute("SELECT COUNT(*) FROM user_diary")
    diary_count = cursor.fetchone()[0]
    
    return notes_count, dossiers_count, diary_count


def main():
    parser = argparse.ArgumentParser(description="Migrate vault data from filesystem to DB")
    parser.add_argument("--force", action="store_true", help="Clear existing data before migration")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be migrated without actually doing it")
    args = parser.parse_args()
    
    print("=" * 60)
    print("Vault Database Migration Tool")
    print("=" * 60)
    
    # Get roots
    roots = get_roots()
    
    # Connect to database
    conn = connect()
    
    # Check existing data
    notes_count, dossiers_count, diary_count = check_existing(conn)
    
    if notes_count > 0 or dossiers_count > 0 or diary_count > 0:
        print(f"\n⚠️  Database already has {notes_count} notes, {dossiers_count} dossiers, {diary_count} diary entries")
        
        if not args.force:
            print("\nUse --force to clear existing data and migrate again")
            if not args.dry_run:
                print("\nExiting without migrating...")
                conn.close()
                return 1
    
    if args.force:
        print("\n⚠️  Clearing existing data...")
        conn.execute("DELETE FROM vault_notes")
        conn.execute("DELETE FROM agent_dossiers")
        conn.execute("DELETE FROM user_diary")
        conn.commit()
    
    # Perform migration
    total_migrated = 0
    
    total_migrated += migrate_notes(roots, dry_run=args.dry_run)
    total_migrated += migrate_dossiers(roots, dry_run=args.dry_run)
    total_migrated += migrate_diary(roots, dry_run=args.dry_run)
    
    # Final statistics
    print("\n" + "=" * 60)
    print("Migration Complete!")
    print("=" * 60)
    
    final_notes, final_dossiers, final_diary = check_existing(conn)
    
    print(f"\nFinal counts:")
    print(f"  • vault_notes:      {final_notes} records")
    print(f"  • agent_dossiers:   {final_dossiers} records")
    print(f"  • user_diary:       {final_diary} records")
    print(f"  • Total migrated:   {total_migrated} records")
    
    conn.close()
    
    if args.dry_run:
        print("\n(Dry run mode - no changes were made)")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
