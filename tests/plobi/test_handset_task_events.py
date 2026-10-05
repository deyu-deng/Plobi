"""The handset's task/event read surface, in GATED mode (骨架第 2、3 根).

Two things are pinned here that the design depends on:

* the five outward classes are a *projection* — an unknown kernel kind must still
  reach the phone (as ``status``), never be dropped and never be a 500;
* the surface is read-only and device-only: no token → 401, a browser session →
  401, and a board that does not exist yet → an honest empty list.
"""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from plobi.tasks import router as tasks_router
from plobi_cli import kanban_db
from plobi_cli.dashboard_auth import devices as dev
from plobi_cli.dashboard_auth import registry
from plobi_cli.dashboard_auth.pairing import install_pairing_auth
from plobi_cli.dashboard_auth.scopes import enforce
from plobi_cli.dashboard_auth.token_auth import (
    SESSION_HEADER_NAME,
    clear_token_routes,
    token_auth_middleware,
)


@pytest.fixture
def board(tmp_path):
    """A real kanban DB with two tasks and a mixed bag of kernel events."""
    db = tmp_path / "kanban.db"
    conn = kanban_db.connect(db_path=db)
    try:
        first = kanban_db.create_task(conn, title="排明天的天", assignee="agenda")
        second = kanban_db.create_task(conn, title="写早报草稿", assignee="agenda")
        # Seed through the kernel's own table, not a helper, so the kinds below
        # are exactly what a dispatcher run would have written.
        rows = [
            (first, "spawned", {"pid": 4242}),
            (first, "heartbeat", {"turns": 3}),
            (first, "completed", {"summary": "排好了"}),
            (first, "quantum_leap", {"note": "kind the projection has never seen"}),
            (second, "blocked", {"reason": "要人批"}),
            (second, "attached", {"filename": "morning.md"}),
        ]
        for task_id, kind, payload in rows:
            kanban_db._append_event(conn, task_id, kind, payload)
    finally:
        conn.close()
    return db, first, second


@pytest.fixture
def gated_client(tmp_path, monkeypatch, board):
    """The device surface behind the real token seam + real cookie gate."""
    db, first, second = board
    clear_token_routes()
    registry.clear_providers()

    monkeypatch.setattr(tasks_router, "_db_path", lambda: db)
    monkeypatch.setattr(dev, "_DEFAULT", dev.DeviceStore(tmp_path))

    app = FastAPI()
    app.state.auth_required = True

    @app.middleware("http")
    async def _cookie_gate(request, call_next):
        from plobi_cli.dashboard_auth.middleware import gated_auth_middleware

        return await gated_auth_middleware(request, call_next)

    app.middleware("http")(token_auth_middleware)  # registered last ⇒ outermost
    app.include_router(
        tasks_router.router,
        prefix="/api/handset",
        dependencies=[Depends(enforce)],
    )

    assert install_pairing_auth() is True
    # The device's token, minted the way a real handset mints it: open a pairing
    # window on the desktop, redeem it once.
    store = dev.get_store()
    code, _expires_at = store.issue_code()
    _device_id, token = store.redeem(code, name="test handset")

    with TestClient(app) as client:
        yield client, token, first, second

    clear_token_routes()
    registry.clear_providers()


def _keys(token):
    return {"Authorization": f"Bearer {token}"}


def test_task_list_carries_identity_and_cursor(gated_client):
    client, token, first, _second = gated_client
    rows = client.get("/api/handset/tasks", headers=_keys(token)).json()
    assert isinstance(rows, list) and rows, rows
    by_id = {row["id"]: row for row in rows}
    assert first in by_id
    row = by_id[first]
    assert row["title"] == "排明天的天"
    assert row["assignee"] == "agenda"
    assert row["status"] in kanban_db.VALID_STATUSES
    # The cursor the handset stores so it can ask "what happened since".
    assert row["lastEventId"] >= 1


