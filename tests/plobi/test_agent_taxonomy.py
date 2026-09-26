"""R-012/R-013: L2 agent taxonomy + lifecycle (裁定 19).

Covers:

* Four categories (projects/butler/events/research) land on disk with the
  correct default ``project_path`` and the bound folder is created.
* ``events`` agents require ``sourceEventId`` (a confirmed agenda event) —
  missing → 400; sixth live events agent → 400.
* ``POST /api/agents/:id/dissolve`` archives the profile dir + marks the row
  archived; the default list hides it, ``?include_archived=1`` shows it.
* Backward compat: a legacy ``projects.yaml`` row without ``category`` loads
  as ``butler`` (no migration required).

Discipline: events vocab untouched; ADR-0011 L1≠L2 stays green; no new L1
tools. All lifecycle ops go through the API (裁定 19).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plobi.agents.registry import (
    AGENT_CATEGORIES,
    DEFAULT_CATEGORY,
    MAX_LIVE_EVENTS_AGENTS,
    AgentEntry,
    AgentRegistry,
)
import plobi.console.router as console_router
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
    monkeypatch.setattr(
        "plobi.agents.registry.default_path", lambda: tmp_path / "projects.yaml"
    )
    # Redirect default project folders into the tmp tree so we don't touch the
    # real D:\projects / D:\Cloud during tests. The registry reads
    # DEFAULT_PROJECT_PATHS at create time, so we patch that map.
    monkeypatch.setattr(
        "plobi.agents.registry.DEFAULT_PROJECT_PATHS",
        {
            "projects": str(tmp_path / "projects"),
            "events": str(tmp_path / "cloud" / "events"),
            "research": str(tmp_path / "cloud" / "research"),
            "butler": "",
        },
    )
    # Also patch the console router's view of the same map (it imports the
    # symbol by name, so the patch above already covers it — but be explicit
    # in case future code reads a local copy).
    set_registry = AgentRegistry(path=tmp_path / "projects.yaml")
    console_router.set_registry(set_registry)
    set_tracker(SubagentTracker())
    yield set_registry
    console_router.set_registry(None)
    set_tracker(None)


def _stub_spawn(registry, monkeypatch):
    def fake_spawn(name, *, clone_from=None, write_config=True):
        return {"name": name, "clone_from": clone_from}

    monkeypatch.setattr(registry, "spawn", fake_spawn)


# --------------------------------------------------------------------------- #
# R-012: category round-trip + backward compat
# --------------------------------------------------------------------------- #


def test_legacy_row_without_category_loads_as_butler(_isolated_state):
    """A projects.yaml written before R-012 has no category key — it must
    load as ``butler`` (the catch-all), not crash, not invent a grade."""
    raw = {
        "version": 1,
        "agents": {
            "old-agent": {"role": "l2_project", "description": "pre-R-012"},
        },
    }
    import yaml

    path = _isolated_state.path
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    reloaded = AgentRegistry.load(path)
    entry = reloaded.get("old-agent")
    assert entry is not None
    assert entry.category == DEFAULT_CATEGORY == "butler"
    assert entry.project_path == ""  # butler has no folder binding


def test_category_roundtrips_through_yaml(_isolated_state):
    """All four categories survive a save → load cycle faithfully."""
    import yaml

    reg = _isolated_state
    for cat in AGENT_CATEGORIES:
        reg.upsert(
            AgentEntry(name=f"agent-{cat}", role="l2_project", category=cat)
        )
    reg.save()
    reloaded = AgentRegistry.load(reg.path)
    for cat in AGENT_CATEGORIES:
        assert reloaded.get(f"agent-{cat}").category == cat


def test_invalid_category_in_yaml_folds_to_butler(_isolated_state):
    """A typo in projects.yaml (category: personal) fails closed to butler."""
    import yaml

    raw = {
        "version": 1,
        "agents": {
            "typo": {"role": "l2_project", "category": "personal"},
        },
    }
    _isolated_state.path.write_text(
        yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8"
    )
    reloaded = AgentRegistry.load(_isolated_state.path)
    assert reloaded.get("typo").category == "butler"


# --------------------------------------------------------------------------- #
# R-013: project_path default mapping + folder creation
# --------------------------------------------------------------------------- #


def test_four_categories_land_with_correct_project_path(
    client, _isolated_state, monkeypatch, tmp_path
):
    """Acceptance #1: register one agent per category → category/project_path
    correct on disk, and the bound folder is created for non-butler."""
    _stub_spawn(_isolated_state, monkeypatch)
    cases = [
        ("proj-1", "projects", str(tmp_path / "projects")),
        ("evt-1", "events", str(tmp_path / "cloud" / "events")),
        ("res-1", "research", str(tmp_path / "cloud" / "research")),
        ("but-1", "butler", ""),  # butler has no folder
    ]
    for agent_id, cat, expected_path in cases:
        body = {"id": agent_id, "category": cat}
        if cat == "events":
            # events needs a confirmed sourceEventId — stub it.
            monkeypatch.setattr(
                console_router, "_agenda_event_is_confirmed", lambda eid: True
            )
            body["sourceEventId"] = "evt_confirmed_1"
        resp = client.post("/api/agents", json=body)
        assert resp.status_code == 200, (agent_id, resp.json())
        row = resp.json()["data"]
        assert row["category"] == cat

    # Verify project_path landed in the registry + folders were created.
    # NOTE: create_agent calls reload_registry(), so we must read from the
    # live singleton, not the stale _isolated_state handle.
    live_reg = console_router.get_registry()
    for agent_id, cat, expected_path in cases:
        entry = live_reg.get(agent_id)
        assert entry is not None, f"{agent_id} not in registry after POST"
        assert entry.category == cat
        assert entry.project_path == expected_path
        if expected_path:
            assert Path(expected_path).is_dir(), (
                f"folder for {agent_id} ({cat}) not created: {expected_path}"
            )
        else:
            # butler: no folder expected.
            assert entry.project_path == ""


def test_project_path_override_respected(client, _isolated_state, monkeypatch, tmp_path):
    """A caller-supplied projectPath overrides the category default."""
    _stub_spawn(_isolated_state, monkeypatch)
    override = str(tmp_path / "custom" / "my-proj")
    resp = client.post(
        "/api/agents",
        json={"id": "custom-proj", "category": "projects", "projectPath": override},
    )
    assert resp.status_code == 200
    live_reg = console_router.get_registry()
    entry = live_reg.get("custom-proj")
    assert entry.project_path == override
    assert Path(override).is_dir()


def test_overview_returns_project_path(client, _isolated_state):
    """GET /api/agents/:id/overview carries projectPath for the right rail."""
    _isolated_state.upsert(
        AgentEntry(
            name="proj-x",
            role="l2_project",
            category="projects",
            project_path="/tmp/proj-x",
        )
    )
    _isolated_state.save()
    console_router.reload_registry()
    body = client.get("/api/agents/proj-x/overview").json()
    assert body["ok"] is True
    assert body["data"]["projectPath"] == "/tmp/proj-x"


# --------------------------------------------------------------------------- #
# 裁定 19: events lifecycle guards
# --------------------------------------------------------------------------- #


def test_events_agent_without_source_event_id_is_400(
    client, _isolated_state, monkeypatch
):
    """Acceptance #2a: events category without sourceEventId → 400."""
    _stub_spawn(_isolated_state, monkeypatch)
    resp = client.post(
        "/api/agents", json={"id": "evt-no-src", "category": "events"}
    )
    assert resp.status_code == 400
    assert resp.json()["ok"] is False
    assert "sourceEventId" in resp.json()["error"] or "source_event" in resp.json()["error"]
    assert _isolated_state.get("evt-no-src") is None


