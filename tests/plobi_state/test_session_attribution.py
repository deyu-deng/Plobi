"""`sessions.agent_id` —— session 的 Agent 归属（WP-AGENT-SESSION-ATTRIBUTION / 裁定 180）。

桌面一个后端一个库（SINGLE-GATEWAY）后，隔离从文件级（一家一库）改为**行级**。
这一格必须：写进去读得回、老库升级后存量行是空串（= L1，不是 NULL 也不是
某个 agent）、upsert 两个方向都不串味（bare 先到后补赢 / 已有归属不被空
调用覆盖）、热查询走索引。跨 profile 家的存量数据并入顶层库由一次性脚本
`scripts/plobi/migrate_session_attribution.py` 负责，不进运行时迁移链。
"""

import sqlite3

import pytest

from plobi_state import SCHEMA_VERSION, SessionDB


@pytest.fixture
def db(tmp_path):
    return SessionDB(tmp_path / "state.db")


def _agent_ids(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        return dict(
            conn.execute("SELECT id, agent_id FROM sessions").fetchall()
        )
    finally:
        conn.close()


def _columns(db_path, table="sessions"):
    conn = sqlite3.connect(str(db_path))
    try:
        return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    finally:
        conn.close()


class TestSchema:
    def test_column_present_on_fresh_db(self, db, tmp_path):
        assert "agent_id" in _columns(tmp_path / "state.db")

    def test_schema_version_is_21(self):
        assert SCHEMA_VERSION == 21

    def test_agent_recent_index_exists(self, db, tmp_path):
        conn = sqlite3.connect(str(tmp_path / "state.db"))
        try:
            names = [r[1] for r in conn.execute("PRAGMA index_list(sessions)")]
        finally:
            conn.close()
        assert any("agent_recent" in n for n in names), names

    def test_legacy_db_upgrades_with_empty_default(self, tmp_path):
        """旧库（v20，无 agent_id 列）打开后：列被加上、存量行是空串。

        空串 = L1（总秘书不是名册行）——不是 NULL（读路径 ``WHERE agent_id=?``
        对 NULL 的行为与空串不同，混用会漏行），也不是哪个 agent 的名。
        """
        path = tmp_path / "state.db"
        conn = sqlite3.connect(str(path))
        # 复刻一个 v20 库：无 agent_id 列、版本号钉 20、一行存量 session
        conn.execute(
            "CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT NOT NULL, "
            "user_id TEXT, session_key TEXT, chat_id TEXT, chat_type TEXT, "
            "thread_id TEXT, display_name TEXT, origin_json TEXT, "
            "expiry_finalized INTEGER DEFAULT 0, model TEXT, model_config TEXT, "
            "system_prompt TEXT, parent_session_id TEXT, started_at REAL NOT NULL, "
            "ended_at REAL, end_reason TEXT, message_count INTEGER DEFAULT 0, "
            "tool_call_count INTEGER DEFAULT 0, input_tokens INTEGER DEFAULT 0, "
            "output_tokens INTEGER DEFAULT 0, cache_read_tokens INTEGER DEFAULT 0, "
            "cache_write_tokens INTEGER DEFAULT 0, reasoning_tokens INTEGER DEFAULT 0, "
            "cwd TEXT, git_branch TEXT, git_repo_root TEXT, billing_provider TEXT, "
            "billing_base_url TEXT, billing_mode TEXT, estimated_cost_usd REAL, "
            "actual_cost_usd REAL, cost_status TEXT, cost_source TEXT, "
            "pricing_version TEXT, title TEXT, api_call_count INTEGER DEFAULT 0, "
            "handoff_state TEXT, handoff_platform TEXT, handoff_error TEXT, "
            "compression_failure_cooldown_until REAL, compression_failure_error TEXT, "
            "rewind_count INTEGER NOT NULL DEFAULT 0, archived INTEGER NOT NULL DEFAULT 0, "
            "FOREIGN KEY (parent_session_id) REFERENCES sessions(id))"
        )
        conn.execute(
            "CREATE TABLE schema_version (version INTEGER NOT NULL)"
        )
        conn.execute("INSERT INTO schema_version (version) VALUES (20)")
        conn.execute(
            "INSERT INTO sessions (id, source, started_at) VALUES ('legacy-1', 'cli', 1.0)"
        )
        conn.commit()
        conn.close()

        # 新代码打开同一文件——_reconcile_columns() 声明式加列
        db = SessionDB(path)
        assert "agent_id" in _columns(path)
        assert _agent_ids(path) == {"legacy-1": ""}

        # 版本号被推进（无数据迁移块，加列即全部）
        conn = sqlite3.connect(str(path))
        try:
            assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 21
        finally:
            conn.close()


class TestUpsertAttribution:
    def test_bare_row_then_agent_enrichment_wins(self, db, tmp_path):
        """网关先建 bare row（无身份），agent 后补——后者必须赢。

        列是 NOT NULL DEFAULT ''，裸 COALESCE 会让先到的空串永不让位；
        NULLIF(…, '') 把空串当"未归属"才补得进去。
        """
        db.create_session("s1", "gateway")
        db.create_session("s1", "cli", agent_id="Aura")
        assert _agent_ids(tmp_path / "state.db")["s1"] == "Aura"

    def test_existing_attribution_survives_bare_overwrite(self, db, tmp_path):
        """已有归属的 session，后续 bare 调用（usage 兜底之类）不许抹掉它。"""
        db.create_session("s2", "cli", agent_id="Aura")
        db.ensure_session("s2", "unknown")
        assert _agent_ids(tmp_path / "state.db")["s2"] == "Aura"

    def test_default_is_empty_l1(self, db, tmp_path):
        """不传 agent_id = L1（空串）——与 SINGLE-GATEWAY §3.3 同口径。"""
        db.create_session("s3", "cli")
        assert _agent_ids(tmp_path / "state.db")["s3"] == ""

    def test_none_is_coerced_to_empty(self, db, tmp_path):
        """None 不许进库（NOT NULL 列），静默转空串。"""
        db.create_session("s4", "cli", agent_id=None)
        assert _agent_ids(tmp_path / "state.db")["s4"] == ""


class TestReadPath:
    def test_query_by_agent_uses_index_shape(self, db, tmp_path):
        """读路径的形状（console 将按它改）：WHERE agent_id=? 取最近。

        进行中的会话 ended_at 为空，COALESCE 后天然排前——与
        idx_sessions_agent_recent 的表达式逐字对齐才走索引。
        """
        db.create_session("s-a1", "cli", agent_id="Aura")
        db.create_session("s-a2", "cli", agent_id="Aura")
        db.create_session("s-k1", "cli", agent_id="Kit")
        conn = sqlite3.connect(str(tmp_path / "state.db"))
        try:
            conn.execute("UPDATE sessions SET started_at = 2.0 WHERE id = 's-a1'")
            conn.execute("UPDATE sessions SET started_at = 3.0 WHERE id = 's-a2'")
            conn.execute("UPDATE sessions SET started_at = 1.0 WHERE id = 's-k1'")
            conn.commit()
            rows = conn.execute(
                "SELECT id FROM sessions WHERE agent_id = ? "
                "ORDER BY COALESCE(ended_at, started_at) DESC LIMIT 1",
                ("Aura",),
            ).fetchall()
            plan = conn.execute(
                "EXPLAIN QUERY PLAN SELECT id FROM sessions WHERE agent_id = ? "
                "ORDER BY COALESCE(ended_at, started_at) DESC LIMIT 1",
                ("Aura",),
            ).fetchall()
        finally:
            conn.close()
        assert rows[0][0] == "s-a2"
        assert any("agent_recent" in str(r) for r in plan), plan
