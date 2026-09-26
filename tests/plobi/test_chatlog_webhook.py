"""Webhook path: same dedupe ledger as the sweep, and one bad record can't sink a batch."""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plobi.agenda.service import AgendaService
from plobi.collectors.chatlog import webhook as webhook_module
from plobi.collectors.chatlog.config import CollectorConfig
from plobi.collectors.chatlog.confirm import HeuristicConfirmer
from plobi.collectors.chatlog.pipeline import ChatlogPipeline
from plobi.collectors.chatlog.state import SeenStore, TalkerStore

NOW = datetime(2026, 8, 25, 10, 0)


class StubClient:
    def fetch(self, talker, day=None):
        return []

    def list_talkers(self, keyword: str = "", limit: int = 10000) -> list[str]:
        return []

    def healthy(self):
        return True


@pytest.fixture()
def client(tmp_path):
    pipeline = ChatlogPipeline(
        config=CollectorConfig(talkers=["班级群"], enabled=True),
        client=StubClient(),
        service=AgendaService(tmp_path / "agenda.db"),
        seen=SeenStore(tmp_path / "seen.db"),
        talkers=TalkerStore(tmp_path / "talkers.db"),
        confirmer=HeuristicConfirmer(now_factory=lambda: NOW),
    )
    webhook_module.set_pipeline(pipeline)

    app = FastAPI()
    app.include_router(webhook_module.router, prefix="/api/chatlog")

    with TestClient(app) as test_client:
        test_client.pipeline = pipeline  # type: ignore[attr-defined]
        yield test_client

    webhook_module.set_pipeline(None)


def _record(content: str, msg_id: str = "w1", talker: str = "班级群") -> dict:
    return {
        "id": msg_id,
        "talker": talker,
        "senderName": "导师",
        "time": "2026-08-25 10:00:00",
        "content": content,
    }


def test_single_record_creates_pending_entry(client):
    response = client.post("/api/chatlog/webhook", json=_record("明天下午三点开组会"))

    assert response.status_code == 200
    assert len(response.json()["created"]) == 1
    assert len(client.pipeline.service.list_pending()) == 1


def test_batch_under_data_key(client):
    payload = {"data": [_record("明天下午三点开组会", "w1"), _record("周三上午十点答辩", "w2")]}
    response = client.post("/api/chatlog/webhook", json=payload)

    assert len(response.json()["created"]) == 2


def test_replay_of_same_message_is_deduped(client):
    client.post("/api/chatlog/webhook", json=_record("明天下午三点开组会"))
    second = client.post("/api/chatlog/webhook", json=_record("明天下午三点开组会"))

    assert second.json()["skipped_duplicate"] == 1
    assert len(client.pipeline.service.list_pending()) == 1


def test_non_whitelisted_talker_is_rejected_even_when_pushed(client):
    response = client.post(
        "/api/chatlog/webhook", json=_record("明天下午三点开组会", "w9", talker="私人聊天")
    )

    assert response.json()["skipped_not_whitelisted"] == 1
    assert client.pipeline.service.list_pending() == []


def test_malformed_records_are_skipped_not_fatal(client):
    payload = {"data": [{"junk": True}, _record("明天下午三点开组会", "w3")]}
    response = client.post("/api/chatlog/webhook", json=payload)

    assert response.status_code == 200
    assert len(response.json()["created"]) == 1


def test_status_reports_configuration(client):
    body = client.get("/api/chatlog/status").json()

    assert body["enabled"] is True
    assert body["talkers"] == 1
    assert body["chatlog_reachable"] is True


def test_sweep_endpoint_runs_the_pipeline(client):
    body = client.post("/api/chatlog/sweep").json()

    assert body["scanned"] == 0