def test_events_agent_with_unconfirmed_source_event_is_400(
    client, _isolated_state, monkeypatch
):
    """sourceEventId must point to a *confirmed* agenda event — fail-closed."""
    _stub_spawn(_isolated_state, monkeypatch)
    monkeypatch.setattr(
        console_router, "_agenda_event_is_confirmed", lambda eid: False
    )
    resp = client.post(
        "/api/agents",
        json={"id": "evt-bad-src", "category": "events", "sourceEventId": "evt_x"},
    )
    assert resp.status_code == 400
    assert resp.json()["ok"] is False
    assert _isolated_state.get("evt-bad-src") is None


def test_sixth_events_agent_is_400(client, _isolated_state, monkeypatch):
    """Acceptance #2b: the 6th live events agent is rejected (cap = 5)."""
    _stub_spawn(_isolated_state, monkeypatch)
    monkeypatch.setattr(
        console_router, "_agenda_event_is_confirmed", lambda eid: True
    )
    # Register 5 events agents directly (bypassing the API to seed the cap).
    for i in range(MAX_LIVE_EVENTS_AGENTS):
        _isolated_state.upsert(
            AgentEntry(
                name=f"evt-{i}",
                role="l2_project",
                category="events",
                source_event_id=f"confirmed_{i}",
            )
        )
    _isolated_state.save()
    console_router.reload_registry()
    # The 6th via the API → 400.
    resp = client.post(
        "/api/agents",
        json={"id": "evt-6", "category": "events", "sourceEventId": "confirmed_6"},
    )
    assert resp.status_code == 400
    assert resp.json()["ok"] is False
    assert "5" in resp.json()["error"] or "events" in resp.json()["error"]
    assert _isolated_state.get("evt-6") is None


