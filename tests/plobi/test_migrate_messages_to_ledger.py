"""`scripts/plobi/migrate_messages_to_ledger.py` 的钉子（裁定 180 收尾那一刀）。

背景：``migrate_session_attribution.py`` 只搬了 ``sessions`` 行与
``session_model_usage``，**没搬 messages**。读路径已全部切到单库总账之后，
那些历史对话打开是空的——这个脚本把它们并进来。

这份测试钉四件事：
1. **重新发号**：``messages.id`` 是 AUTOINCREMENT，照搬 id 会撞主键（``INSERT OR
   IGNORE`` 静默吃掉）⇒ 新行必须拿到**比总账现有最大值更大的新 id**，旧 id 不复用；
2. **报数要真**：``total_changes`` 会把 FTS 触发器的变更也数进去（现场 577 行报成
   30416 条）⇒ 写入数只认 ``messages`` 的行数差；
3. **可重复执行**：每库搬完写 ``state_meta`` 标记，第二次规划不再把它列进待搬；
4. **缺会话要报**：源库里有消息、但目标会话不在总账 ⇒ 计数报出来（不静默丢）。
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

from plobi_state import SessionDB

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "plobi" / "migrate_messages_to_ledger.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("plobi_migrate_messages_under_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# 老形状的源库：没有 asked_by（迁移前那一版），只留脚本与读面真正用到的列。
_OLD_SCHEMA = """
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT,
    timestamp REAL NOT NULL
);
"""


@pytest.fixture()
def M():
    return _load_script()


@pytest.fixture()
def homes(tmp_path, monkeypatch):
    """临时 PLOBI_HOME / PLOBI_STATE_HOME + 一个总账 + 一个分家库。"""
    home = tmp_path / "home"
    (home / "profiles" / "aura").mkdir(parents=True)
    monkeypatch.setenv("PLOBI_HOME", str(home))
    monkeypatch.setenv("PLOBI_STATE_HOME", str(home))
    return home


@pytest.fixture()
def ledger(homes):
    """总账：两个会话、三条消息（id 1..3，作为「已有号段」的基线）。"""
    db = SessionDB(db_path=homes / "state.db")
    try:
        db.create_session("s-aura", source="cli", agent_id="aura")
        db.create_session("s-other", source="cli", agent_id="kit")
        for i in range(3):
            db.append_message("s-aura", role="user", content=f"新世界第{i}条")
    finally:
        db.close()
    return homes / "state.db"


@pytest.fixture()
def source(homes):
    """分家库：老 schema，两条属于 s-aura（空世界之前的转录）＋一条孤儿会话。"""
    db_path = homes / "profiles" / "aura" / "state.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(_OLD_SCHEMA)
        conn.executemany(
            "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?,?,?,?)",
            [
                ("s-aura", "user", "旧世界第1条", 1000.0),
                ("s-aura", "assistant", "旧世界第2条", 1001.0),
                ("s-gone", "user", "会话已经不在总账里", 1002.0),
            ],
        )
        conn.commit()
    finally:
        conn.close()
    return db_path


def test_plan_reports_rows_columns_and_missing_sessions(M, ledger, source, homes):
    plans, _sessions, ledger_count = M._plan(ledger, M._sources(ledger))

    assert ledger_count == 3
    assert len(plans) == 1
    plan = plans[0]
    assert plan["name"] == "aura"
    assert plan["rows"] == 3
    assert plan["missing_session"] == 1  # s-gone 不在总账
    assert "asked_by" in plan["extra_cols"]  # 总账多这一列（源库老形状）
    assert plan["already"] == ""  # 还没搬过


def test_copy_rekeys_ids_and_reports_true_count(M, ledger, source, homes):
    plans, _sessions, _count = M._plan(ledger, M._sources(ledger))
    written = M._copy_one(ledger, plans[0])

    # ① 报数只认 messages 的行数差（不是 total_changes —— FTS 触发器会灌水）
    assert written == 3

    conn = sqlite3.connect(str(ledger))
    try:
        total = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        assert total == 6  # 3 条原有 + 3 条搬来

        # ② 重新发号：新行 id 全部大于旧最大值 3，旧 id 一个都没被复用成别人的话
        new_rows = conn.execute(
            "SELECT id, session_id, role, content, timestamp FROM messages "
            "WHERE id > 3 ORDER BY id"
        ).fetchall()
        assert [r[0] for r in new_rows] == [4, 5, 6]
        assert all(r[0] > 3 for r in new_rows)
        # ③ 会话归属与顺序（timestamp 保序）在
        assert [r[1] for r in new_rows] == ["s-aura", "s-aura", "s-gone"]
        assert [r[2] for r in new_rows] == ["user", "assistant", "user"]
        assert [r[4] for r in new_rows] == [1000.0, 1001.0, 1002.0]
        # ④ 标记写下了**真数**
        marker = conn.execute(
            "SELECT value FROM state_meta WHERE key = 'messages_migrated:aura'"
        ).fetchone()[0]
        assert marker == "3"
    finally:
        conn.close()


def test_second_plan_skips_an_already_migrated_db(M, ledger, source, homes):
    plans, _sessions, _count = M._plan(ledger, M._sources(ledger))
    M._copy_one(ledger, plans[0])

    replans, _s2, _c2 = M._plan(ledger, M._sources(ledger))
    assert replans[0]["already"] == "3"  # 已搬、真数 3
    # 再跑一次 --run 不会把他列进待搬（幂等）
    todo = [p for p in replans if not p["already"]]
    assert todo == []


def test_fts_index_gets_the_migrated_rows(M, ledger, source, homes):
    """普通 INSERT 会走 FTS 触发器 —— 搬过来的行要能搜到。"""
    plans, _sessions, _count = M._plan(ledger, M._sources(ledger))
    M._copy_one(ledger, plans[0])

    conn = sqlite3.connect(str(ledger))
    try:
        hit = conn.execute(
            "SELECT COUNT(*) FROM messages_fts_trigram WHERE messages_fts_trigram MATCH '旧世界'"
        ).fetchone()[0]
        assert hit >= 2
    finally:
        conn.close()
