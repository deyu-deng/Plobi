"""Vault 数据库：vault_notes / agent_dossiers / user_diary 三张物理表。

参照 `agenda/store.py` 的模式：原生 SQLite（不通过 SQLAlchemy），WAL 模式，
一次性初始化 schema，无业务逻辑只负责 CRUD/查询。数据从文件系统扫描后插入，
之后 API 端点直接查库。

三个表：

1. **vault_notes** - 学科笔记元数据
   - id: 主键 = path（Surface/notes/...）
   - title, topic, tags JSONB, belongs JSONB, relates JSONB
   - updated, chars, excerpt

2. **agent_dossiers** - L2 Agent 标准档案
   - agent_id: 主键（如 "python-secrets", "microeconomics"）
   - plan_content, progress_content（正文内容）
   - milestones_total, milestones_done
   - work_path, updated

3. **user_diary** - 日记
   - date: 主键（YYYY-MM-DD）
   - content, updated
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

# --- Shared DB path (shared across profiles) ---------------------------

DB_NAME = "vault.db"
_INIT_LOCK = threading.Lock()
_INITIALIZED: set[str] = set()


def vault_db_path() -> Path:
    """Resolve the vault DB path."""
    override = os.environ.get("PLOBI_VAULT_DB", "").strip()
    if override:
        return Path(override)
    try:
        from plobi_constants import get_shared_plobi_root
        root = get_shared_plobi_root() / "plobi"
    except Exception:
        root = Path.home() / ".plobi" / "plobi"
    return root / DB_NAME


@dataclass
class VaultNote:
    """vault_notes 记录。"""
    path: str
    title: str
    topic: str
    tags: list = field(default_factory=list)
    belongs: list = field(default_factory=list)
    relates: list = field(default_factory=list)
    updated: str = ""
    chars: int = 0
    excerpt: str = ""
    
    def to_dict(self) -> dict:
        d = asdict(self)
        d["tags"] = json.dumps(d["tags"])
        d["belongs"] = json.dumps(d["belongs"])
        d["relates"] = json.dumps(d["relates"])
        return d
    
    @classmethod
    def from_dict(cls, data: dict) -> "VaultNote":
        data["tags"] = json.loads(data["tags"]) if isinstance(data["tags"], str) else data["tags"]
        data["belongs"] = json.loads(data["belongs"]) if isinstance(data["belongs"], str) else data["belongs"]
        data["relates"] = json.loads(data["relates"]) if isinstance(data["relates"], str) else data["relates"]
        return cls(**data)


@dataclass
class AgentDossier:
    """agent_dossiers 记录。

    11-09 起（WP-DOSSIER-SHAPE）档案本体是 ``dossier.json``，这份记录是它的**镜像**
    （元数据索引），不是真源：``plan_content``／``progress_content`` 那两格留着是为了
    不动既有库的结构，它们**不再被写**；结构化内容走 ``profile_json``。
    """

    agent_id: str
    plan_content: Optional[str] = None
    progress_content: Optional[str] = None
    milestones_total: int = 0
    milestones_done: int = 0
    work_path: Optional[str] = None
    updated: str = ""
    profile_json: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "AgentDossier":
        return cls(**data)


@dataclass
class UserDiaryEntry:
    """user_diary 记录。"""
    date: str  # YYYY-MM-DD
    content: str = ""
    updated: str = ""
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> "UserDiaryEntry":
        return cls(**data)


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open a connection with WAL + row factory, initializing schema once."""
    path = Path(db_path) if db_path else vault_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    key = str(path.resolve())
    if key not in _INITIALIZED:
        with _INIT_LOCK:
            if key not in _INITIALIZED:
                init_db(conn)
                _INITIALIZED.add(key)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create schema. Idempotent — safe to call on every connect."""
    conn.executescript("""
        -- vault_notes: 学科笔记元数据
        CREATE TABLE IF NOT EXISTS vault_notes (
            path       TEXT PRIMARY KEY,
            title      TEXT NOT NULL,
            topic      TEXT NOT NULL DEFAULT '',
            tags       TEXT NOT NULL DEFAULT '[]',
            belongs    TEXT NOT NULL DEFAULT '[]',
            relates    TEXT NOT NULL DEFAULT '[]',
            updated    TEXT,
            chars      INTEGER NOT NULL DEFAULT 0,
            excerpt    TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_vault_notes_topic ON vault_notes(topic);
        CREATE INDEX IF NOT EXISTS idx_vault_notes_updated ON vault_notes(updated);
        
        -- agent_dossiers: L2 Agent 档案（**镜像**：真源是档案本体 dossier.json）
        CREATE TABLE IF NOT EXISTS agent_dossiers (
            agent_id          TEXT PRIMARY KEY,
            plan_content      TEXT,
            progress_content  TEXT,
            milestones_total  INTEGER NOT NULL DEFAULT 0,
            milestones_done   INTEGER NOT NULL DEFAULT 0,
            work_path         TEXT,
            updated           TEXT,
            profile_json      TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_agent_dossiers_updated ON agent_dossiers(updated);
        
        -- user_diary: 日记条目
        CREATE TABLE IF NOT EXISTS user_diary (
            date     TEXT PRIMARY KEY,
            content  TEXT NOT NULL DEFAULT '',
            updated  TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_user_diary_date ON user_diary(date);
        """)
    _ensure_columns(conn)
    conn.commit()


# 后加的那几列：``CREATE TABLE IF NOT EXISTS`` 不会给**既有**的表补列，老库自己不长。
_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "agent_dossiers": {"profile_json": "TEXT"},
}


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """给既有库补上后加的那几列（幂等；表与列名都是这里的常量，不来自输入）。"""
    for table, columns in _ADDED_COLUMNS.items():
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for column, decl in columns.items():
            if column in existing:
                continue
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


# --- vault_notes operations ----------------------------------------------

def upsert_note(note: VaultNote) -> None:
    """Insert or update a note record."""
    conn = connect()
    try:
        cursor = conn.execute(
            """INSERT OR REPLACE INTO vault_notes 
               (path, title, topic, tags, belongs, relates, updated, chars, excerpt)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (note.path, note.title, note.topic,
             note.to_dict()["tags"], note.to_dict()["belongs"], note.to_dict()["relates"],
             note.updated, note.chars, note.excerpt)
        )
        conn.commit()
    finally:
        conn.close()


def delete_note(path: str) -> None:
    """Delete a note record by path."""
    conn = connect()
    try:
        conn.execute("DELETE FROM vault_notes WHERE path = ?", (path,))
        conn.commit()
    finally:
        conn.close()


def get_note(path: str) -> Optional[VaultNote]:
    """Get a note by path."""
    conn = connect()
    try:
        row = conn.execute(
            "SELECT * FROM vault_notes WHERE path = ?", (path,)
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        data["tags"] = json.loads(data["tags"])
        data["belongs"] = json.loads(data["belongs"])
        data["relates"] = json.loads(data["relates"])
        return VaultNote(**data)
    finally:
        conn.close()


def list_notes(topic: Optional[str] = None) -> List[VaultNote]:
    """List all notes, optionally filtered by topic."""
    conn = connect()
    try:
        query = "SELECT * FROM vault_notes"
        params = []
        if topic:
            query += " WHERE topic = ?"
            params.append(topic)
        query += " ORDER BY topic, title"
        
        rows = conn.execute(query, params).fetchall()
        notes = []
        for row in rows:
            data = dict(row)
            data["tags"] = json.loads(data["tags"])
            data["belongs"] = json.loads(data["belongs"])
            data["relates"] = json.loads(data["relates"])
            notes.append(VaultNote(**data))
        return notes
    finally:
        conn.close()


def search_notes(query_text: str, limit: int = 50) -> List[VaultNote]:
    """Search notes by title/excerpt."""
    conn = connect()
    try:
        pattern = f"%{query_text}%"
        rows = conn.execute(
            """SELECT * FROM vault_notes 
               WHERE title LIKE ? OR excerpt LIKE ?
               ORDER BY updated DESC
               LIMIT ?""",
            (pattern, pattern, limit)
        ).fetchall()
        notes = []
        for row in rows:
            data = dict(row)
            data["tags"] = json.loads(data["tags"])
            data["belongs"] = json.loads(data["belongs"])
            data["relates"] = json.loads(data["relates"])
            notes.append(VaultNote(**data))
        return notes
    finally:
        conn.close()


# --- agent_dossiers operations -------------------------------------------

def upsert_dossier(dossier: AgentDossier) -> None:
    """Insert or update an agent dossier record."""
    conn = connect()
    try:
        cursor = conn.execute(
            """INSERT OR REPLACE INTO agent_dossiers
               (agent_id, plan_content, progress_content, milestones_total,
                milestones_done, work_path, updated, profile_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (dossier.agent_id, dossier.plan_content, dossier.progress_content,
             dossier.milestones_total, dossier.milestones_done,
             dossier.work_path, dossier.updated, dossier.profile_json)
        )
        conn.commit()
    finally:
        conn.close()


def get_dossier(agent_id: str) -> Optional[AgentDossier]:
    """Get an agent dossier by agent_id."""
    conn = connect()
    try:
        row = conn.execute(
            "SELECT * FROM agent_dossiers WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        if not row:
            return None
        return AgentDossier(**dict(row))
    finally:
        conn.close()


def list_dossiers() -> List[AgentDossier]:
    """List all agent dossiers."""
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM agent_dossiers ORDER BY agent_id"
        ).fetchall()
        return [AgentDossier(**dict(row)) for row in rows]
    finally:
        conn.close()


# --- user_diary operations -----------------------------------------------

def upsert_diary(entry: UserDiaryEntry) -> None:
    """Insert or update a diary entry."""
    conn = connect()
    try:
        cursor = conn.execute(
            """INSERT OR REPLACE INTO user_diary (date, content, updated)
               VALUES (?, ?, ?)""",
            (entry.date, entry.content, entry.updated)
        )
        conn.commit()
    finally:
        conn.close()


def get_diary(date: str) -> Optional[UserDiaryEntry]:
    """Get a diary entry by date."""
    conn = connect()
    try:
        row = conn.execute(
            "SELECT * FROM user_diary WHERE date = ?", (date,)
        ).fetchone()
        if not row:
            return None
        return UserDiaryEntry(**dict(row))
    finally:
        conn.close()


def list_diaries(start_date: Optional[str] = None, end_date: Optional[str] = None) -> List[UserDiaryEntry]:
    """List diary entries, optionally filtered by date range."""
    conn = connect()
    try:
        query = "SELECT * FROM user_diary"
        params = []
        if start_date and end_date:
            query += " WHERE date BETWEEN ? AND ?"
            params.extend([start_date, end_date])
        elif start_date:
            query += " WHERE date >= ?"
            params.append(start_date)
        elif end_date:
            query += " WHERE date <= ?"
            params.append(end_date)
        query += " ORDER BY date DESC"
        
        rows = conn.execute(query, params).fetchall()
        return [UserDiaryEntry(**dict(row)) for row in rows]
    finally:
        conn.close()


# --- Migration utilities -------------------------------------------------

def migrate_notes_from_filesystem(notes_root: Path) -> int:
    """Scan filesystem and migrate notes to database. Returns count of migrated notes."""
    from plobi.mind.notes_library import build_library as build_library_from_fs
    
    library = build_library_from_fs()
    count = 0
    for note_data in library["notes"]:
        note = VaultNote(
            path=note_data["path"],
            title=note_data["title"],
            topic=note_data.get("topic", ""),
            tags=note_data.get("tags", []),
            belongs=note_data.get("belongs", []),
            relates=[],  # 暂时空着，后续从文件解析
            updated=note_data.get("updated", ""),
            chars=note_data.get("chars", 0),
            excerpt=note_data.get("excerpt", "")
        )
        upsert_note(note)
        count += 1
    return count


def migrate_dossiers_from_filesystem(mind_root: Path) -> int:
    """Scan filesystem and migrate dossiers to database. Returns count of migrated dossiers.

    真源是**档案本体**（``dossier.json``），这份迁移只把它的**元数据**搬进库当索引：
    目标／现状／阶段／状态都在 ``profile_json`` 里；md 那一对退役之后不再有正文可搬。
    """
    from plobi.mind.agent_dossier import build_index as build_index_from_fs

    index = build_index_from_fs()
    count = 0
    for agent_data in index["agents"]:
        profile = {
            "goal": agent_data.get("goal", ""),
            "status": agent_data.get("summary", ""),
            "state": agent_data.get("status", ""),
            "stages": agent_data.get("stages", []),
            "updated": agent_data.get("updated", ""),
        }
        dossier = AgentDossier(
            agent_id=agent_data.get("id", ""),
            milestones_total=agent_data.get("milestones", {}).get("total", 0),
            milestones_done=agent_data.get("milestones", {}).get("done", 0),
            work_path=agent_data.get("workPath", ""),
            updated=agent_data.get("updated", ""),
            profile_json=json.dumps(profile, ensure_ascii=False),
        )
        upsert_dossier(dossier)
        count += 1
    return count


def migrate_diary_from_filesystem(diary_root: Path) -> int:
    """Scan filesystem and migrate diary entries to database. Returns count of migrated entries."""
    from plobi.mind.diary import list_entries as list_entries_from_fs, read_entry
    
    entries = list_entries_from_fs()
    count = 0
    for date, meta in entries.items():
        try:
            entry_data = read_entry(date)
            entry = UserDiaryEntry(
                date=date,
                content=entry_data.get("text", "") if meta.get("exists") else "",
                updated=meta.get("updated", "")
            )
            upsert_diary(entry)
            count += 1
        except Exception as e:
            logger.warning(f"Failed to migrate diary entry {date}: {e}")
    return count


if __name__ == "__main__":
    # Quick migration test
    print("Migrating vault notes...")
    n = migrate_notes_from_filesystem(Path(__file__).parent.parent.parent / "Surface" / "notes")
    print(f"Migrated {n} notes")
    
    print("\nMigrating agent dossiers...")
    n = migrate_dossiers_from_filesystem(Path(__file__).parent.parent.parent / "Mind")
    print(f"Migrated {n} dossiers")
    
    print("\nMigrating diary entries...")
    n = migrate_diary_from_filesystem(Path(__file__).parent.parent.parent / "Surface" / "diary" / "journal")
    print(f"Migrated {n} diary entries")
