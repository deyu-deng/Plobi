"""WP-BE-1 — §5 console read API (GET /api/agents, subagents, overview).

Shapes are asserted field-for-field against ``console/types.ts``: ``Agent`` is
exactly ``{id, name, status, model, todayCalls, profile}`` and ``AgentOverview`` is
exactly ``{agent, sessionId, todayCostUsd, todayTokens}``. A field added here
without a matching contract change is a bug, not an improvement.

The app under test is a minimal FastAPI instance mounting only the console
router — importing the full ``plobi_cli.web_server`` would drag in its
lifespan and background services for what is a routing/shape test. One test at
the bottom asserts the real server actually mounts these paths, so a missing
``include_router`` fails here rather than in the desktop app.
"""

from __future__ import annotations

import os
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plobi.agents.registry import AgentEntry, AgentRegistry
import plobi.console.router as console_router  # module, not the APIRouter re-export
from plobi.delegation.tracker import SubagentTracker, set_tracker


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(console_router.router, prefix="/api")
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    """Fresh registry + tracker per test; nothing leaks into the real home."""
    monkeypatch.delenv("PLOBI_PROJECTS_CONFIG", raising=False)
    monkeypatch.setattr("plobi.agents.registry.default_path", lambda: tmp_path / "projects.yaml")
    # R-013 扩写（2026-10-06）：建 project 分身现在要在 Mind 立项（Vault/projects/<名>
    # + 三件套）。MIND_ROOT 指到 tmp 里一个**存在**的假库（resolve_root 只认存在的目录），
    # 否则每个 project 用例都会往真的脑仓里写目录。
    (tmp_path / "mind").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MIND_ROOT", str(tmp_path / "mind"))
    # 「家落地了没有」（registry._profile_landed → plobi_cli.profiles.profile_exists）
    # 在这个隔离环境里必须可控：真家是共享的，读它等于把本用例的读数交给那台机器。
    # 只承认 l2-agenda 有家——它就是被 spawn 过的那一行（AGENDA 的形状）。
    import plobi_cli.profiles as _profiles

    monkeypatch.setattr(
        _profiles, "profile_exists", lambda name: str(name).strip().lower() == "l2-agenda", raising=False
    )
    set_registry = AgentRegistry(path=tmp_path / "projects.yaml")
    console_router.set_registry(set_registry)
    set_tracker(SubagentTracker())
    yield set_registry
    console_router.set_registry(None)
    set_tracker(None)


def register(registry: AgentRegistry, **entries: dict) -> AgentRegistry:
    for name, raw in entries.items():
        registry.upsert(AgentEntry.from_dict(name, raw))
    return registry


AGENDA = {"role": "l2_agenda", "profile": "l2-agenda", "description": "日程秘书"}
SECRETARY = {"role": "l1_secretary", "profile": "master"}


# --------------------------------------------------------------------------- #
# GET /api/agents
# --------------------------------------------------------------------------- #


def test_agents_empty_registry_is_ok_envelope(client):
    """No registrations → empty rail, still a §5 envelope (not 500 / not null)."""
    body = client.get("/api/agents").json()
    assert body == {"ok": True, "data": []}


def test_agents_shape_matches_typescript_contract(client, _isolated_state):
    register(_isolated_state, agenda=AGENDA)
    row = client.get("/api/agents").json()["data"][0]
    # 2026-10-19 起行上带三份 per-agent 工具黑名单（全局真源 + per-agent 开关）；
    # icon/color 是 WP-AGENT-CREATE-UPGRADE 随行透出的（契约债一并收）；
    # askable 是 R-072 嘴的判据，navigable 是桌面点击的判据（两回事，见
    # registry.agent_navigability 的 docstring）。
    assert set(row) == {
        "id", "name", "status", "model", "todayCalls", "profile", "category", "mindSubtree",
        "icon", "color",
        "disabledSkills", "disabledToolsets", "disabledMcpServers",
        "askable", "notAskableReason", "navigable",
        # WP-AGENT-MODEL-PICKER-MOUNT 第一档：这一格恒在（bool），没覆盖就是 False
        # —— 前端要靠「缺键」分别「没读数」与「没覆盖」，所以不许被 None 剥掉。
        "modelOverride",
        # 2026-10-21：「三个点 → 编辑」要看/改工作目录，于是它随行透出
        # （以前只在 overview 上，菜单想显示它得再打一次 overview）。
        "projectPath", "projectPaths", "working",
    }
    assert row["id"] == "agenda"
    assert row["name"] == "日程秘书"
    assert row["status"] == "idle"  # no L3 children in flight
    assert row["model"] == "aigw/workbuddy/deepseek-chat"  # ADR-0011 default route
    assert row["todayCalls"] == 0
    assert row["profile"] == "l2-agenda"
    # 日程秘书走 secretary_ask，所以 askable=false——但左栏完全点得动。
    # 这正是 askable 与 navigable 必须分成两个字段的原因。
    assert row["askable"] is False
    assert row["navigable"] is True
    # None 的键在 agent_row 末尾被省略（与 model?/actualModel? 同一口径）
    assert "notNavigableReason" not in row
    # R-013 扩写：行上必带 mind_subtree（没挂是空串），左栏才辨得出同一项目登记了两条。
    assert row["mindSubtree"] == ""


def test_agents_excludes_l1_secretary(client, _isolated_state):
    """§3.1: the left rail lists L2s; L1 is the shell, not a row."""
    register(_isolated_state, agenda=AGENDA, secretary=SECRETARY)
    ids = [row["id"] for row in client.get("/api/agents").json()["data"]]
    assert ids == ["agenda"]


def test_agents_name_falls_back_to_entry_name(client, _isolated_state):
    register(_isolated_state, bare={"role": "l2_project"})
    assert client.get("/api/agents").json()["data"][0]["name"] == "bare"


def test_agents_name_ignores_pipeline_description_slogan(client, _isolated_state):
    """QA P0-2: description slogans must not become the AGENTS rail label."""
    register(
        _isolated_state,
        agenda={
            "role": "l2_agenda",
            "profile": "l2-agenda",
            "description": "日程采集 -> SQLite -> 看板 -> 钉钉 闭环",
        },
    )
    assert client.get("/api/agents").json()["data"][0]["name"] == "日程秘书"


def test_agents_name_never_uses_short_description(client, _isolated_state):
    """裁定 13: even a short non-slogan description is not the rail label."""
    register(
        _isolated_state,
        custom={
            "role": "l2_project",
            "description": "项目助理",
        },
    )
    assert client.get("/api/agents").json()["data"][0]["name"] == "custom"


def test_agents_honours_registry_model_override(client, _isolated_state):
    register(
        _isolated_state,
        custom={"role": "l2_project", "provider": "zhipu", "model": "glm-4.6"},
    )
    row = client.get("/api/agents").json()["data"][0]
    assert row["model"] == "zhipu/glm-4.6"


