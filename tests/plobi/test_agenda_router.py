"""HTTP contract for /api/agenda — status codes and payload shape."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plobi.agenda import router as agenda_router
from plobi.agenda import service as agenda_service
from plobi.agenda.service import AgendaService


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(agenda_service, "_DEFAULT", AgendaService(tmp_path / "agenda.db"))
    app = FastAPI()
    app.include_router(agenda_router.router, prefix="/api/agenda")
    with TestClient(app) as test_client:
        yield test_client


def test_create_list_and_delete(client):
    created = client.post(
        "/api/agenda",
        json={"title": "自习", "start_at": "2026-08-25T19:00:00", "kind": "task"},
    )
    assert created.status_code == 200
    event = created.json()
    assert event["status"] == "confirmed"
    assert event["source"] == "manual"

    listed = client.get("/api/agenda", params={"from": "2026-08-25T00:00:00", "to": "2026-08-25T23:59:59"})
    assert listed.status_code == 200
    assert [e["title"] for e in listed.json()] == ["自习"]

    deleted = client.delete(f"/api/agenda/{event['id']}")
    assert deleted.status_code == 200
    assert deleted.json()["ok"] is True


def test_patch_updates_fields(client):
    event = client.post(
        "/api/agenda", json={"title": "组会", "start_at": "2026-08-25T14:00:00"}
    ).json()

    patched = client.patch(f"/api/agenda/{event['id']}", json={"title": "组会（改）"})
    assert patched.status_code == 200
    assert patched.json()["title"] == "组会（改）"
    assert patched.json()["start_at"] == "2026-08-25T14:00:00"


def test_missing_event_is_404(client):
    assert client.patch("/api/agenda/evt_nope", json={"title": "x"}).status_code == 404
    assert client.delete("/api/agenda/evt_nope").status_code == 404
    assert client.post("/api/agenda/evt_nope/confirm").status_code == 404


def test_invalid_payload_is_4xx_not_500(client):
    # Missing required field -> pydantic 422
    assert client.post("/api/agenda", json={"title": "x"}).status_code == 422
    # Well-formed but semantically invalid -> domain 400
    bad_date = client.post("/api/agenda", json={"title": "x", "start_at": "not-a-date"})
    assert bad_date.status_code == 400
    bad_kind = client.post(
        "/api/agenda", json={"title": "x", "start_at": "2026-08-25T09:00:00", "kind": "nope"}
    )
    assert bad_kind.status_code == 400


def test_pending_confirm_flow(client, tmp_path):
    svc = agenda_service.get_service()
    result = svc.ingest_candidate(
        title="组会",
        start_at="2026-08-25T14:00:00",
        kind="meeting",
        evidence={"msg_id": "m1", "snippet": "两点组会"},
    )

    pending = client.get("/api/agenda/pending")
    assert pending.status_code == 200
    assert [e["id"] for e in pending.json()] == [result.event.id]
    assert pending.json()[0]["evidence"]["snippet"] == "两点组会"

    confirmed = client.post(f"/api/agenda/{result.event.id}/confirm")
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"
    assert client.get("/api/agenda/pending").json() == []


def test_dismiss_reports_deletion_of_new_proposal(client):
    svc = agenda_service.get_service()
    result = svc.ingest_candidate(title="临时会", start_at="2026-08-25T14:00:00")

    dismissed = client.post(f"/api/agenda/{result.event.id}/dismiss")
    assert dismissed.status_code == 200
    assert dismissed.json() == {"ok": True, "id": result.event.id, "deleted": True}


def test_dismiss_rolls_back_a_change(client):
    original = client.post(
        "/api/agenda", json={"title": "组会", "start_at": "2026-08-25T14:00:00"}
    ).json()

    svc = agenda_service.get_service()
    svc.ingest_candidate(
        title="组会", start_at="2026-08-25T16:00:00", target_event_id=original["id"]
    )

    board = client.get(
        "/api/agenda", params={"from": "2026-08-25T00:00:00", "to": "2026-08-25T23:59:59"}
    ).json()
    assert board[0]["status"] == "pending"
    assert board[0]["prev_value"]["start_at"] == "2026-08-25T14:00:00"

    restored = client.post(f"/api/agenda/{original['id']}/dismiss").json()
    assert restored["start_at"] == "2026-08-25T14:00:00"
    assert restored["status"] == "confirmed"
    assert restored["prev_value"] is None


def test_default_window_needs_no_params(client):
    assert client.get("/api/agenda").status_code == 200
