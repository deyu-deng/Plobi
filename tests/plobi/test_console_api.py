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
    monkeypatch.setattr("plobi.agents.registry.default_path", lambda: tmp_path / "projects.yaml")
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
    assert set(row) == {"id", "name", "status", "model", "todayCalls", "profile", "category"}
    assert row["id"] == "agenda"
    assert row["name"] == "日程秘书"
    assert row["status"] == "idle"  # no L3 children in flight
    assert row["model"] == "aigw/workbuddy/deepseek-chat"  # ADR-0011 default route
    assert row["todayCalls"] == 0
    assert row["profile"] == "l2-agenda"


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
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["id"] == "plobi-code"
    assert body["data"]["category"] == "projects"
    assert set(body["data"]) == {"id", "name", "status", "model", "todayCalls", "profile", "category"}
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
    assert set(body["data"]) == {"agent", "sessionId", "todayCostUsd", "todayTokens", "projectPath"}
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
# session attribution (WP-BE-3, ARCH-RULINGS 2026-09-02 裁定 1)
# --------------------------------------------------------------------------- #


def test_overview_session_id_is_latest_of_profile_state_db(client, _isolated_state, monkeypatch):
    """裁定 1：sessionId = agent→profile→该 profile state.db 最近活跃会话。"""
    register(_isolated_state, agenda=AGENDA)
    monkeypatch.setattr(
        console_router, "_latest_session_for_profile", lambda p: "sess-latest"
    )
    body = client.get("/api/agents/agenda/overview").json()
    assert body["ok"] is True
    assert body["data"]["sessionId"] == "sess-latest"


def test_session_attribution_uses_entry_profile_name(_isolated_state, monkeypatch):
    """profile 名取 entry.profile 或 agent 名（registry 1:1 映射），传给查询。"""
    register(_isolated_state, agenda={"role": "l2_agenda", "profile": "l2-agenda"})
    seen: list[str] = []

    def fake_latest(profile: str) -> str:
        seen.append(profile)
        return "sess-x"

    monkeypatch.setattr(console_router, "_latest_session_for_profile", fake_latest)
    assert console_router.sessions_for_agent("agenda") == ["sess-x"]
    assert seen == ["l2-agenda"]


def test_latest_session_missing_db_is_empty(tmp_path, monkeypatch):
    """profile home 无 state.db → 诚实空串（不建库、不伪造）。"""
    import plobi_cli.profiles as profiles_mod

    monkeypatch.setattr(profiles_mod, "get_profile_dir", lambda name: tmp_path)
    assert console_router._latest_session_for_profile("agenda") == ""


def test_usage_follows_session_attribution(client, _isolated_state, monkeypatch, tmp_path):
    """todayCalls 走 归属 session → session_model_usage 聚合（真实 SQL）。"""
    register(_isolated_state, agenda=AGENDA)
    db = tmp_path / "state.db"
    _usage_db(db, [_row("sess-latest", 4, 100, 0.5)])
    monkeypatch.setattr(console_router, "_latest_session_for_profile", lambda p: "sess-latest")
    monkeypatch.setattr(console_router, "default_db_path", lambda: db)

    row = client.get("/api/agents").json()["data"][0]
    assert row["todayCalls"] == 4


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
    def __init__(self, statuses):
        self._statuses = statuses

    def probe_all(self):
        return list(self._statuses)


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


def test_quota_summary_today_usd_sums_usage_db(client, monkeypatch, tmp_path):
    monkeypatch.setattr(console_router, "get_quota_pool", lambda: _StubPool([]))
    db = tmp_path / "state.db"
    _usage_db(db, [_row("s1", 3, 100, 0.5), _row("s2", 1, 10, 0.25)])
    monkeypatch.setattr(console_router, "default_db_path", lambda: db)

    body = client.get("/api/quota/summary").json()
    assert body["data"]["todayUsd"] == 0.75


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
# C4 routine template CRUD endpoints
# --------------------------------------------------------------------------- #


def test_get_routines_returns_seeds_disabled(client):
    body = client.get("/api/routines").json()
    assert body["ok"] is True
    seeds = {t["id"]: t for t in body["data"]}
    assert {"seed-sleep", "seed-breakfast", "seed-lunch", "seed-dinner"} <= set(seeds)
    assert all(t["enabled"] is False for t in seeds.values())


def test_post_routines_upserts_and_enables(client):
    body = client.post(
        "/api/routines",
        json={"id": "seed-lunch", "title": "午饭", "start_time": "12:30",
              "end_time": "13:10", "weekdays": [0, 1, 2, 3, 4], "enabled": True},
    ).json()
    assert body["ok"] is True
    assert body["data"]["title"] == "午饭"
    assert body["data"]["enabled"] is True
    listing = client.get("/api/routines").json()["data"]
    assert any(t["id"] == "seed-lunch" and t["enabled"] for t in listing)


def test_post_routines_validation_error_is_envelope(client):
    response = client.post(
        "/api/routines", json={"title": "x", "start_time": "25:00", "end_time": "26:00"}
    )
    assert response.status_code == 400
    assert response.json()["ok"] is False


# --------------------------------------------------------------------------- #
# C4 routine template CRUD endpoints
# --------------------------------------------------------------------------- #


def test_get_routines_returns_seeds_disabled(client):
    body = client.get("/api/routines").json()
    assert body["ok"] is True
    seeds = {t["id"]: t for t in body["data"]}
    assert {"seed-sleep", "seed-breakfast", "seed-lunch", "seed-dinner"} <= set(seeds)
    assert all(t["enabled"] is False for t in seeds.values())


def test_post_routines_upserts_and_enables(client):
    body = client.post(
        "/api/routines",
        json={"id": "seed-lunch", "title": "午饭", "start_time": "12:30",
              "end_time": "13:10", "weekdays": [0, 1, 2, 3, 4], "enabled": True},
    ).json()
    assert body["ok"] is True
    assert body["data"]["title"] == "午饭"
    assert body["data"]["enabled"] is True
    listing = client.get("/api/routines").json()["data"]
    assert any(t["id"] == "seed-lunch" and t["enabled"] for t in listing)


def test_post_routines_validation_error_is_envelope(client):
    response = client.post(
        "/api/routines", json={"title": "x", "start_time": "25:00", "end_time": "26:00"}
    )
    assert response.status_code == 400
    assert response.json()["ok"] is False
