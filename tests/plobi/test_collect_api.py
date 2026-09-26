"""/api/collect: the board's first-run review and pending-queue endpoints (A7)."""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plobi.agenda.service import AgendaService
from plobi.collectors.chatlog import collect_api, webhook as webhook_module
from plobi.collectors.chatlog.client import TalkerSession
from plobi.collectors.chatlog.config import CollectorConfig
from plobi.collectors.chatlog.confirm import HeuristicConfirmer
from plobi.collectors.chatlog.pipeline import ChatlogPipeline
from plobi.collectors.chatlog.state import SeenStore, TalkerStore

NOW = datetime(2026, 8, 25, 10, 0)

# Raw talker ids vs the display name chatlog would report for them.
DEFAULT_NAMES = {
    "班级群": "ZJU 2502 班",
    "项目群": "项目协作群",
}


class StubClient:
    def __init__(self, sessions: list[str], names: dict[str, str] | None = None):
        self.sessions = sessions
        self.names = names or {}
        self.fetches: list[tuple[str, str | None]] = []

    def fetch(self, talker, day=None):
        self.fetches.append((talker, day.isoformat() if day is not None else None))
        return []

    def list_talker_sessions(self, keyword: str = "", limit: int = 10000) -> list[TalkerSession]:
        return [
            TalkerSession(id=talker, name=self.names.get(talker, talker))
            for talker in self.sessions
            if keyword in talker
        ]

    def list_talkers(self, keyword: str = "", limit: int = 10000) -> list[str]:
        return [t for t in self.sessions if keyword in t]

    def healthy(self):
        return True


@pytest.fixture()
def client(tmp_path):
    pipeline = ChatlogPipeline(
        config=CollectorConfig(mode="blacklist", enabled=True),
        client=StubClient(["班级群", "项目群", "广告群"], names=DEFAULT_NAMES),
        service=AgendaService(tmp_path / "agenda.db"),
        seen=SeenStore(tmp_path / "seen.db"),
        talkers=TalkerStore(tmp_path / "talkers.db"),
        confirmer=HeuristicConfirmer(now_factory=lambda: NOW),
    )
    # collect_api wires to the webhook module's canonical pipeline instance.
    webhook_module.set_pipeline(pipeline)

    app = FastAPI()
    app.include_router(webhook_module.router, prefix="/api/chatlog")
    app.include_router(collect_api.router, prefix="/api/collect")

    with TestClient(app) as test_client:
        test_client.pipeline = pipeline  # type: ignore[attr-defined]
        yield test_client

    webhook_module.set_pipeline(None)
    collect_api.set_pipeline(None)


def test_list_starts_unreviewed_with_all_candidates_pending(client):
    body = client.get("/api/collect/talkers").json()

    assert body["reviewComplete"] is False
    # Every active session is offered for the first-run review; unreviewed
    # ones surface as pending without being written to the status table.
    by_id = {row["id"]: row["status"] for row in body["talkers"]}
    assert by_id == {"班级群": "pending", "项目群": "pending", "广告群": "pending"}
    assert client.pipeline.talkers.pending() == set()


def test_list_surfaces_chatlog_display_names(client):
    body = client.get("/api/collect/talkers").json()

    names = {row["id"]: row["name"] for row in body["talkers"]}
    # The board renders `name`; chatlog's topicName must reach it instead of
    # the raw @chatroom / wxid id.
    assert names["班级群"] == "ZJU 2502 班"
    assert names["项目群"] == "项目协作群"
    # No chatlog label -> id fallback, so a row can never be blank.
    assert names["广告群"] == "广告群"


def test_one_click_collect_marks_known(client):
    response = client.post("/api/collect/talkers/班级群/mode", json={"mode": "collect"})

    assert response.json() == {"id": "班级群", "status": "known"}
    assert client.pipeline.talkers.status_of("班级群") == "known"

    body = client.get("/api/collect/talkers").json()
    assert {r["id"] for r in body["talkers"] if r["status"] == "known"} == {"班级群"}


def test_one_click_exclude_marks_excluded(client):
    response = client.post("/api/collect/talkers/广告群/mode", json={"mode": "exclude"})

    assert response.json() == {"id": "广告群", "status": "excluded"}
    assert client.pipeline.talkers.status_of("广告群") == "excluded"