def test_event_stream_projects_the_five_classes(gated_client):
    client, token, first, _second = gated_client
    events = client.get(f"/api/handset/tasks/{first}/events", headers=_keys(token)).json()
    kinds = {event["kind"] for event in events}
    details = {event["detail"] for event in events}

    assert {"progress", "artifact", "status"} <= kinds
    assert "spawned" in details and "completed" in details
    for event in events:
        assert event["kind"] in tasks_router.EVENT_CLASSES
        # Raw kernel information is never lost by the grouping.
        assert event["detail"]
        assert event["taskId"] == first
        assert set(event) == {"id", "taskId", "runId", "kind", "detail", "at", "payload"}


def test_unknown_kernel_kind_still_reaches_the_phone(gated_client):
    """A kernel kind this projection has never seen must not vanish or 500."""
    client, token, first, _second = gated_client
    events = client.get(f"/api/handset/tasks/{first}/events", headers=_keys(token)).json()
    odd = [event for event in events if event["detail"] == "quantum_leap"]
    assert len(odd) == 1
    assert odd[0]["kind"] == tasks_router._DEFAULT_CLASS
    assert odd[0]["payload"]["note"].startswith("kind the projection")


def test_after_cursor_pages_forward(gated_client):
    client, token, first, _second = gated_client
    all_events = client.get(
        f"/api/handset/tasks/{first}/events", headers=_keys(token)
    ).json()
    assert len(all_events) >= 4
    mid = all_events[1]["id"]
    tail = client.get(
        f"/api/handset/tasks/{first}/events",
        params={"after": mid},
        headers=_keys(token),
    ).json()
    assert [event["id"] for event in tail] == [e["id"] for e in all_events[2:]]


def test_global_stream_walks_across_tasks(gated_client):
    client, token, first, second = gated_client
    stream = client.get("/api/handset/events", params={"limit": 100}, headers=_keys(token)).json()
    assert {event["taskId"] for event in stream} >= {first, second}
    ids = [event["id"] for event in stream]
    assert ids == sorted(ids)

    marker = ids[2]
    after = client.get(
        "/api/handset/events", params={"after": marker}, headers=_keys(token)
    ).json()
    assert all(event["id"] > marker for event in after)
    assert after == [event for event in stream if event["id"] > marker]


def test_surface_is_read_only_and_device_only(gated_client):
    client, token, _first, _second = gated_client
    # No credential at all, and a browser session, are both refused here.
    assert client.get("/api/handset/tasks").status_code == 401
    client.cookies.set("plobi_session_at", "not-a-browser-session")
    assert client.get("/api/handset/tasks").status_code == 401
    client.cookies.clear()
    # The token buys reads, not writes: POST is not a registered method.
    assert client.post("/api/handset/tasks", json={}, headers=_keys(token)).status_code == 401
    assert client.get(
        "/api/handset/tasks", headers={SESSION_HEADER_NAME: token}
    ).status_code == 200


def test_bad_status_and_missing_task_are_honest(gated_client):
    client, token, _first, _second = gated_client
    bad = client.get(
        "/api/handset/tasks", params={"status": "nonsense"}, headers=_keys(token)
    )
    assert bad.status_code == 400
    assert "status must be one of" in bad.json()["detail"]
    assert client.get(
        "/api/handset/tasks/no-such-task/events", headers=_keys(token)
    ).status_code == 404


def test_no_board_yet_is_an_empty_list_not_an_error(monkeypatch, tmp_path):
    """A machine that never ran the dispatcher must get ``[]``, not a created
    schema and not a 500."""
    clear_token_routes()
    registry.clear_providers()
    monkeypatch.setattr(tasks_router, "_db_path", lambda: tmp_path / "absent.db")

    app = FastAPI()
    app.include_router(tasks_router.router, prefix="/api/handset")
    with TestClient(app) as client:
        assert client.get("/api/handset/tasks").json() == []
        assert client.get("/api/handset/events").json() == []
        assert client.get("/api/handset/tasks/x/events").status_code == 404
    assert not (tmp_path / "absent.db").exists()

    clear_token_routes()
    registry.clear_providers()