def test_events_cap_not_counted_on_update(client, _isolated_state, monkeypatch):
    """Updating an existing events agent (same id) does not trip the cap."""
    _stub_spawn(_isolated_state, monkeypatch)
    monkeypatch.setattr(
        console_router, "_agenda_event_is_confirmed", lambda eid: True
    )
    for i in range(MAX_LIVE_EVENTS_AGENTS):
        _isolated_state.upsert(
            AgentEntry(
                name=f"evt-{i}",
                role="l2_project",
                category="events",
                source_event_id=f"confirmed_{i}",
            )
        )
    _isolated_state.save()
    console_router.reload_registry()
    # Re-POST one of the existing ids → should succeed (not counted as new).
    resp = client.post(
        "/api/agents",
        json={
            "id": "evt-0",
            "category": "events",
            "sourceEventId": "confirmed_0",
        },
    )
    # evt-0 is not an agenda entry, so it goes through upsert (update path).
    assert resp.status_code == 200


# --------------------------------------------------------------------------- #
# 裁定 19: dissolve (archive, not delete)
# --------------------------------------------------------------------------- #


def test_dissolve_archives_and_hides_from_default_list(
    client, _isolated_state, monkeypatch, tmp_path
):
    """Acceptance #3: dissolve → row hidden by default, include_archived=1 shows it."""
    _stub_spawn(_isolated_state, monkeypatch)
    # Register a projects agent.
    resp = client.post(
        "/api/agents", json={"id": "to-dissolve", "category": "projects"}
    )
    assert resp.status_code == 200
    # Stub the profile dir move: _archive_profile_dir needs a real profile dir.
    # Patch get_profile_dir to point at a tmp dir we control.
    fake_profile = tmp_path / "profiles" / "to-dissolve"
    fake_profile.mkdir(parents=True)
    monkeypatch.setattr(
        "plobi_cli.profiles.get_profile_dir",
        lambda name: fake_profile,
        raising=False,
    )
    # Also patch the import inside _archive_profile_dir (it imports locally).
    import sys

    fake_plobi_cli = sys.modules.get("plobi_cli")
    if fake_plobi_cli is None:
        # Create a minimal stub module so the local import succeeds.
        import types

        fake_plobi_cli = types.ModuleType("plobi_cli")
        sys.modules["plobi_cli"] = fake_plobi_cli
    import types as _types

    if not hasattr(fake_plobi_cli, "profiles"):
        fake_profiles = _types.ModuleType("plobi_cli.profiles")
        fake_profiles.get_profile_dir = lambda name: fake_profile
        fake_plobi_cli.profiles = fake_profiles
    else:
        fake_plobi_cli.profiles.get_profile_dir = lambda name: fake_profile

    # Dissolve.
    resp = client.post("/api/agents/to-dissolve/dissolve")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["archived"] is True
    assert data["archivedAt"]
    assert data["archivePath"]
    assert "notification" in data
    # The profile dir moved into _archived/.
    archived = tmp_path / "profiles" / "_archived"
    assert archived.is_dir()
    assert any(p.name.startswith("to-dissolve-") for p in archived.iterdir())
    # Source dir is gone.
    assert not fake_profile.exists()

    # Default list hides it.
    ids = [r["id"] for r in client.get("/api/agents").json()["data"]]
    assert "to-dissolve" not in ids
    # include_archived=1 shows it.
    arch_ids = [
        r["id"]
        for r in client.get("/api/agents?include_archived=1").json()["data"]
    ]
    assert "to-dissolve" in arch_ids


