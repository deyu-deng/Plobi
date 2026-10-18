#!/usr/bin/env python3
"""WP-AGENT-SESSION-ATTRIBUTION（裁定 180）一次性迁移：profile 家的 session 并入顶层库。

桌面一个后端一个库（SINGLE-GATEWAY）后，``sessions`` 的隔离从文件级（一家一库）
改为行级（``agent_id``）。本脚本把存量 13 个有 ``state.db`` 的 profile 家里的
session 行并入顶层 ``$PLOBI_HOME/state.db``，按「家名 → 名册行」反查打
``agent_id``；顶层库存量行不打标（空串 = L1）。

**安全口径（个人数据）**：
- 任何写操作前先全量备份（顶层 + 各家）到 ``D:\\Temp\\plobi-sandbox\\`` 下
  带时间戳的目录；``--run`` 拒绝在备份失败时继续。
- ``--dry-run`` 只读：报各家行数、名册反查结果、将写入的 agent_id 分布、
  撞车预判，不写一个字节。
- 撞车（session id 在顶层已存在）→ **保留顶层行**，跳过并入，逐一列出
  （INSERT OR IGNORE 语义）——个人数据不许被静默覆盖。
- 反查不到名册的孤儿家 → 行打空串（= L1）并 WARN 列出，不静默、不丢弃。
- 家库**不删**（乙案终态另刀）：迁移后它们只是不再被读的历史副本，也是
  ``--rollback`` 的源。
- ``--rollback <备份目录>``：从备份恢复顶层 state.db（各家本就未动）。

用法：
    python scripts/plobi/migrate_session_attribution.py --dry-run
    python scripts/plobi/migrate_session_attribution.py --run
    python scripts/plobi/migrate_session_attribution.py --rollback <backup-dir>
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from plobi_constants import get_plobi_home  # noqa: E402

BACKUP_ROOT = Path(r"D:\Temp\plobi-sandbox\session-migration-backups")

# sessions 全列（照 plobi_state.SCHEMA_SQL 的顺序；agent_id 由本脚本填）。
SESSION_COLUMNS = (
    "id, source, user_id, session_key, chat_id, chat_type, thread_id, "
    "display_name, origin_json, expiry_finalized, model, model_config, "
    "system_prompt, parent_session_id, started_at, ended_at, end_reason, "
    "message_count, tool_call_count, input_tokens, output_tokens, "
    "cache_read_tokens, cache_write_tokens, reasoning_tokens, cwd, "
    "git_branch, git_repo_root, billing_provider, billing_base_url, "
    "billing_mode, estimated_cost_usd, actual_cost_usd, cost_status, "
    "cost_source, pricing_version, title, api_call_count, handoff_state, "
    "handoff_platform, handoff_error, compression_failure_cooldown_until, "
    "compression_failure_error, rewind_count, archived"
)

# session_model_usage（v20 per-model 记账）——console 的 todayCalls/todayUsd
# 走它。它**没有 agent_id 列**，归属经 session_id 关联：sessions 搬完、
# usage 按 PK 去重并入同一库，「该 agent 的用量」= 先按 agent_id 取 session
# ids 再查 usage（console totals_for_sessions 的形状）。
USAGE_COLUMNS = (
    "session_id, model, billing_provider, billing_base_url, billing_mode, "
    "api_call_count, input_tokens, output_tokens, cache_read_tokens, "
    "cache_write_tokens, reasoning_tokens, estimated_cost_usd, "
    "actual_cost_usd, cost_status, cost_source, first_seen, last_seen"
)


def _profile_to_agent() -> dict[str, str]:
    """家名（profile）→ 名册 name；查不到的不进表（调用方按孤儿处理）。"""
    try:
        from plobi.agents.registry import load_registry

        entries = load_registry().entries()
    except Exception as exc:  # 名册读不了 = 不敢迁移，宁可全报孤儿
        print(f"ERROR: registry unreadable ({exc}); refusing to guess attribution")
        return {}
    out: dict[str, str] = {}
    for entry in entries:
        profile = (getattr(entry, "profile_name", "") or "").strip()
        name = (getattr(entry, "name", "") or "").strip()
        if profile and name:
            out[profile] = name
    return out


def _count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _agent_id_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT COALESCE(agent_id, ''), COUNT(*) FROM sessions GROUP BY 1"
    ).fetchall()
    return {str(k): int(v) for k, v in rows}


def _backup(home: Path, profile_dbs: list[Path], stamp: str) -> Path:
    dest = BACKUP_ROOT / f"backup-{stamp}"
    dest.mkdir(parents=True, exist_ok=False)
    top = home / "state.db"
    if top.exists():
        shutil.copy2(top, dest / "top-state.db")
    for db in profile_dbs:
        # 备份里保持 profiles/<家名>/state.db 的相对形状，rollback 与人工核对
        # 都能直接对上是哪一家。
        rel = db.relative_to(home)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(db, target)
    # 顺手带上 WAL/SHM（若在）——只 copy state.db 可能丢还没 checkpoint 的帧。
    for suffix in ("-wal", "-shm"):
        side = home / f"state.db{suffix}"
        if side.exists():
            shutil.copy2(side, dest / f"top-state.db{suffix}")
    return dest


def _profile_dbs(home: Path) -> list[Path]:
    root = home / "profiles"
    if not root.is_dir():
        return []
    return sorted(
        p for p in (root / d / "state.db" for d in sorted(
            x.name for x in root.iterdir() if x.is_dir()
        )) if p.exists()
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true", help="只读：报数与预判，不写")
    g.add_argument("--run", action="store_true", help="备份后真跑迁移")
    g.add_argument("--rollback", metavar="BACKUP_DIR", help="从备份恢复顶层 state.db")
    args = ap.parse_args()

    home = Path(get_plobi_home())
    top_db = home / "state.db"
    profile_dbs = _profile_dbs(home)
    roster = _profile_to_agent()

    if args.rollback:
        backup = Path(args.rollback)
        src = backup / "top-state.db"
        if not src.is_file():
            print(f"ERROR: {src} not found")
            return 1
        if not top_db.exists():
            print(f"ERROR: {top_db} missing — nothing to restore onto")
            return 1
        shutil.copy2(src, top_db)
        for suffix in ("-wal", "-shm"):
            side = backup / f"top-state.db{suffix}"
            if side.exists():
                shutil.copy2(side, top_db.with_name(top_db.name + suffix))
        print(f"rolled back {top_db} from {src}")
        return 0

    print(f"PLOBI_HOME = {home}")
    print(f"top state.db: {'exists' if top_db.exists() else 'MISSING'}")
    print(f"profile homes with state.db: {len(profile_dbs)}")
    if not profile_dbs:
        print("nothing to migrate")
        return 0

    # 读侧账本：各家行数 + 反查结果 + 顶层现状
    per_home: list[tuple[Path, str, int]] = []
    orphans: list[str] = []
    for db in profile_dbs:
        home_name = db.parent.name
        agent = roster.get(home_name, "")
        if not agent:
            orphans.append(home_name)
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            n = _count(conn, "sessions")
            try:
                u = _count(conn, "session_model_usage")
            except sqlite3.OperationalError:
                u = 0  # 老库没有 usage 表（v20 之前）
        finally:
            conn.close()
        per_home.append((db, agent, n))

    top_before = 0
    top_usage_before = 0
    if top_db.exists():
        conn = sqlite3.connect(f"file:{top_db}?mode=ro", uri=True)
        try:
            top_before = _count(conn, "sessions")
            try:
                top_usage_before = _count(conn, "session_model_usage")
            except sqlite3.OperationalError:
                top_usage_before = 0
        finally:
            conn.close()

    total_home = sum(n for _, _, n in per_home)
    print(f"\ntop sessions (before): {top_before}")
    print(f"home sessions (sum):   {total_home}")
    for db, agent, n in per_home:
        mark = "" if agent else "  ← ORPHAN (rows will be attributed to '')"
        print(f"  {db.parent.name:28s} → agent_id={agent or "''":12s} rows={n}{mark}")
    if orphans:
        print(f"\nWARNING: {len(orphans)} orphan home(s) not in roster: {orphans}")
        print("         their rows land as '' (= L1) — listed, not silently dropped")
    print(f"top usage rows (before): {top_usage_before} (merged per PK, dedup)")

    if args.dry_run:
        # 撞车预判：各家 id 与顶层现存 id 的交集
        if top_db.exists():
            top_ids = set()
            conn = sqlite3.connect(f"file:{top_db}?mode=ro", uri=True)
            try:
                top_ids = {r[0] for r in conn.execute("SELECT id FROM sessions")}
            finally:
                conn.close()
            clashes: list[str] = []
            for db, _agent, _n in per_home:
                conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
                try:
                    ids = {r[0] for r in conn.execute("SELECT id FROM sessions")}
                finally:
                    conn.close()
                hits = sorted(ids & top_ids)
                if hits:
                    clashes.extend(f"{db.parent.name}:{h}" for h in hits)
            if clashes:
                print(f"\nWARNING: {len(clashes)} session id(s) already in top (will be SKIPPED, top kept):")
                for c in clashes[:20]:
                    print(f"  {c}")
                if len(clashes) > 20:
                    print(f"  … and {len(clashes) - 20} more")
            else:
                print("\nno session-id clashes with top — clean insert expected")
        print("\nDRY RUN — nothing written")
        return 0

    # --run：备份优先，备份失败不写
    stamp = time.strftime("%Y%m%d-%H%M%S")
    try:
        backup = _backup(home, profile_dbs, stamp)
    except Exception as exc:
        print(f"ERROR: backup failed ({exc}); refusing to migrate without it")
        return 1
    print(f"\nbackup → {backup}")

    # 顶层库若是 v20 旧库，先经 SessionDB 的声明式 reconcile 加上 agent_id 列
    # （plobi_state._reconcile_columns 是加列的唯一正门——裸 sqlite3.connect
    # 不会触发它，2026-10-18 首跑就是在这里撞的 "no such column: agent_id"）。
    from plobi_state import SessionDB as _SessionDB

    reconciler = _SessionDB(db_path=top_db)
    try:
        reconciler.close()
    except Exception:
        pass

    conn = sqlite3.connect(str(top_db))
    try:
        inserted_total = 0
        for db, agent, _n in per_home:
            conn.execute("ATTACH DATABASE ? AS src", (str(db),))
            try:
                before = _count(conn, "main.sessions")
                # 显式关 cursor：DETACH 前任何 active 语句都会让 src 保持锁定
                # （2026-10-18 首跑撞过 "database src is locked"）。注意 python
                # 的 sqlite3.Cursor 不支持 with，必须手动 close。
                cur = conn.execute(
                    f"INSERT OR IGNORE INTO main.sessions ({SESSION_COLUMNS}, agent_id) "
                    f"SELECT {SESSION_COLUMNS}, ? FROM src.sessions",
                    (agent,),
                )
                inserted_here = cur.rowcount
                cur.close()
                after = _count(conn, "main.sessions")
                skipped = _count(conn, "src.sessions") - inserted_here
                inserted_total += inserted_here
                print(
                    f"  {db.parent.name:28s} inserted={inserted_here:4d} "
                    f"skipped={skipped:4d} (top {before} → {after})"
                )
                if skipped:
                    print(f"    NOTE: {skipped} row(s) from this home already existed in top (kept top)")
                # usage 同库并入（按 PK 去重；sessions 可能被跳过，但那行的
                # usage 在顶层已有同样的 session——PK 撞车自然 OR IGNORE）。
                try:
                    ucur = conn.execute(
                        f"INSERT OR IGNORE INTO main.session_model_usage ({USAGE_COLUMNS}) "
                        f"SELECT {USAGE_COLUMNS} FROM src.session_model_usage"
                    )
                    usage_merged = ucur.rowcount
                    ucur.close()
                    if usage_merged:
                        print(f"    usage rows merged: {usage_merged}")
                except sqlite3.OperationalError as exc:
                    print(f"    usage merge skipped ({exc})")
                # 一家一 commit：中途失败时前面的家已落盘（备份可整体回滚），
                # 未提交的部分随连接关闭自动回滚。
                conn.commit()
            finally:
                conn.execute("DETACH DATABASE src")

        # 守恒核对：top_before + 实际插入 = 现在
        after = _count(conn, "sessions")
        expected = top_before + inserted_total
        status = "OK" if after == expected else "MISMATCH"
        print(f"\nconservation: {top_before} + {inserted_total} = {expected} vs actual {after} → {status}")
        dist = _agent_id_counts(conn)
        nonempty = {k: v for k, v in sorted(dist.items()) if k}
        print(f"agent_id distribution: '' (L1) = {dist.get('', 0)}, "
              f"attributed = {sum(nonempty.values())} across {len(nonempty)} agent(s)")
        for k, v in nonempty.items():
            print(f"    {k:24s} {v}")
        if after != expected:
            print("ERROR: conservation failed — roll back with:")
            print(f"  python scripts/plobi/migrate_session_attribution.py --rollback {backup}")
            return 1
    finally:
        conn.close()

    print(f"\nDONE. top state.db now serves every agent (agent_id row-level isolation).")
    print(f"home DBs left untouched (history + rollback source). backup: {backup}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
