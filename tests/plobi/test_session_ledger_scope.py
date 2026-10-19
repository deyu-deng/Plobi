"""缺口 B（WP-AGENT-SESSION-ATTRIBUTION 读侧聚合面收口）。

判据：单库总账（裁定 180）之后，聚合读面按 ``agent_id`` 行级圈定「这个
Agent 的」会话——等价迁移旧的一家一库读法；``agent_id=None`` = 全库
（L1 / flat 历史行为）。**删除面必须同刀收**：不圈就是删别人的空会话。
"""

import pytest

from plobi_state import SessionDB


@pytest.fixture
def db(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    # 三个 Agent 各自的会话 + 两条 L1（agent_id 空）。
    db.create_session("s-aura-1", source="cli", agent_id="aura")
    db.create_session("s-aura-empty", source="cli", agent_id="aura")
    db.create_session("s-kit-1", source="cli", agent_id="kit")
    db.create_session("s-kit-empty", source="cli", agent_id="kit")
    db.create_session("s-l1-1", source="cli")
    db.append_message("s-aura-1", role="user", content="aura 的问题")
    db.append_message("s-kit-1", role="user", content="kit 的问题")
    db.append_message("s-l1-1", role="user", content="L1 的问题")
    # 空会话要 ended 才算 empty（安全契约）。
    with db._lock:
        for sid in ("s-aura-empty", "s-kit-empty"):
            db._conn.execute(
                "UPDATE sessions SET ended_at = ? WHERE id = ?", (1_700_000_000, sid)
            )
        db._conn.commit()
    yield db
    db.close()


# ---------------------------------------------------------------------------
# 1. 读面：list / search / count / stats 的 agent_id 圈定
# ---------------------------------------------------------------------------


def test_list_sessions_rich_scopes_by_agent(db):
    ids = {s["id"] for s in db.list_sessions_rich(agent_id="aura", include_children=True)}
    assert ids == {"s-aura-1", "s-aura-empty"}
    # None = 全库（历史行为）。
    assert len(db.list_sessions_rich(include_children=True)) == 5


def test_search_sessions_by_id_scopes_by_agent(db):
    rows = db.search_sessions_by_id("s-", limit=20, agent_id="kit")
    assert {r["id"] for r in rows} <= {"s-kit-1", "s-kit-empty"}
    assert any(r["id"] == "s-kit-1" for r in rows)
    # 别人的 id 搜不出来。
    assert all(r["id"] != "s-aura-1" for r in rows)


def test_search_messages_scopes_by_agent(db):
    hits = db.search_messages(query="aura*", agent_id="aura")
    assert hits and all("aura" in h["snippet"].lower() for h in hits)
    kit_hits = db.search_messages(query="aura*", agent_id="kit")
    assert kit_hits == []


def test_count_and_message_count_scope_by_agent(db):
    assert db.count_empty_sessions(agent_id="aura") == 1
    assert db.count_empty_sessions() == 2
    assert db.message_count(agent_id="aura") == 1
    assert db.message_count() == 3
    assert db.session_count(agent_id="kit") == 2
    assert db.session_count() == 5


# ---------------------------------------------------------------------------
# 2. 删除面：删空会话绝不出圈
# ---------------------------------------------------------------------------


def test_delete_empty_sessions_scopes_by_agent(db):
    deleted = db.delete_empty_sessions(agent_id="aura")
    assert deleted == 1
    # kit 的空会话还在——不圈就是删别人的。
    assert db.count_empty_sessions(agent_id="kit") == 1
    assert db.count_empty_sessions() == 1


# ---------------------------------------------------------------------------
# 3. web_server 侧：家名 → 名册 name 的桥 + 总账单源
# ---------------------------------------------------------------------------


def test_agent_id_for_profile_maps_via_registry(tmp_path, monkeypatch):
    import plobi_cli.web_server as ws

    monkeypatch.setattr(Path_home := __import__("pathlib").Path, "home", lambda: tmp_path)
    base = tmp_path / ".plobi"
    (base / "plobi").mkdir(parents=True)
    monkeypatch.setenv("PLOBI_HOME", str(base))
    monkeypatch.setenv("PLOBI_PROJECTS_CONFIG", str(base / "plobi" / "projects.yaml"))
    import yaml
    from plobi.agents.registry import REGISTRY_VERSION

    (base / "plobi" / "projects.yaml").write_text(
        yaml.safe_dump(
            {
                "version": REGISTRY_VERSION,
                "agents": {"aura": {"name": "aura", "role": "l2_project", "profile": "l2-aura", "category": "projects"}},
            },
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    assert ws._agent_id_for_profile("l2-aura") == "aura"
    assert ws._agent_id_for_profile("l2-AURA") == "aura"  # 家名大小写不敏感
    assert ws._agent_id_for_profile("no-such-home") == ""
    assert ws._agent_id_for_profile(None) == ""


def test_open_session_db_for_profile_always_opens_the_single_ledger(tmp_path, monkeypatch):
    """家不再选库：named profile 也开总账（裁定 180 后只有这一份）。"""
    import plobi_cli.web_server as ws

    home = tmp_path / "home"
    profile = tmp_path / "profiles" / "l2-aura"
    profile.mkdir(parents=True)
    (profile / "state.db").write_bytes(b"stale per-profile db")
    monkeypatch.setenv("PLOBI_HOME", str(home))

    db = ws._open_session_db_for_profile("l2-aura")
    try:
        assert db.db_path != profile / "state.db"
    finally:
        db.close()
