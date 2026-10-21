#!/usr/bin/env python3
"""WP-AGENT-RAIL-LIVE 续：把分家库里遗留的 ``messages`` 并进单库总账（裁定 180 的收尾）。

## 为什么需要这一刀

``migrate_session_attribution.py`` 只搬了 ``sessions`` 行与 ``session_model_usage``
——**没搬 ``messages``**。于是迁移之后：总账里有会话行（带 ``agent_id``），
转录却还留在各家的 ``profiles/<家名>/state.db`` 里；而读路径已经全部切到总账
（裁定 180 单库）⇒ 那些历史对话打开是空的。

## 与上一刀的两个关键差异

1. **id 不能照搬**：``messages.id`` 是 ``INTEGER PRIMARY KEY AUTOINCREMENT``。
   分家库的号从 1 开始，总账的号已经用到四位数——照搬 id 会让
   ``INSERT OR IGNORE`` 静默吃掉大半（撞主键），且撞掉的那些**看不出丢了**。
   所以这里**重新发号**（交给 AUTOINCREMENT），只保 ``session_id`` 与其余列；
   每库按原 id 升序插入，同一会话内的先后顺序不变（读面按 ``timestamp, id`` 排）。
2. **可重复执行**：重新发号之后重跑就是重复插入。所以每个源库在
   **同一个事务**里写一条 ``state_meta`` 标记（``messages_migrated:<家名>``），
   只搬没有标记的库；崩在中途也不会写半个标记，下一个 ``--run`` 会整库重来一次
   （该库那半笔已随事务回滚）。

## 安全口径（个人数据）

- 任何写操作前先**全量备份**（总账 + 每个有消息的源库）到
  ``D:\\Temp\\plobi-sandbox\\message-migration-backups\\<时间戳>\\``；
  ``--run`` 在备份失败时**拒绝继续**。
- 源库**只读打开**（``mode=ro``），一个字节都不写。
- ``--dry-run`` 只读：报每个源库的行数、目标会话是否在总账里、列差、将写入的行数，
  不写一个字节。
- ``--rollback <备份目录>``：从备份恢复总账（源库本就未动）。
- 消息 ``content`` 里可能有对话正文——脚本**不打印正文**，只打计数与 id。

用法：
    python scripts/plobi/migrate_messages_to_ledger.py --dry-run
    python scripts/plobi/migrate_messages_to_ledger.py --run
    python scripts/plobi/migrate_messages_to_ledger.py --rollback <backup-dir>
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from plobi_constants import get_plobi_home, get_plobi_state_home  # noqa: E402

BACKUP_ROOT = Path(r"D:\Temp\plobi-sandbox\message-migration-backups")

#: 要搬的列（``id`` 故意不在其中：重新发号，见模块头）。
MESSAGE_COLUMNS = (
    "session_id, role, content, tool_call_id, tool_calls, tool_name, "
    "effect_disposition, timestamp, token_count, finish_reason, reasoning, "
    "reasoning_content, reasoning_details, codex_reasoning_items, "
    "codex_message_items, platform_message_id, observed, active, compacted, asked_by"
)

MARKER_PREFIX = "messages_migrated:"


def _ledger_path() -> Path:
    return Path(get_plobi_state_home()) / "state.db"


def _profiles_root() -> Path:
    return Path(get_plobi_home()) / "profiles"


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def _sources(ledger_path: Path) -> list[tuple[str, Path]]:
    """有消息的分家库：(家名, db 路径)。总账自己那份 home 不算「分家」。"""
    out: list[tuple[str, Path]] = []
    root = _profiles_root()
    if not root.is_dir():
        return out
    for db_path in sorted(root.glob("*/state.db")):
        if db_path.resolve() == ledger_path.resolve():
            continue
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            finally:
                conn.close()
        except sqlite3.Error as exc:
            print(f"  ! {db_path.parent.name}: 读不了（{exc}），跳过")
            continue
        if count:
            out.append((db_path.parent.name, db_path))
    return out


def _marker_state(ledger: sqlite3.Connection, name: str) -> str:
    try:
        row = ledger.execute(
            "SELECT value FROM state_meta WHERE key = ?", (MARKER_PREFIX + name,)
        ).fetchone()
    except sqlite3.Error:
        return ""
    return str(row[0]) if row else ""


def _backup(ledger_path: Path, sources: list[tuple[str, Path]]) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = BACKUP_ROOT / f"backup-{stamp}"
    target.mkdir(parents=True, exist_ok=False)
    shutil.copy2(ledger_path, target / "ledger-state.db")
    for name, db_path in sources:
        shutil.copy2(db_path, target / f"profile-{name}-state.db")
    return target


def _plan(ledger_path: Path, sources: list[tuple[str, Path]]) -> list[dict]:
    """只读：每个源库要搬什么、有没有搬不动的东西。"""
    ledger = sqlite3.connect(f"file:{ledger_path}?mode=ro", uri=True)
    try:
        ledger_sessions = {r[0] for r in ledger.execute("SELECT id FROM sessions")}
        ledger_message_count = ledger.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        plans: list[dict] = []
        for name, db_path in sources:
            plan: dict = {
                "name": name,
                "db": db_path,
                "already": _marker_state(ledger, name),
                "rows": 0,
                "missing_session": 0,
                "extra_cols": [],
                "missing_cols": [],
            }
            src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                src_cols = set(_columns(src, "messages"))
                ledger_cols = set(_columns(ledger, "messages"))
                plan["extra_cols"] = sorted(ledger_cols - src_cols)
                plan["missing_cols"] = sorted(src_cols - ledger_cols)
                wanted = [c.strip() for c in MESSAGE_COLUMNS.split(",")]
                usable = [c for c in wanted if c in src_cols]
                plan["columns"] = usable
                rows = src.execute(
                    f"SELECT session_id, id FROM messages ORDER BY id"
                ).fetchall()
                plan["rows"] = len(rows)
                plan["missing_session"] = sum(1 for sid, _ in rows if sid not in ledger_sessions)
            finally:
                src.close()
            plans.append(plan)
        return plans, ledger_sessions, ledger_message_count
    finally:
        ledger.close()


def _copy_one(ledger_path: Path, plan: dict) -> int:
    """把一个源库并进总账：单事务 + 标记。返回写入行数。"""
    name = plan["name"]
    columns = plan["columns"]
    placeholders = ",".join("?" for _ in columns)

    ledger = sqlite3.connect(str(ledger_path), timeout=30.0)
    ledger.execute("PRAGMA journal_mode=WAL")
    try:
        src = sqlite3.connect(f"file:{plan['db']}?mode=ro", uri=True)
        try:
            rows = src.execute(
                f"SELECT {','.join(columns)} FROM messages ORDER BY id"
            ).fetchall()
        finally:
            src.close()

        with ledger:  # 一个事务：行与标记同生同死
            # 计数**不能**用 ``total_changes``：它会连 FTS 触发器里的变更一起数
            # （2026-10-21 现场：577 行报成 30416 条）。真数只认 messages 的行数差。
            before = ledger.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            ledger.executemany(
                f"INSERT INTO messages ({','.join(columns)}) VALUES ({placeholders})", rows
            )
            written = ledger.execute("SELECT COUNT(*) FROM messages").fetchone()[0] - before
            ledger.execute(
                "INSERT INTO state_meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (MARKER_PREFIX + name, str(written)),
            )
        return written
    finally:
        ledger.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="把分家库遗留的 messages 并进单库总账")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="只读：报数，不写一个字节")
    mode.add_argument("--run", action="store_true", help="先备份，再搬")
    mode.add_argument("--rollback", metavar="BACKUP_DIR", help="从备份恢复总账")
    args = ap.parse_args(argv)

    ledger_path = _ledger_path()
    if not ledger_path.exists():
        print(f"总账不存在：{ledger_path}")
        return 2

    if args.rollback:
        backup = Path(args.rollback)
        restored = backup / "ledger-state.db"
        if not restored.is_file():
            print(f"这个备份目录里没有 ledger-state.db：{backup}")
            return 2
        shutil.copy2(restored, ledger_path)
        print(f"已从 {backup} 恢复总账（源库本就未动）。")
        return 0

    sources = _sources(ledger_path)
    plans, ledger_sessions, ledger_message_count = _plan(ledger_path, sources)

    print(f"总账：{ledger_path}")
    print(f"  现读 {len(ledger_sessions)} 个会话 / {ledger_message_count} 条消息")
    print(f"有消息的分家库：{len(plans)} 个\n")

    todo: list[dict] = []
    total_rows = 0
    for plan in plans:
        state = "已搬（跳过）" if plan["already"] else "待搬"
        print(
            f"  {plan['name']:24s} rows={plan['rows']:4d} 缺会话={plan['missing_session']:3d} "
            f"列差={('+'.join(plan['extra_cols']) or '无')} [{state}]"
        )
        if plan["missing_cols"]:
            print(f"    !! 源库有总账没有的列：{plan['missing_cols']}（这些列会丢）")
        if not plan["already"]:
            todo.append(plan)
            total_rows += plan["rows"]

    print(f"\n本次将写入：{total_rows} 条（{len(todo)} 个库）")
    if args.dry_run:
        print("--dry-run：一个字节都没写。")
        return 0

    if not todo:
        print("没有待搬的库——总账已经齐了。")
        return 0

    try:
        backup = _backup(ledger_path, sources)
    except Exception as exc:  # noqa: BLE001
        print(f"备份失败，拒绝继续：{exc}")
        return 1
    print(f"备份：{backup}")

    written_total = 0
    for plan in todo:
        try:
            written = _copy_one(ledger_path, plan)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {plan['name']}: 搬失败（{exc}）——该库整笔已回滚，标记未写")
            return 1
        written_total += written
        print(f"  {plan['name']:24s} 写入 {written} 条")

    print(f"\n完成：共写入 {written_total} 条。回滚：--rollback {backup}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