# --------------------------------------------------------------------------- #
# POST /api/agents (WP-L2-BE — human L2 配备)
# --------------------------------------------------------------------------- #


def _stub_spawn(registry, monkeypatch):
    calls: list[tuple] = []

    def fake_spawn(name, *, clone_from=None, write_config=True):
        calls.append((name, clone_from))
        return {"name": name, "clone_from": clone_from}

    monkeypatch.setattr(registry, "spawn", fake_spawn)
    return calls


def test_post_l2_project_appears_in_list(client, _isolated_state, monkeypatch):
    """Human-created project L2 is saved and then visible on GET /api/agents."""
    calls = _stub_spawn(_isolated_state, monkeypatch)
    response = client.post(
        "/api/agents", json={"id": "plobi-code", "category": "projects"}
    )
    print("PROBE-BODY:", response.status_code, response.text[:400])
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["id"] == "plobi-code"
    assert body["data"]["category"] == "projects"
    # 2026-10-19 起行上带三份 per-agent 工具黑名单（全局真源 + per-agent 开关）；
    # icon/color 是 WP-AGENT-CREATE-UPGRADE 随行透出的（测试这条契约债一并收）；
    # askable/navigable 是 R-072 的两把判据（嘴 / 点击）。
    assert set(body["data"]) == {
        "id", "name", "status", "model", "todayCalls", "profile", "category", "mindSubtree",
        "icon", "color",
        "disabledSkills", "disabledToolsets", "disabledMcpServers",
        "askable", "notAskableReason", "navigable", "notNavigableReason",
        "modelOverride",
        "projectPath", "projectPaths", "working",
    }
    # 这一条刚登记、spawn 被 stub 掉了 → 家还没落地 → 左栏据此先不点它，
    # 并把原因原样透出去（而不是让用户点进去撞 spawn 失败）。
    assert body["data"]["navigable"] is False
    assert "还没在这台机器上落地" in body["data"]["notNavigableReason"]
    ids = [row["id"] for row in client.get("/api/agents").json()["data"]]
    assert "plobi-code" in ids
    assert _isolated_state.get("plobi-code").role == "l2_project"
    assert _isolated_state.get("plobi-code").category == "projects"
    assert calls == [("plobi-code", None)]


def test_post_rejects_l1_secretary(client, _isolated_state, monkeypatch):
    calls = _stub_spawn(_isolated_state, monkeypatch)
    response = client.post(
        "/api/agents", json={"id": "boss", "role": "l1_secretary", "category": "butler"}
    )
    assert response.status_code == 400
    assert response.json()["ok"] is False
    assert "error" in response.json()
    assert calls == []
    assert client.get("/api/agents").json()["data"] == []


def test_post_missing_id_is_400(client):
    response = client.post("/api/agents", json={"name": "nope", "category": "butler"})
    assert response.status_code == 400
    assert response.json()["ok"] is False
    assert "error" in response.json()


def test_post_invalid_id_is_400(client):
    response = client.post(
        "/api/agents", json={"id": "has space", "category": "butler"}
    )
    assert response.status_code == 400
    assert response.json()["ok"] is False
    assert "error" in response.json()


# --------------------------------------------------------------------------- #
# R-013: creating an agent must never fabricate a directory this platform
# cannot mean. Windows-era rows (``D:\Cloud\Projects\<name>``, still seeded by
# registry.DEFAULT_CLOUD_PROJECTS_ROOT) survive in projects.yaml, and on POSIX a
# backslash is a legal filename character — so a naive mkdir drops ONE junk dir
# literally named ``D:\Cloud\Projects\...`` into the server's cwd.
# --------------------------------------------------------------------------- #


WINDOWS_BINDING = r"D:\Cloud\Projects\Gone-Project"


