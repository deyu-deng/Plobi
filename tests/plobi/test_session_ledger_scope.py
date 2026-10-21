"""缺口 B（WP-AGENT-SESSION-ATTRIBUTION 读侧聚合面收口）。

判据：单库总账（裁定 180）之后，聚合读面按 ``agent_id`` 行级圈定「这个
Agent 的」会话——等价迁移旧的一家一库读法；``agent_id=None`` = 全库
（L1 / flat 历史行为）。**删除面必须同刀收**：不圈就是删别人的空会话。
"""

import pytest
import yaml
from pathlib import Path

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


# ---------------------------------------------------------------------------
# 4. 面板作用域：Agent 视图读写名册行，全局表不动（全局真源 + per-agent 开关）
# ---------------------------------------------------------------------------


@pytest.fixture
def panel_scope(tmp_path, monkeypatch):
    """顶层家 + 名册一行 aura（profile l2-aura）。"""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    base = tmp_path / ".plobi"
    (base / "plobi").mkdir(parents=True)
    (base / "profiles" / "l2-aura").mkdir(parents=True)
    (base / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "skills": {"disabled": ["global-off"]},
                "platform_toolsets": {"cli": ["browser", "web"]},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PLOBI_HOME", str(base))
    monkeypatch.setenv("PLOBI_PROJECTS_CONFIG", str(base / "plobi" / "projects.yaml"))
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
    import plobi_cli.config as cfg

    cfg._LOAD_CONFIG_CACHE.clear()
    cfg._RAW_CONFIG_CACHE.clear()
    yield base
    cfg._LOAD_CONFIG_CACHE.clear()
    cfg._RAW_CONFIG_CACHE.clear()


def _roster_entry():
    from plobi.agents.registry import AgentRegistry

    return AgentRegistry.load().get("aura")


def test_skill_toggle_under_agent_scope_writes_roster_row(panel_scope):
    import asyncio
    import plobi_cli.web_server as ws

    body = ws.SkillToggle(name="my-skill", enabled=False)
    result = asyncio.run(ws.toggle_skill(body, profile="l2-aura"))
    assert result == {"ok": True, "name": "my-skill", "enabled": False}

    # 名册行记了偏好……
    assert "my-skill" in _roster_entry().disabled_skills
    # ……全局表一个字没动（直接读文件——load_config 会归一化 skills 节）。
    on_disk = yaml.safe_load((panel_scope / "config.yaml").read_text(encoding="utf-8"))
    assert "my-skill" not in (on_disk.get("skills") or {}).get("disabled", [])
    assert "global-off" in (on_disk.get("skills") or {}).get("disabled", [])

    # 开回来：从行里移除，全局表依旧。
    asyncio.run(ws.toggle_skill(ws.SkillToggle(name="my-skill", enabled=True), profile="l2-aura"))
    assert "my-skill" not in _roster_entry().disabled_skills


def test_toolset_toggle_under_agent_scope_subtracts_from_whitelist(panel_scope):
    import asyncio
    import plobi_cli.web_server as ws

    # 白名单里有哪些是真实可配置 toolset 就用哪些（第一个关掉，第二个留开）。
    all_rows = asyncio.run(ws.get_toolsets(profile=None))
    on_rows = [r for r in all_rows if r["enabled"]]
    assert len(on_rows) >= 2, "fixture whitelist should enable >=2 configurable toolsets"
    target, untouched = on_rows[0]["name"], on_rows[1]["name"]

    result = asyncio.run(ws.toggle_toolset(target, ws.ToolsetToggle(enabled=False), profile="l2-aura"))
    assert result == {"ok": True, "name": target, "enabled": False}
    assert target in _roster_entry().disabled_toolsets

    # 全局白名单没动（直接读文件）。
    on_disk = yaml.safe_load((panel_scope / "config.yaml").read_text(encoding="utf-8"))
    assert target in (on_disk.get("platform_toolsets") or {}).get("cli", [])

    # GET 视图：目标对这个 Agent 是关的，别的照旧；flat 视图仍是开的。
    rows = {r["name"]: r for r in asyncio.run(ws.get_toolsets(profile="l2-aura"))}
    assert rows[target]["enabled"] is False
    assert rows[untouched]["enabled"] is True
    flat = {r["name"]: r for r in asyncio.run(ws.get_toolsets(profile=None))}
    assert flat[target]["enabled"] is True


# ---------------------------------------------------------------------------
# 5. 「这个 Agent 的会话」唯一判据（2026-10-21，say 全落进点不回来的会话）
#
# 病根是三处各判一次：say 的投递按 started_at 取最新（不看来源）、console
# overview 按最近活跃取（不看归档/来源/子会话）、桌面侧栏读的是**分家库**
# （profile 退役时漏改的端点）——于是消息落进的会话 ≠ 桌面打开的会话，
# 用户点开那个 Agent 只看到一张 fresh draft。修法是把判据收成
# SessionDB.latest_conversational_session_for_agent 一个方法，三处都调它。
# ---------------------------------------------------------------------------


@pytest.fixture
def mixed_ledger(tmp_path, monkeypatch):
    """总账 + 名册一行 aura（家 l2-aura）+ 那个家的目录（端点校验用）。

    aura 的会话故意混成「最近活跃的一屏」不是「最新建的一屏」：
      s-chat      cli       交谈类   旧                 ← 唯一该被选中的
      s-cron      cron      调度器   较新
      s-archived  cli       交谈类   最新，但已归档
      s-child     subagent  跑腿     最新，且是子会话
    """
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    base = tmp_path / "home"
    (base / "plobi").mkdir(parents=True)
    (base / "profiles" / "l2-aura").mkdir(parents=True)
    # 分家库留一份**带毒**的旧库：端点若还按家选库就会读到它（修前的病根）。
    (base / "profiles" / "l2-aura" / "state.db").write_bytes(b"stale per-profile db")
    monkeypatch.setenv("PLOBI_HOME", str(base))
    monkeypatch.setenv("PLOBI_STATE_HOME", str(base))
    monkeypatch.setenv("PLOBI_PROJECTS_CONFIG", str(base / "plobi" / "projects.yaml"))

    from plobi.agents.registry import REGISTRY_VERSION

    (base / "plobi" / "projects.yaml").write_text(
        yaml.safe_dump(
            {
                "version": REGISTRY_VERSION,
                "agents": {
                    "aura": {
                        "name": "aura",
                        "role": "l2_project",
                        "profile": "l2-aura",
                        "category": "projects",
                    }
                },
            },
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    db = SessionDB(db_path=base / "state.db")
    db.create_session("s-chat", source="cli", agent_id="aura")
    db.create_session("s-cron", source="cron", agent_id="aura")
    db.create_session("s-archived", source="cli", agent_id="aura")
    db.create_session("s-child", source="subagent", agent_id="aura", parent_session_id="s-chat")
    db.append_message("s-chat", role="user", content="上一屏的对话")
    with db._lock:
        db._conn.execute("UPDATE sessions SET started_at = ?, ended_at = ? WHERE id = 's-chat'", (1000, 1100))
        db._conn.execute("UPDATE sessions SET started_at = ?, ended_at = ? WHERE id = 's-cron'", (2000, 2100))
        db._conn.execute("UPDATE sessions SET started_at = ?, archived = 1 WHERE id = 's-archived'", (3000,))
        db._conn.execute("UPDATE sessions SET started_at = ?, ended_at = ? WHERE id = 's-child'", (4000, 4100))
        db._conn.commit()
    yield base
    db.close()


def test_latest_conversational_session_skips_cron_archived_and_children(mixed_ledger):
    """最近活跃里排前的是 cron / 归档 / 跑腿——三个都不能当「这个 Agent 的会话」。"""
    db = SessionDB(db_path=mixed_ledger / "state.db", read_only=True)
    try:
        assert db.latest_conversational_session_for_agent("aura") == "s-chat"
        # 别的 Agent 问不到 aura 的；空 agent_id（L1）没有「这一屏」。
        assert db.latest_conversational_session_for_agent("kit") is None
        assert db.latest_conversational_session_for_agent("") is None
    finally:
        db.close()


def test_console_overview_uses_the_same_rule(mixed_ledger):
    """overview.sessionId 与 say 落点同源——这一条不变式被破坏过整整一天。"""
    from plobi.console import router as console_router

    assert (
        console_router._latest_session_for_agent("aura", db_path=mixed_ledger / "state.db")
        == "s-chat"
    )


def test_say_delivers_into_the_session_the_desktop_opens(mixed_ledger):
    """端到端：say 落进的会话，正是 overview 会指给桌面、侧栏会列出的那个。"""
    import importlib.util
    import sys
    import types

    plugin_dir = Path(__file__).resolve().parents[2] / "plugins" / "plobi-north-star"
    pkg_name = "plobi_test_say_landing_ns_pkg"
    if pkg_name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            pkg_name, plugin_dir / "__init__.py", submodule_search_locations=[str(plugin_dir)]
        )
        module = importlib.util.module_from_spec(spec)
        module.__package__ = pkg_name
        module.__path__ = [str(plugin_dir)]
        sys.modules[pkg_name] = module
        spec.loader.exec_module(module)
    channel = importlib.import_module(f"{pkg_name}.channel")

    session_id, _message_id = channel._deliver(
        types.SimpleNamespace(name="aura"), "明天的会挪到周四", sender="L1"
    )
    assert session_id == "s-chat", "say 必须落进桌面会打开的那一屏，不是 cron/归档/跑腿"

    db = SessionDB(db_path=mixed_ledger / "state.db", read_only=True)
    try:
        row = db._conn.execute(
            "SELECT role, content FROM messages WHERE session_id = 's-chat' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row[0] == "user" and "明天的会挪到周四" in row[1]
    finally:
        db.close()


def test_profiles_sessions_endpoint_reads_the_ledger_not_the_home(mixed_ledger):
    """桌面侧栏那个端点：家名只圈行，不选库（修前它读分家库 ⇒ 永远空）。"""
    import plobi_cli.web_server as ws

    result = ws.get_profiles_sessions(profile="l2-aura", min_messages=1, limit=50)
    ids = {s["id"] for s in result["sessions"]}
    assert ids == {"s-chat"}
    row = result["sessions"][0]
    assert row["profile"] == "l2-aura"  # 家名照打（桌面那几处认它）
    assert result["profile_totals"] == {"l2-aura": 1}
    # cron 转录不在侧栏可见集里（ exclude_sources 那一等的另一半：来源圈定）。
    cron_view = ws.get_profiles_sessions(profile="l2-aura", source="cron", limit=50)
    assert {s["id"] for s in cron_view["sessions"]} == {"s-cron"}


def test_profiles_sessions_all_mode_tags_each_row_by_its_agent(mixed_ledger):
    """'all' 读全库，每行按归属 Agent 打家名；L1 行 = default。"""
    import plobi_cli.web_server as ws

    db = SessionDB(db_path=mixed_ledger / "state.db")
    try:
        db.create_session("s-l1", source="cli")
        db.append_message("s-l1", role="user", content="L1 自己说的话")
        db.create_session("s-said", source="agent_message", agent_id="aura")
        db.append_message("s-said", role="user", content="另一条 say 落进来的")
    finally:
        db.close()

    result = ws.get_profiles_sessions(profile="all", min_messages=1, limit=50)
    by_id = {s["id"]: s for s in result["sessions"]}
    assert by_id["s-chat"]["profile"] == "l2-aura"
    assert by_id["s-said"]["profile"] == "l2-aura"  # say 开的会话也归这个 Agent
    assert by_id["s-l1"]["profile"] == "default"
    assert by_id["s-l1"]["is_default_profile"] is True
    assert result["profile_totals"]["l2-aura"] == 2
    assert result["profile_totals"]["default"] == 1