def test_dissolve_unknown_agent_is_404(client, _isolated_state):
    resp = client.post("/api/agents/nope/dissolve")
    assert resp.status_code == 404
    assert resp.json()["ok"] is False


def test_dissolve_is_idempotent(client, _isolated_state, monkeypatch, tmp_path):
    """Dissolving an already-archived agent returns the existing archive info."""
    _stub_spawn(_isolated_state, monkeypatch)
    _isolated_state.upsert(
        AgentEntry(name="already", role="l2_project", category="projects")
    )
    _isolated_state.save()
    console_router.reload_registry()
    fake_profile = tmp_path / "profiles" / "already"
    fake_profile.mkdir(parents=True)
    import sys, types

    fake_plobi_cli = sys.modules.get("plobi_cli") or types.ModuleType("plobi_cli")
    sys.modules["plobi_cli"] = fake_plobi_cli
    if not hasattr(fake_plobi_cli, "profiles"):
        fake_plobi_cli.profiles = types.ModuleType("plobi_cli.profiles")
    fake_plobi_cli.profiles.get_profile_dir = lambda name: fake_profile

    first = client.post("/api/agents/already/dissolve")
    assert first.status_code == 200
    assert first.json()["data"]["archived"] is True
    first_at = first.json()["data"]["archivedAt"]

    second = client.post("/api/agents/already/dissolve")
    assert second.status_code == 200
    assert second.json()["data"]["alreadyArchived"] is True
    assert second.json()["data"]["archivedAt"] == first_at


# ---------------------------------------------------------------------------
# WP-AGENT-DISPLAY-NAME / 裁定 37.1：display_name 走完整 router 路径
# ---------------------------------------------------------------------------


def test_create_agent_routes_name_to_display_name_not_description(client, _isolated_state, monkeypatch):
    """POST /api/agents 带 name=Aura → AgentEntry.display_name='Aura'，
    description 保持空（不再被人名占坑）。"""
    from plobi.console import router as console_router

    reg = console_router.get_registry()
    _stub_spawn(reg, monkeypatch)

    body = {
        "id": "aura",
        "category": "projects",
        "role": "l2_project",
        "name": "Aura",
        "description": "节奏感知的项目 L2",
    }
    resp = client.post("/api/agents", json=body)
    assert resp.status_code == 200, resp.text
    payload = resp.json()["data"]
    # id 永远是小写 slug。
    assert payload["id"] == "aura"
    # name = display_name（人设的中文 / 保留大小写）。
    assert payload["name"] == "Aura"

    entry = reg.get("aura")
    assert entry.display_name == "Aura"
    assert entry.description == "节奏感知的项目 L2"