@pytest.mark.skipif(os.name == "nt", reason="a D:\\ binding is legitimate on Windows")
def test_post_windows_project_path_creates_no_directory(
    client, _isolated_state, monkeypatch, tmp_path
):
    """Stale Windows binding → record saved, create succeeds, NOTHING on disk."""
    calls = _stub_spawn(_isolated_state, monkeypatch)
    # Stand-ins for the server cwd; the registry yaml lives in tmp_path itself.
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    response = client.post(
        "/api/agents",
        json={"id": "gone", "category": "projects", "projectPath": WINDOWS_BINDING},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert calls == [("gone", None)]
    assert _isolated_state.get("gone") is not None
    # The cwd is untouched, and specifically no literal backslash artefact.
    assert [p.name for p in cwd.iterdir()] == []
    assert [p.name for p in Path.cwd().iterdir() if "\\" in p.name] == []


def test_post_missing_posix_project_path_is_created(
    client, _isolated_state, monkeypatch, tmp_path
):
    """R-013 preserved: a normal, platform-meaningful binding still gets made."""
    _stub_spawn(_isolated_state, monkeypatch)
    target = tmp_path / "Projects" / "Aura"

    response = client.post(
        "/api/agents",
        json={"id": "aura", "category": "projects", "projectPath": str(target)},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert target.is_dir()


def test_post_missing_category_is_400(client, _isolated_state, monkeypatch):
    """R-012: category is now required — a bare id is rejected."""
    _stub_spawn(_isolated_state, monkeypatch)
    response = client.post("/api/agents", json={"id": "no-cat"})
    assert response.status_code == 400
    assert response.json()["ok"] is False
    assert "category" in response.json()["error"]


def test_post_invalid_category_is_400(client, _isolated_state, monkeypatch):
    _stub_spawn(_isolated_state, monkeypatch)
    response = client.post(
        "/api/agents", json={"id": "bad-cat", "category": "personal"}
    )
    assert response.status_code == 400
    assert response.json()["ok"] is False
    assert "category" in response.json()["error"]


def test_post_existing_agenda_does_not_spawn_second(client, _isolated_state, monkeypatch):
    """S2: an id that is already the agenda L2 is returned as-is, no respawn."""
    register(_isolated_state, agenda=AGENDA)
    calls = _stub_spawn(_isolated_state, monkeypatch)
    response = client.post(
        "/api/agents",
        json={"id": "agenda", "role": "l2_agenda", "category": "butler"},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["data"]["id"] == "agenda"
    assert calls == []


def test_post_second_agenda_id_is_409(client, _isolated_state, monkeypatch):
    register(_isolated_state, agenda=AGENDA)
    calls = _stub_spawn(_isolated_state, monkeypatch)
    response = client.post(
        "/api/agents",
        json={"id": "agenda-2", "role": "l2_agenda", "category": "butler"},
    )
    assert response.status_code == 409
    assert response.json()["ok"] is False
    assert calls == []
    ids = [row["id"] for row in client.get("/api/agents").json()["data"]]
    assert ids == ["agenda"]


# --------------------------------------------------------------------------- #
# status derivation from live L3 children (B4 tracker)
# --------------------------------------------------------------------------- #


def test_status_reflects_working_child(client, _isolated_state):
    register(_isolated_state, agenda=AGENDA)
    tracker = SubagentTracker()
    set_tracker(tracker)
    tracker.on_start(
        child_session_id="s1", parent_session_id="p1", child_goal="整理周报", agent_id="agenda"
    )
    assert client.get("/api/agents").json()["data"][0]["status"] == "working"


def test_status_error_outranks_working(client, _isolated_state):
    register(_isolated_state, agenda=AGENDA)
    tracker = SubagentTracker()
    set_tracker(tracker)
    tracker.on_start(child_session_id="s1", agent_id="agenda")
    tracker.on_stop(child_session_id="s1", child_status="timeout")
    tracker.on_start(child_session_id="s2", agent_id="agenda")
    assert client.get("/api/agents").json()["data"][0]["status"] == "error"


def test_status_is_scoped_per_agent(client, _isolated_state):
    register(_isolated_state, agenda=AGENDA, other={"role": "l2_project"})
    tracker = SubagentTracker()
    set_tracker(tracker)
    tracker.on_start(child_session_id="s1", agent_id="agenda")
    by_id = {row["id"]: row["status"] for row in client.get("/api/agents").json()["data"]}
    assert by_id == {"agenda": "working", "other": "idle"}


# --------------------------------------------------------------------------- #
# GET /api/agents/:id/subagents
# --------------------------------------------------------------------------- #


def test_subagents_shape_is_passthrough(client, _isolated_state):
    register(_isolated_state, agenda=AGENDA)
    tracker = SubagentTracker()
    set_tracker(tracker)
    tracker.on_start(
        child_session_id="s1", parent_session_id="p1", child_goal="整理周报", agent_id="agenda"
    )
    row = client.get("/api/agents/agenda/subagents").json()["data"][0]
    assert set(row) == {"id", "name", "status"}
    assert row["status"] == "working"


def test_subagents_empty_when_idle(client, _isolated_state):
    register(_isolated_state, agenda=AGENDA)
    body = client.get("/api/agents/agenda/subagents").json()
    assert body == {"ok": True, "data": []}


def test_unknown_agent_is_404_envelope(client, _isolated_state):
    response = client.get("/api/agents/nope/subagents")
    assert response.status_code == 404
    assert response.json()["ok"] is False
    assert "error" in response.json()


# --------------------------------------------------------------------------- #
# GET /api/agents/:id/overview
# --------------------------------------------------------------------------- #


def test_overview_shape(client, _isolated_state):
    register(_isolated_state, agenda=AGENDA)
    body = client.get("/api/agents/agenda/overview").json()
    assert body["ok"] is True
    # WP-AGENT-CREATE-UPGRADE：projectPaths 是 10-16 加的附加字段（完整绑定清单，
    # 主文件夹在前）；projectPath 保留为主文件夹，旧消费方不变。
    assert set(body["data"]) == {
        "agent",
        "sessionId",
        "todayCostUsd",
        "todayTokens",
        "projectPath",
        "projectPaths",
    }
    assert body["data"]["agent"]["id"] == "agenda"
    assert isinstance(body["data"]["sessionId"], str)
    assert body["data"]["todayCostUsd"] == 0
    assert body["data"]["todayTokens"] == 0


def test_overview_404_for_unknown_agent(client, _isolated_state):
    response = client.get("/api/agents/nope/overview")
    assert response.status_code == 404
    assert response.json()["ok"] is False


def test_overview_404_for_l1_secretary(client, _isolated_state):
    """L1 is not an L2 row, so it has no S2 workbench card either."""
    register(_isolated_state, secretary=SECRETARY)
    assert client.get("/api/agents/secretary/overview").status_code == 404


# --------------------------------------------------------------------------- #
# session attribution (WP-AGENT-SESSION-ATTRIBUTION / 裁定 180：单库 + agent_id)
# --------------------------------------------------------------------------- #


def _attributed_db(tmp_path, monkeypatch, rows):
    """建一个真实 schema 的 state.db 并把 default_db_path 指过去。

    rows = [(session_id, agent_id, cost, calls)]；顺带写 usage 行让 todayCalls/
    todayUsd 有数可聚。这是单库后所有读侧测试的共同底座——身份在行上
    （sessions.agent_id），不在文件路径上。
    """
    import sqlite3

    from plobi_state import SessionDB

    db = tmp_path / "state.db"
    store = SessionDB(db_path=db)
    stamp = (datetime.now() - timedelta(hours=1)).timestamp()
    for sid, agent_id, cost, calls in rows:
        store.create_session(sid, source="cli", agent_id=agent_id)
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO session_model_usage (session_id, model, api_call_count, "
            "estimated_cost_usd, first_seen, last_seen) VALUES (?, 'm', ?, ?, ?, ?)",
            (sid, calls, cost, stamp, stamp),
        )
        conn.commit()
        conn.close()
    store.close()
    monkeypatch.setattr(console_router, "default_db_path", lambda: db)
    return db


def test_overview_session_id_is_latest_of_agent(client, _isolated_state, monkeypatch, tmp_path):
    """裁定 180：sessionId = 该 agent 在单库里最近活跃的会话（agent_id 行级）。"""
    register(_isolated_state, agenda=AGENDA)
    _attributed_db(
        tmp_path,
        monkeypatch,
        [("s-old", "agenda", 0.1, 1), ("s-new", "agenda", 0.2, 1), ("s-other", "Kit", 9.9, 1)],
    )
    body = client.get("/api/agents/agenda/overview").json()
    assert body["ok"] is True
    assert body["data"]["sessionId"] in ("s-old", "s-new")


def test_session_attribution_is_row_level(client, _isolated_state, monkeypatch, tmp_path):
    """归属按 sessions.agent_id：agenda 的会话不被 Kit 的查询看见（单库不串味）。"""
    register(_isolated_state, agenda=AGENDA, kit={"role": "l2_project", "profile": "kit"})
    _attributed_db(
        tmp_path,
        monkeypatch,
        [("s-agenda", "agenda", 0.5, 2), ("s-kit", "Kit", 0.3, 1)],
    )
    assert console_router.sessions_for_agent("agenda") == ["s-agenda"]
    assert console_router.sessions_for_agent("Kit") == ["s-kit"]
    assert console_router.sessions_for_agent("Nuclide") == []


def test_latest_session_missing_db_is_empty(tmp_path, monkeypatch):
    """顶层无 state.db → 诚实空串（不建库、不伪造）。"""
    monkeypatch.setattr(console_router, "default_db_path", lambda: tmp_path / "nope.db")
    assert console_router._latest_session_for_agent("agenda") == ""


def test_data_row_without_sessions_is_empty(client, _isolated_state, monkeypatch, tmp_path):
    """数据条目（l2_course）不再有跨库解析那条路（单库后没有 500 的面了）。

    库里没有它的行就是空——以前靠 profile_name 空串早退防 500，现在结构性
    保证：查询只碰 agent_id 列，永不碰 profile 解析器。
    """
    register(_isolated_state, physics={"role": "l2_course", "category": "courses"})
    _attributed_db(tmp_path, monkeypatch, [("s-a", "agenda", 0.1, 1)])
    assert console_router.sessions_for_agent("physics") == []


def test_usage_follows_session_attribution(client, _isolated_state, monkeypatch, tmp_path):
    """todayCalls 走 归属 session → session_model_usage 聚合（真实 SQL，单库）。"""
    register(_isolated_state, agenda=AGENDA)
    _attributed_db(tmp_path, monkeypatch, [("sess-latest", "agenda", 0.5, 4)])

    row = client.get("/api/agents").json()["data"][0]
    assert row["todayCalls"] == 4


def test_the_surface_reads_the_leg_that_actually_ran(tmp_path, client, _isolated_state, monkeypatch):
    """R-092 面上那半（Code `305ad585` 撤掉切换通知之后必须有东西可读）：
    「当前是哪条腿」只认 ``session_model_usage``，取 ``last_seen`` **最大**那一行——
    关系判据，不是"第一行"也不是配置里那条。``model_for()`` 给的是**打算走**哪条，
    这一格给的是**刚才真走过**哪条，两个键必须同时在场、不许互相冒充。"""
    register(_isolated_state, agenda=AGENDA)
    db = tmp_path / "state.db"
    _usage_db(db, [
        ("sess-latest", "workbuddy/hy3", 3, 10, 5, 0, 0, 0, 0.0, 100.0, 200.0),
        ("sess-latest", "zai/glm-5.3", 1, 2, 1, 0, 0, 0, 0.0, 300.0, 900.0),  # 最新走过
        ("sess-other", "workbuddy/hy3-x", 2, 4, 2, 0, 0, 0, 0.0, 50.0, 60.0),
    ])
    monkeypatch.setattr(console_router, "default_db_path", lambda: db)

    leg = console_router.leg_for_sessions(["sess-latest", "sess-other"], db_path=db)
    assert leg is not None and leg["model"] == "zai/glm-5.3", leg
    assert console_router.leg_for_sessions([]) is None
    assert console_router.leg_for_sessions(["sess-none"]) is None


def test_agent_rows_read_their_own_rows_under_single_backend(
    client, _isolated_state, monkeypatch, tmp_path
):
    """单库后每行读自己 agent_id 的行，不串账——这正是最可能的静默回归。

    旧形状是「两个 agent、两个库」（池后端时代）；裁定 180 后是**一个库、
    两个 agent_id**：任何一边读到对面的账，这条用例就红。
    """
    register(
        _isolated_state,
        agenda=AGENDA,
        physics={"role": "l2_project", "profile": "l2-physics", "category": "courses"},
    )
    _attributed_db(
        tmp_path,
        monkeypatch,
        [
            ("sess-agenda", "agenda", 0.5, 4),
            ("sess-physics", "physics", 0.2, 2),
        ],
    )

    rows = {row["id"]: row for row in client.get("/api/agents").json()["data"]}
    assert rows["agenda"]["todayCalls"] == 4
    assert rows["physics"]["todayCalls"] == 2


# --------------------------------------------------------------------------- #
# usage aggregation (real source: state.db session_model_usage)
# --------------------------------------------------------------------------- #


def _usage_db(path, rows):
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE session_model_usage (session_id TEXT, model TEXT, "
        "api_call_count INTEGER, input_tokens INTEGER, output_tokens INTEGER, "
        "cache_read_tokens INTEGER, cache_write_tokens INTEGER, "
        "reasoning_tokens INTEGER, estimated_cost_usd REAL, "
        "first_seen REAL, last_seen REAL)"
    )
    conn.executemany(
        "INSERT INTO session_model_usage VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows
    )
    conn.commit()
    conn.close()


def _row(session_id, calls, tokens, cost, *, hours_ago=1):
    stamp = (datetime.now() - timedelta(hours=hours_ago)).timestamp()
    return (session_id, "deepseek-chat", calls, tokens, 0, 0, 0, 0, cost, stamp, stamp)


def test_usage_sums_only_today_and_only_asked_sessions(tmp_path):
    db = tmp_path / "state.db"
    _usage_db(
        db,
        [
            _row("s-in", 3, 100, 0.5),
            _row("s-other", 99, 999, 9.9),  # different agent
            _row("s-old", 7, 700, 7.0, hours_ago=48),  # yesterday
        ],
    )
    totals = console_router.totals_for_sessions(["s-in"], db_path=db)
    assert (totals.calls, totals.tokens, round(totals.cost_usd, 2)) == (3, 100, 0.5)


def test_usage_empty_sessions_is_zero_without_touching_db(tmp_path):
    totals = console_router.totals_for_sessions([], db_path=tmp_path / "missing.db")
    assert totals == console_router.UsageTotals()


def test_usage_missing_db_does_not_raise(tmp_path):
    totals = console_router.totals_for_sessions(["s1"], db_path=tmp_path / "nope.db")
    assert totals == console_router.UsageTotals()


def test_usage_day_bounds_cover_local_calendar_day():
    start, end = console_router.day_bounds(time.time())
    assert start <= end
    assert datetime.fromtimestamp(start).hour == 0


# --------------------------------------------------------------------------- #
# GET /api/quota/summary (WP-BE-4)
# --------------------------------------------------------------------------- #


class _StubPool:
    def __init__(self, statuses, catalog=None):
        self._statuses = statuses
        self._catalog = catalog

    def probe_all(self):
        return list(self._statuses)

    def catalog_status(self):
        """Last gateway catalog read — ``None`` means the gateway was never asked."""
        return self._catalog


def test_quota_summary_gateway_separates_unasked_from_empty(
    client, monkeypatch, tmp_path
):
    """「还没问过网关」与「问过、它一个额度源都没供」必须是两格（R-048 口径）。

    否则界面上「没接」和「坏了」会长成同一个样子，而这两种情况的下一步完全不同。
    """
    from plobi.quota.sources import HealthStatus, SourceStatus

    statuses = [SourceStatus("zhipu-air", "cheap_api", HealthStatus.HEALTHY, 1.0, "ok", 1.0)]
    monkeypatch.setattr(console_router, "default_db_path", lambda: tmp_path / "missing.db")

    monkeypatch.setattr(console_router, "get_quota_pool", lambda: _StubPool(statuses))
    assert client.get("/api/quota/summary").json()["data"]["gateway"] is None

    answered_empty = {
        "state": "ok", "detail": "answered with 6 models, 0 quota app(s)",
        "checked_at": 1.0, "models_seen": 6, "source_names": [],
    }
    monkeypatch.setattr(
        console_router, "get_quota_pool", lambda: _StubPool(statuses, answered_empty)
    )
    got = client.get("/api/quota/summary").json()["data"]["gateway"]
    assert got == answered_empty
    assert got["state"] == "ok" and got["source_names"] == []


def test_quota_summary_envelope(client, monkeypatch, tmp_path):
    """§5 信封 + 契约字段（SourceStatus.as_dict 透出）+ 无 db 时 todayUsd=null。"""
    from plobi.quota.sources import HealthStatus, SourceStatus

    statuses = [
        SourceStatus("zhipu-air", "cheap_api", HealthStatus.HEALTHY, 1.25, "ok", 123.0),
        SourceStatus("antigravity", "aigw", HealthStatus.DEGRADED, None, "not verified", 123.0),
    ]
    monkeypatch.setattr(console_router, "get_quota_pool", lambda: _StubPool(statuses))
    monkeypatch.setattr(console_router, "default_db_path", lambda: tmp_path / "missing.db")

    body = client.get("/api/quota/summary").json()
    assert body["ok"] is True
    data = body["data"]
    assert [s["name"] for s in data["sources"]] == ["zhipu-air", "antigravity"]
    first = data["sources"][0]
    assert set(first) >= {"name", "kind", "health", "remaining_usd", "detail"}
    assert first["health"] == "healthy"
    assert first["remaining_usd"] == 1.25
    assert data["todayUsd"] is None  # 无 state.db → 诚实 null


def test_quota_summary_today_usd_sums_usage_db(client, _isolated_state, monkeypatch, tmp_path):
    """L1 视角（不带身份）的 todayUsd = agent_id 空串那些会话今日合计。"""
    monkeypatch.setattr(console_router, "get_quota_pool", lambda: _StubPool([]))
    register(_isolated_state, agenda=AGENDA)
    _attributed_db(
        tmp_path,
        monkeypatch,
        [("s1", "", 0.5, 3), ("s2", "", 0.25, 1)],
    )

    body = client.get("/api/quota/summary").json()
    assert body["data"]["todayUsd"] == 0.75


# --------------------------------------------------------------------------- #
# 请求级身份（WP-AGENT-SINGLE-GATEWAY §3.4）
# --------------------------------------------------------------------------- #


def test_quota_summary_today_usd_follows_request_identity(client, _isolated_state, monkeypatch, tmp_path):
    """单库后每个 Agent 必须看到自己那份账，不是别人那份。

    桌面一个进程服务所有 Agent（裁定 168 运行时那一半）：``X-Plobi-Agent``
    header 带身份（profile 名），``todayUsd`` 按反查出的名册 name 聚合该
    agent 会话的 usage（裁定 180 行级归属）。回归面 = 不带身份时看 L1
    （agent_id 空串）那份——与「agent 空 = L1」同口径。
    """
    monkeypatch.setattr(console_router, "get_quota_pool", lambda: _StubPool([]))
    register(
        _isolated_state,
        agenda=AGENDA,
        physics={"role": "l2_project", "profile": "l2-physics", "category": "courses"},
    )
    _attributed_db(
        tmp_path,
        monkeypatch,
        [
            ("s-l1", "", 1.0, 5),          # L1 的账
            ("s-physics", "physics", 0.25, 2),  # 课程 L2 的账
        ],
    )

    body = client.get(
        "/api/quota/summary", headers={"X-Plobi-Agent": "l2-physics"}
    ).json()
    assert body["data"]["todayUsd"] == 0.25

    # 不带身份 = L1（agent_id 空串）那份，不是全表、不是主后端那份
    plain = client.get("/api/quota/summary").json()
    assert plain["data"]["todayUsd"] == 1.0


def test_quota_summary_query_profile_is_remote_fallback(client, _isolated_state, monkeypatch, tmp_path):
    """``?profile=`` 是 global-remote 的既有路（connection-config.ts:230）。

    两条身份入口同语义；本地单后端走 header，remote 兜底走 query。
    """
    monkeypatch.setattr(console_router, "get_quota_pool", lambda: _StubPool([]))
    register(
        _isolated_state,
        agenda=AGENDA,
        physics={"role": "l2_project", "profile": "l2-physics", "category": "courses"},
    )
    _attributed_db(
        tmp_path,
        monkeypatch,
        [("s-l1", "", 1.0, 5), ("s-physics", "physics", 0.4, 1)],
    )

    body = client.get("/api/quota/summary?profile=l2-physics").json()
    assert body["data"]["todayUsd"] == 0.4


def test_agent_id_for_profile_maps_roster_rows(_isolated_state):
    """profile 名 → 名册 name 的反查（桌面头传 profile、库里存 name 的那一跳）。"""
    register(_isolated_state, agenda=AGENDA, physics={"role": "l2_project", "profile": "l2-physics"})
    assert console_router._agent_id_for_profile("l2-agenda") == "agenda"
    assert console_router._agent_id_for_profile("l2-physics") == "physics"
    assert console_router._agent_id_for_profile("") == ""
    assert console_router._agent_id_for_profile("no-such-home") == ""


def test_request_profile_precedence_header_over_query():
    """header（本地新路）优先于 query（remote 兜底）；都空 → 空串 = 进程默认身份。"""

    class _FakeRequest:
        def __init__(self, headers, query):
            self.headers = headers
            self.query_params = query

    got = console_router.request_profile(
        _FakeRequest({"x-plobi-agent": "course-l2"}, {"profile": "remote-host"})
    )
    assert got == "course-l2"

    got = console_router.request_profile(_FakeRequest({}, {"profile": "remote-host"}))
    assert got == "remote-host"

    assert console_router.request_profile(_FakeRequest({}, {})) == ""
    assert console_router.request_profile(None) == ""


def test_today_usd_agent_filter_matches_only_that_agent(tmp_path):
    """today_usd(agent_id=…) 只聚合该 agent 会话的 usage；空串 = L1；None = 全表。"""
    from plobi_state import SessionDB

    db = tmp_path / "state.db"
    store = SessionDB(db_path=db)
    stamp = (datetime.now() - timedelta(hours=1)).timestamp()
    store.create_session("s-l1", source="cli", agent_id="")
    store.create_session("s-a", source="cli", agent_id="Aura")
    conn = sqlite3.connect(str(db))
    for sid, cost in (("s-l1", 1.0), ("s-a", 0.25)):
        conn.execute(
            "INSERT INTO session_model_usage (session_id, model, api_call_count, "
            "estimated_cost_usd, first_seen, last_seen) VALUES (?, 'm', 1, ?, ?, ?)",
            (sid, cost, stamp, stamp),
        )
    conn.commit()
    conn.close()
    store.close()

    assert console_router.today_usd(db_path=db, agent_id="Aura") == 0.25
    assert console_router.today_usd(db_path=db, agent_id="") == 1.0
    assert console_router.today_usd(db_path=db, agent_id=None) == 1.25
    # 库不存在 → 诚实 None（不是 0：未知与没花钱是两回事）
    assert console_router.today_usd(db_path=tmp_path / "nope.db", agent_id="Aura") is None


def test_quota_summary_mounted_on_real_server():
    from plobi_cli import web_server

    assert web_server.app.url_path_for("quota_summary") == "/api/quota/summary"


# --------------------------------------------------------------------------- #
# mounting
# --------------------------------------------------------------------------- #


def test_real_web_server_mounts_console_routes():
    """A missing include_router must fail here, not in the desktop app.

    Resolved by endpoint name rather than by walking ``app.routes``: Starlette
    1.0 keeps ``include_router`` results nested (``_IncludedRouter``) instead
    of flattening them, so a path scan silently sees nothing.
    """
    from plobi_cli import web_server

    app = web_server.app
    assert app.url_path_for("list_agents") == "/api/agents"
    assert app.url_path_for("create_agent") == "/api/agents"
    assert (
        app.url_path_for("list_subagents", agent_id="agenda")
        == "/api/agents/agenda/subagents"
    )
    assert (
        app.url_path_for("agent_overview", agent_id="agenda")
        == "/api/agents/agenda/overview"
    )


def test_agenda_routes_still_mounted():
    """Guard against clobbering the live /api/agenda surface."""
    from plobi_cli import web_server

    assert web_server.app.url_path_for("list_agenda").startswith("/api/agenda")


# --------------------------------------------------------------------------- #
# startup seeding — the agenda L2 is a permanent registry row, not rail fiction
# --------------------------------------------------------------------------- #


def _stub_agenda_materialisation(monkeypatch) -> dict[str, list[str]]:
    """Record — never run — the two writes that build a profile on disk.

    ``spawn`` clones a whole profile home and the L2 toolset pass writes inside
    it. Both are the secretary's promise when it lands a 日程, not a backend's,
    and a startup test cannot assert "neither happened" against the real
    functions, so they are watched instead.
    """
    calls: dict[str, list[str]] = {"spawn": [], "toolsets": []}

    def fake_spawn(self, name, **kwargs):
        calls["spawn"].append(name)
        return {"name": name}

    def fake_toolsets(path):
        calls["toolsets"].append(str(path))
        return False

    monkeypatch.setattr(AgentRegistry, "spawn", fake_spawn)
    monkeypatch.setattr("plobi_cli.profiles.profile_exists", lambda name: False)
    monkeypatch.setattr("plobi.agents.registry.ensure_l2_agenda_toolsets", fake_toolsets)
    monkeypatch.setattr("plobi.agents.registry.ensure_l1_secretary_form", lambda: False)
    return calls


def test_startup_seed_registers_agenda_without_materialising_a_profile(
    _isolated_state, monkeypatch
):
    """The desktop/serve backend must seed the agenda L2 before it serves — as a row.

    ``GET /api/agents`` reads ``projects.yaml`` and writes nothing, so without
    this seed a home whose registry only holds human projects shows a rail with
    no 日程管家 at all. Visibility, though, is the whole of what the rail needs:
    materializing ``profiles/l2-agenda`` at boot handed every user an empty
    12K profile skeleton they never asked for, so both writes are pinned absent.
    """
    from plobi_cli import web_server

    calls = _stub_agenda_materialisation(monkeypatch)
    console_router.set_registry(None)  # a fresh server reads the row back off disk

    with TestClient(web_server.app):
        rows = console_router.list_agents()

    assert [row["id"] for row in rows] == ["agenda"]
    assert rows[0]["name"] == "日程秘书"
    assert calls == {"spawn": [], "toolsets": []}


def test_startup_puts_the_default_home_into_l1_secretary_shape(
    _isolated_state, monkeypatch, tmp_path
):
    """Boot flips a never-opted-in home into L1 秘书形态, in place, with no new profile.

    ``plobi-north-star`` is a bundled plugin and therefore opt-in, and the only
    code that narrowed L1 / wrote its SOUL fence ran from that plugin's
    ``register()`` — so the shape was unreachable on exactly the machines that
    needed it (measured: L1 called ``terminal`` and never dispatched). The
    desktop backend is where that has to break, and breaking it must not buy
    visibility with a fresh profile home.
    """
    import yaml

    from plobi.agents.registry import L1_MID_TOOLSETS, L1_SOUL_BEGIN
    from plobi_cli import web_server

    home = tmp_path / ".plobi"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("PLOBI_HOME", str(home))
    console_router.set_registry(None)

    with TestClient(web_server.app):
        rows = console_router.list_agents()

    assert [row["id"] for row in rows] == ["agenda"]
    cfg = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert "plobi-north-star" in cfg["plugins"]["enabled"]
    assert cfg["toolsets"] == list(L1_MID_TOOLSETS)
    assert "terminal" not in cfg["platform_toolsets"]["cli"]
    assert (home / "SOUL.md").read_text(encoding="utf-8").count(L1_SOUL_BEGIN) == 1
    assert not (home / "profiles").exists()


def test_seed_failure_does_not_break_startup(_isolated_state, monkeypatch, caplog):
    """A failed seed may not take the backend down with it.

    The agenda L2 ships with the product, and the secretary write paths re-seed
    on first use, so startup logs and keeps serving.
    """
    import logging

    from plobi_cli import web_server

    def explode(*args, **kwargs):
        raise RuntimeError("registry offline")

    monkeypatch.setattr("plobi.agents.registry.register_agenda_agent", explode)

    with caplog.at_level(logging.WARNING, logger="plobi_cli.web_server"):
        with TestClient(
            web_server.app,
            headers={web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN},
        ) as test_client:
            response = test_client.get("/api/agents")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert any("agenda L2" in record.getMessage() for record in caplog.records), caplog.text


def test_l1_secretary_shape_failure_does_not_break_startup(_isolated_state, monkeypatch, caplog):
    """Same rule for the secretary-shape pass: it is boot garnish, not a dependency.

    ``ensure_north_star_toolset`` re-applies the shape on every plugin load, so a
    home that fails this pass at boot still gets it before the first turn — the
    backend must not die over a config write.
    """
    import logging

    from plobi_cli import web_server

    def explode(*args, **kwargs):
        raise RuntimeError("config offline")

    monkeypatch.setattr("plobi.agents.registry.register_agenda_agent", lambda: (None, False))
    monkeypatch.setattr("plobi.agents.registry.ensure_l1_secretary_form", explode)

    with caplog.at_level(logging.WARNING, logger="plobi_cli.web_server"):
        with TestClient(
            web_server.app,
            headers={web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN},
        ) as test_client:
            response = test_client.get("/api/agents")

    assert response.status_code == 200
    assert any("L1 secretary shape" in record.getMessage() for record in caplog.records), (
        caplog.text
    )


# --------------------------------------------------------------------------- #
# GET /api/agents/:id/offloads —— 外包流水那一处（WP-L2-OUTSOURCE-ENTRY 判据 2）
# --------------------------------------------------------------------------- #


def _round(cwd: str, ts: str, *, cost=None, status="usage_reported_without_price", agent="codebuddy"):
    return {
        "ts": ts, "agent": agent, "cwd": cwd, "permission_flag": "-y --permission-mode=bypassPermissions",
        "brief": f"推进 {cwd}", "artifacts": [{"path": f"{cwd}/today.py", "sha256_12": "3da942743391"}],
        "duration_seconds": 16.4, "usage": {"input_tokens": 56255} if cost is None else {"cost_usd": cost},
        "cost_status": status, "failure": "",
    }


def _ledger_stub(monkeypatch, rows):
    import plobi.delegation.offload as offload_mod

    class _Stub:
        def __init__(self, root=None):
            pass

        def rows(self):
            return list(rows)

    monkeypatch.setattr(offload_mod, "RoundLedger", _Stub)


class TestAgentOffloadReadout:
    def test_unknown_agent_is_404(self, client, _isolated_state):
        assert client.get("/api/agents/没有这一格/offloads").status_code == 404

    def test_rounds_are_attributed_by_bound_folder(self, _isolated_state, monkeypatch):
        """流水按**目录**记（交出去的本来就是"某个目录里的一段活"），归属靠 project_path。"""
        register(_isolated_state, aura={"role": "l2_project", "profile": "aura",
                                        "project_path": "D:/projects/Aura"})
        _ledger_stub(monkeypatch, [
            _round("D:/projects/Aura", "2026-10-13T01:00:00+00:00"),
            _round("D:/projects/Aura/src", "2026-10-13T02:00:00+00:00"),
            _round("D:/projects/Other", "2026-10-13T03:00:00+00:00"),
        ])

        out = console_router.agent_offloads("aura")

        assert out["bound_folder"] == "D:/projects/Aura"
        assert out["total_rounds"] == 2, out
        assert {r["cwd"] for r in out["rounds"]} == {"D:/projects/Aura", "D:/projects/Aura/src"}
        assert out["unattributed_reason"] == ""

    def test_newest_first_and_limit(self, _isolated_state, monkeypatch):
        register(_isolated_state, aura={"role": "l2_project", "profile": "aura",
                                        "project_path": "D:/projects/Aura"})
        _ledger_stub(monkeypatch, [_round("D:/projects/Aura", f"2026-10-1{3 - i}T00:00:00+00:00")
                                   for i in range(3)])

        out = console_router.agent_offloads("aura", limit=2)

        assert [r["ts"] for r in out["rounds"]] == ["2026-10-13T00:00:00+00:00",
                                                    "2026-10-12T00:00:00+00:00"]
        assert out["total_rounds"] == 3, "limit 只截显示，总数不许跟着变小"

    def test_unbound_agent_says_why_instead_of_guessing(self, _isolated_state, monkeypatch):
        """没绑定目录 ⇒ 不许把所有外包都算到它头上（那比漏报更坏）。"""
        register(_isolated_state, aura={"role": "l2_project", "profile": "aura"})
        _ledger_stub(monkeypatch, [_round("D:/projects/Aura", "2026-10-13T01:00:00+00:00")])

        out = console_router.agent_offloads("aura")

        assert out["rounds"] == [] and out["total_rounds"] == 0
        assert "不猜" in out["unattributed_reason"]

    def test_one_unmeasurable_round_makes_the_total_unmeasurable(self, _isolated_state, monkeypatch):
        """R-073：任何一笔算不出 ⇒ 总额也不能装作算得出，且不许塌成 0。"""
        register(_isolated_state, aura={"role": "l2_project", "profile": "aura",
                                        "project_path": "D:/projects/Aura"})
        _ledger_stub(monkeypatch, [
            _round("D:/projects/Aura", "2026-10-13T01:00:00+00:00", cost=0.42),
            _round("D:/projects/Aura", "2026-10-13T02:00:00+00:00"),
        ])

        out = console_router.agent_offloads("aura")

        assert out["total_usd"] is None
        assert out["cost_status"] == "usage_reported_without_price"

    def test_priced_rounds_sum_up(self, _isolated_state, monkeypatch):
        register(_isolated_state, aura={"role": "l2_project", "profile": "aura",
                                        "project_path": "D:/projects/Aura"})
        _ledger_stub(monkeypatch, [
            _round("D:/projects/Aura", "2026-10-13T01:00:00+00:00", cost=0.42),
            _round("D:/projects/Aura", "2026-10-13T02:00:00+00:00", cost=0.08),
        ])

        out = console_router.agent_offloads("aura")

        assert out["total_usd"] == pytest.approx(0.5)
        assert out["cost_status"] == "priced"

    def test_empty_batch_is_not_zero_spend(self):
        """一格没外包过 ≠ 花了 0 元。"""
        from plobi.delegation.offload import total_usd

        assert total_usd([]) == (None, "priced")
        assert total_usd([_round("D:/x", "t")]) == (None, "usage_reported_without_price")
        assert total_usd([_round("D:/x", "t", cost=1.0)])[0] == 1.0


# --------------------------------------------------------------------------- #
# POST /api/offload/resume —— 点一行外包记录，回到那台外部软件里的那条会话
# （WP-L2-OUTSOURCE-ENTRY 入口面③第三级；标识与工作区**都来自台账那一行**，界面递的不算）
# --------------------------------------------------------------------------- #


RESUME_ROW = {
    "ts": "2026-10-14T02:00:00+00:00", "agent": "codebuddy", "cwd": "D:/projects/Aura",
    "permission_flag": "-y --permission-mode=bypassPermissions", "brief": "把今天的读数写成页",
    "artifacts": [], "duration_seconds": 16.4, "usage": None,
    "cost_status": "usage_reported_without_price", "failure": "",
    "external_session": "01a1248d-3b15-738e-977b-0b90cd206790",
    "external_session_source": "result.session_id",
}


def _no_spawn(monkeypatch):
    """任何一次真起进程都算失败：这一组的判据是"守门在起之前"。"""
    import subprocess

    def _boom(*_a, **_kw):
        raise AssertionError("不该起进程")

    monkeypatch.setattr(subprocess, "Popen", _boom)


def _ledger_rows(monkeypatch, rows):
    """台账走桩：这一组的判据是"只认台账里记过的那一轮"，不碰真 PLOBI_HOME。"""
    import plobi.delegation.offload as offload_mod

    class _Stub:
        def __init__(self, root=None):
            pass

        def rows(self):
            return [dict(r) for r in rows]

    monkeypatch.setattr(offload_mod, "RoundLedger", _Stub)


def _spawn_capture(monkeypatch):
    import subprocess
    from types import SimpleNamespace

    captured = {}
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kw: (
        captured.update(argv=list(argv), kw=kw), SimpleNamespace(pid=4242))[1])
    return captured


class TestOffloadResume:
    @pytest.mark.parametrize("agent", ["Cursor", "cursor", "", "   ", "codebuddy.exe; calc"])
    def test_only_the_two_allowlisted_agents_reach_a_spawn(self, monkeypatch, agent):
        _no_spawn(monkeypatch)
        _ledger_rows(monkeypatch, [RESUME_ROW])
        out = console_router.resume_external_session(agent, RESUME_ROW["external_session"])

        assert out["ok"] is False and out["status"] == 400

    @pytest.mark.parametrize("bad", [
        "", "   ", "01a1248d 3b15", "id; rm -rf /", "--resume --open",
        "..\\..\\windows\\system32", "x" * 200, "$(whoami)", "`id`",
    ])
    def test_an_id_that_is_not_executable_shape_is_refused(self, monkeypatch, bad):
        _no_spawn(monkeypatch)
        _ledger_rows(monkeypatch, [RESUME_ROW])
        import agent.external_offload as channel

        monkeypatch.setattr(channel, "resolve_command", lambda _a="": "D:/somewhere/codebuddy.cmd")
        out = console_router.resume_external_session("codebuddy", bad)

        assert out["ok"] is False and out["status"] == 400

    @pytest.mark.parametrize("agent,expect", [
        ("codebuddy", ["--resume", "{id}", "--open"]),
        ("agy", ["--conversation", "{id}"]),
    ])
    def test_argv_is_the_whitelisted_shape_array_not_shell(self, monkeypatch, tmp_path, agent, expect):
        captured = _spawn_capture(monkeypatch)
        import agent.external_offload as channel

        sid = "01a1248d-3b15"
        row = dict(RESUME_ROW, agent=agent, external_session=sid, cwd=str(tmp_path))
        _ledger_rows(monkeypatch, [row])
        binary = str(tmp_path / "launcher.cmd")
        monkeypatch.setattr(channel, "resolve_command", lambda _a="": binary)
        monkeypatch.setattr(channel, "cwd_is_allowed", lambda _p: (True, ""))

        out = console_router.resume_external_session(agent, sid)

        assert out["ok"] is True and out["data"]["pid"] == 4242
        assert captured["argv"][0] == binary
        assert captured["argv"][1:] == [sid if p == "{id}" else p for p in expect]
        assert captured["kw"].get("shell", False) is False
        assert captured["kw"]["cwd"] == str(tmp_path)          # 台账那一行记的目录

    def test_the_caller_cannot_choose_the_directory(self, monkeypatch, tmp_path, client):
        """残余收法（额度线 10-14 递的①）：工作区只认台账，界面递的 cwd 一律不看。"""
        captured = _spawn_capture(monkeypatch)
        import agent.external_offload as channel

        _ledger_rows(monkeypatch, [dict(RESUME_ROW, cwd=str(tmp_path))])
        monkeypatch.setattr(channel, "resolve_command", lambda _a="": "D:/somewhere/codebuddy.cmd")
        monkeypatch.setattr(channel, "cwd_is_allowed", lambda _p: (True, ""))

        resp = client.post("/api/offload/resume", json={
            "agent": "codebuddy",
            "session_id": RESUME_ROW["external_session"],
            "cwd": str(tmp_path.parent / "随便一个别处"),
        })

        assert resp.status_code == 200
        assert captured["kw"]["cwd"] == str(tmp_path)          # 仍然是台账里那个

    def test_an_id_the_ledger_never_recorded_is_refused(self, monkeypatch):
        """标识必须由台账背书：手打一个别处的会话号不算我们记过的那一轮。"""
        _no_spawn(monkeypatch)
        _ledger_rows(monkeypatch, [RESUME_ROW])
        import agent.external_offload as channel

        monkeypatch.setattr(channel, "resolve_command", lambda _a="": "D:/somewhere/codebuddy.cmd")
        out = console_router.resume_external_session("codebuddy", "ffffffff-1111-2222-333344445555")

        assert out["ok"] is False and out["status"] == 409
        assert "不是我们记过的那个" in out["error"]

    def test_a_row_without_a_recorded_folder_is_refused(self, monkeypatch):
        _no_spawn(monkeypatch)
        _ledger_rows(monkeypatch, [dict(RESUME_ROW, cwd="")])
        out = console_router.resume_external_session("codebuddy", RESUME_ROW["external_session"])

        assert out["ok"] is False and out["status"] == 409
        assert "没记工作区" in out["error"]

    def test_a_recorded_folder_that_is_no_longer_allowed_is_refused(self, monkeypatch):
        """台账里的目录也要再过一次通道那道判据（政策收紧时不越过它，而不是信旧值）。"""
        _no_spawn(monkeypatch)
        _ledger_rows(monkeypatch, [RESUME_ROW])
        import agent.external_offload as channel

        monkeypatch.setattr(channel, "resolve_command", lambda _a="": "D:/somewhere/codebuddy.cmd")
        monkeypatch.setattr(channel, "cwd_is_allowed", lambda _p: (False, "那是我们的仓"))
        out = console_router.resume_external_session("codebuddy", RESUME_ROW["external_session"])

        assert out["ok"] is False and out["status"] == 409
        assert "那是我们的仓" in out["error"]

    def test_unresolvable_command_is_reported_not_spawned(self, monkeypatch):
        _no_spawn(monkeypatch)
        _ledger_rows(monkeypatch, [RESUME_ROW])
        import agent.external_offload as channel

        monkeypatch.setattr(channel, "resolve_command", lambda _a="": "")
        out = console_router.resume_external_session("codebuddy", RESUME_ROW["external_session"])

        assert out["ok"] is False and out["status"] == 409

    def test_a_launcher_that_will_not_start_says_so(self, monkeypatch):
        import subprocess

        import agent.external_offload as channel

        _ledger_rows(monkeypatch, [RESUME_ROW])

        def _raise(*_a, **_kw):
            raise OSError("not a valid Win32 application")

        monkeypatch.setattr(subprocess, "Popen", _raise)
        monkeypatch.setattr(channel, "resolve_command", lambda _a="": "D:/somewhere/codebuddy.cmd")
        monkeypatch.setattr(channel, "cwd_is_allowed", lambda _p: (True, ""))
        out = console_router.resume_external_session("codebuddy", RESUME_ROW["external_session"])

        assert out["ok"] is False and out["status"] == 500
        assert "not a valid Win32" in out["error"]

    def test_route_rejects_an_unknown_agent_with_400(self, client, _isolated_state):
        resp = client.post("/api/offload/resume", json={"agent": "Cursor", "session_id": "abc"})

        assert resp.status_code == 400
        assert resp.json()["ok"] is False