def test_bulk_decision_marks_many(client):
    response = client.post(
        "/api/collect/talkers/bulk",
        json={"talkers": ["项目群", "广告群"], "status": "excluded"},
    )

    assert response.json()["updated"] == 2
    assert client.pipeline.talkers.excluded() == {"项目群", "广告群"}


def test_review_complete_flips_the_gate(client):
    response = client.post("/api/collect/review-complete")

    assert response.json() == {"reviewComplete": True}
    assert client.pipeline.talkers.review_done() is True

    body = client.get("/api/collect/talkers").json()
    assert body["reviewComplete"] is True


# --------------------------------------------------------------------------
# WP-M1-COLLECT-CLOSE: review-complete kicks a 3-day lookback sweep
# --------------------------------------------------------------------------


def test_sweep_before_review_is_409_and_fetches_nothing(client):
    response = client.post("/api/collect/sweep")

    assert response.status_code == 409
    # ADR-0010 fail-closed: nothing is fetched before the review completes.
    assert client.pipeline.client.fetches == []


def test_sweep_after_review_runs_the_pipeline(client):
    store = client.pipeline.talkers
    store.mark_known("班级群")
    store.set_review_done(True)

    response = client.post("/api/collect/sweep")

    assert response.status_code == 200
    body = response.json()
    assert body["review_gated"] == 0
    # Default lookback is 3 days for the only known talker.
    assert [talker for (talker, _day) in client.pipeline.client.fetches] == [
        "班级群",
        "班级群",
        "班级群",
    ]


def test_review_complete_excludes_pending_official_accounts_only(client):
    store = client.pipeline.talkers
    store.mark_known("班级群")
    store.mark_pending("gh_待定公众号")
    store.mark_known("gh_已点公众号")
    store.mark_excluded("gh_已排除公众号")
    # Enumerated but never recorded — also counts as pending for the sweep.
    client.pipeline.client.sessions.append("gh_枚举到的公众号")

    response = client.post("/api/collect/review-complete")

    assert response.json() == {"reviewComplete": True}
    assert store.status_of("gh_待定公众号") == "excluded"
    assert store.status_of("gh_枚举到的公众号") == "excluded"
    # Verdict 23: talkers the user already decided are never rewritten.
    assert store.status_of("gh_已点公众号") == "known"
    assert store.status_of("gh_已排除公众号") == "excluded"
    # Non-official pending rows are not touched by the bulk exclude.
    store.mark_pending("项目群")
    response = client.post("/api/collect/review-complete")
    assert store.status_of("项目群") == "pending"


def test_review_complete_survives_enumeration_failure(client, monkeypatch):
    store = client.pipeline.talkers
    store.mark_pending("gh_待定公众号")

    def boom(*args, **kwargs):
        raise RuntimeError("chatlog down")

    monkeypatch.setattr(client.pipeline.client, "list_talker_sessions", boom)

    response = client.post("/api/collect/review-complete")

    # Degraded to store.pending(): still excluded, still review-complete.
    assert response.json() == {"reviewComplete": True}
    assert store.status_of("gh_待定公众号") == "excluded"
    assert store.review_done() is True


def test_review_complete_kicks_one_background_lookback_sweep(client, monkeypatch):
    import threading

    done = threading.Event()
    calls: list[int] = []
    real_kick = collect_api.kick_lookback_sweep

    def spy(pipeline, lookback_days=collect_api.DEFAULT_LOOKBACK_DAYS):
        calls.append(lookback_days)
        result = real_kick(pipeline, lookback_days)
        done.set()
        return result

    monkeypatch.setattr(collect_api, "kick_lookback_sweep", spy)
    client.pipeline.talkers.mark_known("班级群")

    response = client.post("/api/collect/review-complete")

    assert response.json() == {"reviewComplete": True}
    assert done.wait(timeout=5), "background sweep did not finish"
    assert calls == [3], "exactly one background sweep with lookback=3"
    # The review flag is not rolled back even if the sweep had failed.
    assert client.pipeline.talkers.review_done() is True


def test_kick_lookback_sweep_fetches_three_days_for_known_talkers(client):
    store = client.pipeline.talkers
    store.mark_known("班级群")
    store.set_review_done(True)

    report = collect_api.kick_lookback_sweep(client.pipeline, 3)

    days = [day for (_talker, day) in client.pipeline.client.fetches]
    assert len(days) == 3
    assert all(day is not None for day in days)
    assert len(set(days)) == 3
    assert days == sorted(days), "oldest day first"
    assert report["review_gated"] == 0
