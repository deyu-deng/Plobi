"""裁定 196/201：`messages.kind` 是「这一条属于哪一类」的唯一落点。

钉三条：
1. 列在（SCHEMA 声明 + 自动 ADD COLUMN 对老库生效，不用版本门）；
2. `append_message(kind=...)` 写得进、读得出；
3. 不给 kind 的老写法仍然工作（NULL = 老数据，渲染层按 asked_by 推）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from plobi_state import SessionDB  # noqa: E402


def _db(tmp_path) -> SessionDB:
    return SessionDB(db_path=tmp_path / "state.db")


def test_kind_is_written_and_read_back(tmp_path):
    db = _db(tmp_path)
    db.create_session("s1", "desktop")
    db.append_message("s1", role="user", content="hi", asked_by="say:agenda", kind="agent")
    db.append_message("s1", role="user", content="cron", asked_by="cron:j1", kind="cron")

    rows = db.get_messages("s1")

    assert [r["kind"] for r in rows] == ["agent", "cron"]


def test_kind_defaults_to_null_for_older_writers(tmp_path):
    db = _db(tmp_path)
    db.create_session("s1", "desktop")
    db.append_message("s1", role="user", content="typed by owner", asked_by="owner")

    assert db.get_messages("s1")[0]["kind"] is None


def test_the_column_exists_on_an_old_database(tmp_path):
    """老库（没有 kind 列）打开一次就要被补上——不靠版本号门。"""
    import sqlite3

    db_path = tmp_path / "old.db"
    first = SessionDB(db_path=db_path)
    first.close()
    conn = sqlite3.connect(db_path)
    conn.execute("ALTER TABLE messages DROP COLUMN kind")
    conn.commit()
    conn.close()
    check = sqlite3.connect(db_path)
    assert "kind" not in [r[1] for r in check.execute("PRAGMA table_info(messages)")]
    check.close()

    db = SessionDB(db_path=db_path)

    assert "kind" in [r[1] for r in db._conn.execute("PRAGMA table_info(messages)")]