def test_get_agents_returns_human_name_not_slug(client, _isolated_state, monkeypatch):
    """GET /api/agents 返回的 name 字段用人名（小写 id 在 id 字段）。"""
    from plobi.console import router as console_router

    reg = console_router.get_registry()
    _stub_spawn(reg, monkeypatch)

    client.post(
        "/api/agents",
        json={
            "id": "aura",
            "category": "projects",
            "name": "Aura",
        },
    )

    resp = client.get("/api/agents")
    assert resp.status_code == 200
    rows = resp.json()["data"]
    aura = next(r for r in rows if r["id"] == "aura")
    assert aura["name"] == "Aura"  # 不再是 "aura"


def test_get_agents_falls_back_to_id_when_display_name_empty(
    client, _isolated_state, monkeypatch
):
    """老条目无 display_name → name 回退 id（小写 slug），仍可读。"""
    from plobi.console import router as console_router
    import yaml

    reg = console_router.get_registry()
    # 直接写 yaml 模拟老条目（无 display_name 字段）。
    raw = {
        "version": 1,
        "agents": {
            "oldie": {"role": "l2_project", "category": "projects", "description": "legacy"},
        },
    }
    reg.path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    console_router.reload_registry()

    resp = client.get("/api/agents")
    assert resp.status_code == 200
    rows = resp.json()["data"]
    oldie = next(r for r in rows if r["id"] == "oldie")
    # 没有 display_name 字段 → name 回退 id。
    assert oldie["name"] == "oldie"


def test_get_agents_agenda_overrides_display_name(client, _isolated_state, monkeypatch):
    """裁定 13：agenda 的固定短名「日程秘书」优先级最高——即使有人误填
    display_name='日程安排'，左栏仍显示「日程秘书」。"""
    from plobi.console import router as console_router
    from plobi.agents.registry import AgentEntry

    reg = console_router.get_registry()
    # 不通过 POST；直接写一个带自定义 display_name 的 agenda 行。
    reg.upsert(
        AgentEntry(
            name="agenda",
            role="l2_agenda",
            category="agenda",
            display_name="日程安排",  # 误填
            description="日程闭环",
        )
    )

    resp = client.get("/api/agents")
    rows = resp.json()["data"]
    agenda = next(r for r in rows if r["id"] == "agenda")
    # 裁定 13 固定短名赢。
    assert agenda["name"] == "日程秘书"


def test_create_agent_does_not_strip_name_into_description(client, _isolated_state, monkeypatch):
    """裁定 37.1 关键回归：旧代码把 ``name`` 塞进 ``description`` 字段；本
    刀确保这两条字段不再混用——name 不进 description。"""
    from plobi.console import router as console_router

    reg = console_router.get_registry()
    _stub_spawn(reg, monkeypatch)

    body = {
        "id": "open-source",
        "category": "research",
        "name": "Open Source Research",
    }
    resp = client.post("/api/agents", json=body)
    assert resp.status_code == 200, resp.text
    entry = reg.get("open-source")
    # name 不再污染 description。
    assert entry.display_name == "Open Source Research"
    assert entry.description == ""


def test_create_agent_no_titlecase_no_truncate(client, _isolated_state, monkeypatch):
    """iPhone 项目 原样存——不做 title-case、不截断。"""
    from plobi.console import router as console_router

    reg = console_router.get_registry()
    _stub_spawn(reg, monkeypatch)

    body = {
        "id": "iphone-proj",
        "category": "projects",
        "name": "iPhone 项目",
    }
    resp = client.post("/api/agents", json=body)
    assert resp.status_code == 200, resp.text
    entry = reg.get("iphone-proj")
    assert entry.display_name == "iPhone 项目"  # 原样
