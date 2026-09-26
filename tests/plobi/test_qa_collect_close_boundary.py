"""QA boundary challenges for WP-M1-COLLECT-CLOSE (commit 44ad6f8).

Independent verification cases — written by QA, deliberately self-contained
(no imports from the implementer's test helpers) so an implementation bug
cannot hide behind a shared fixture.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plobi.agenda.service import AgendaService
from plobi.collectors.chatlog import collect_api, webhook as webhook_module
from plobi.collectors.chatlog.client import ChatlogUnavailable, ChatMessage, TalkerSession
from plobi.collectors.chatlog.config import CollectorConfig
from plobi.collectors.chatlog.confirm import HeuristicConfirmer
from plobi.collectors.chatlog.pipeline import ChatlogPipeline, refresh_agenda
from plobi.collectors.chatlog.state import SeenStore, TalkerStore

NOW = datetime(2026, 8, 25, 10, 0)


class RecordingClient:
    """Fake chatlog client that records every fetch(talker, day) call."""

    def __init__(self, by_talker=None, sessions=None, fail=False):
        self.by_talker = by_talker or {}
        self.sessions = sessions if sessions is not None else list(self.by_talker.keys())
        self.fail = fail
        self.calls: list[tuple[str, date | None]] = []

    def fetch(self, talker, day=None):
        self.calls.append((talker, day))
        if self.fail:
            raise ChatlogUnavailable("boom")
        return self.by_talker.get(talker, [])

    def list_talker_sessions(self, keyword: str = "", limit: int = 10000) -> list[TalkerSession]:
        return [TalkerSession(id=t, name=t) for t in self.sessions if keyword in t]

    def list_talkers(self, keyword: str = "", limit: int = 10000) -> list[str]:
        return [t for t in self.sessions if keyword in t]

    def healthy(self):
        return not self.fail


class StubApiClient(RecordingClient):
    """fetch() returns [] — API tests only need the recorded calls."""

    def __init__(self, sessions, fail=False):
        super().__init__(by_talker={}, sessions=sessions, fail=fail)

    def fetch(self, talker, day=None):
        self.calls.append((talker, day))
        return []


def make_pipeline(tmp_path, client, *, known=(), review_done=True):
    store = TalkerStore(tmp_path / "talkers.db")
    for talker in known:
        store.mark_known(talker)
    store.set_review_done(review_done)
    return ChatlogPipeline(
        config=CollectorConfig(mode="blacklist", enabled=True),
        client=client,
        service=AgendaService(tmp_path / "agenda.db"),
        seen=SeenStore(tmp_path / "seen.db"),
        talkers=store,
        confirmer=HeuristicConfirmer(now_factory=lambda: NOW),
    )


def make_message(content, *, talker="班级群", msg_id="m1"):
    return ChatMessage(
        msg_id=msg_id,
        talker=talker,
        sender="导师",
        sent_at="2026-08-25 10:00:00",
        content=content,
    )


# ---------------------------------------------------------------------------
# Boundary 1 — explicit `day` with the default lookback=1 must be honoured
# verbatim (no drift to today, no extra days).
# ---------------------------------------------------------------------------


def test_run_once_explicit_day_with_default_lookback_is_respected(tmp_path):
    client = RecordingClient({"班级群": []})
    pipeline = make_pipeline(tmp_path, client, known=["班级群"])

    explicit = date(2026, 8, 24)
    pipeline.run_once(explicit)

    assert client.calls == [("班级群", explicit)], "exactly one fetch for the explicit day"


def test_run_once_default_call_passes_none_not_today(tmp_path):
    client = RecordingClient({"班级群": []})
    pipeline = make_pipeline(tmp_path, client, known=["班级群"])

    pipeline.run_once()

    assert client.calls == [("班级群", None)], "watchdog path must stay day=None"


# ---------------------------------------------------------------------------
# Boundary 2 — POST /api/collect/sweep before review: 409 AND zero fetches.
# ---------------------------------------------------------------------------


@pytest.fixture()
def api(tmp_path):
    client = StubApiClient(["班级群", "项目群"])
    pipeline = make_pipeline(tmp_path, client, known=["班级群"], review_done=False)
    webhook_module.set_pipeline(pipeline)
    app = FastAPI()
    app.include_router(webhook_module.router, prefix="/api/chatlog")
    app.include_router(collect_api.router, prefix="/api/collect")
    with TestClient(app) as test_client:
        test_client.pipeline = pipeline  # type: ignore[attr-defined]
        test_client.stub = client  # type: ignore[attr-defined]
        yield test_client
    webhook_module.set_pipeline(None)
    collect_api.set_pipeline(None)


def test_sweep_before_review_is_409_and_fetch_is_never_called(api):
    response = api.post("/api/collect/sweep")

    assert response.status_code == 409
    assert api.stub.calls == [], "fail-closed: not a single fetch may happen"


def test_sweep_lookback_days_zero_clamps_to_one(api):
    api.pipeline.talkers.set_review_done(True)

    response = api.post("/api/collect/sweep", params={"lookback_days": 0})

    assert response.status_code == 200
    days = [day for (_t, day) in api.stub.calls]
    assert len(days) == 1, "lookback_days=0 must clamp to a single day"


# ---------------------------------------------------------------------------
# Boundary 3 — the same msg_id delivered by two different days of one
# lookback window lands exactly once.
# ---------------------------------------------------------------------------


def test_same_msg_id_across_two_days_ingested_once(tmp_path):
    client = RecordingClient(
        {"班级群": [make_message("明天下午三点开组会", msg_id="dup-1")]}
    )
    pipeline = make_pipeline(tmp_path, client, known=["班级群"])

    report = pipeline.run_once(lookback_days=2)

    assert len(client.calls) == 2
    assert len(report.created) == 1
    assert report.skipped_duplicate == 1
    assert len(pipeline.service.list_pending()) == 1


# ---------------------------------------------------------------------------
# Boundary 4 — an already-excluded gh_ talker is not rewritten back by
# review-complete, even when chatlog enumerates it.
# ---------------------------------------------------------------------------


def test_review_complete_never_rewrites_excluded_or_known_gh(api):
    store = api.pipeline.talkers
    store.mark_excluded("gh_已排除")
    store.mark_known("gh_已收藏")
    api.stub.sessions.append("gh_枚举到的")

    response = api.post("/api/collect/review-complete")

    assert response.json() == {"reviewComplete": True}
    assert store.status_of("gh_已排除") == "excluded", "verdict 23: no rewrite"
    assert store.status_of("gh_已收藏") == "known", "verdict 23: no rewrite"
    assert store.status_of("gh_枚举到的") == "excluded"


def test_review_complete_does_not_touch_pending_non_gh(api):
    store = api.pipeline.talkers
    store.mark_pending("项目群")

    response = api.post("/api/collect/review-complete")

    assert response.json() == {"reviewComplete": True}
    assert store.status_of("项目群") == "pending", "non-official pending stays pending"


# ---------------------------------------------------------------------------
# Boundary 5 — refresh_agenda: today empty but tomorrow non-empty → the
# routine sweep only, no widening.
# ---------------------------------------------------------------------------


def test_refresh_agenda_no_widening_when_tomorrow_is_not_empty(tmp_path):
    client = RecordingClient({"班级群": []})
    pipeline = make_pipeline(tmp_path, client, known=["班级群"])
    tomorrow = (datetime.now() + timedelta(days=1)).date().isoformat()
    pipeline.service.create_manual(
        title="组会", start_at=f"{tomorrow}T15:00:00", kind="meeting"
    )

    _report, summary = refresh_agenda(pipeline=pipeline)

    assert len(client.calls) == 1, "tomorrow occupied → routine sweep only"
    assert not summary.get("lookback_widened", False)


def test_refresh_agenda_no_widening_when_today_is_not_empty(tmp_path):
    client = RecordingClient({"班级群": []})
    pipeline = make_pipeline(tmp_path, client, known=["班级群"])
    today = datetime.now().date().isoformat()
    pipeline.service.create_manual(
        title="答辩", start_at=f"{today}T09:00:00", kind="meeting"
    )

    _report, summary = refresh_agenda(pipeline=pipeline)

    assert len(client.calls) == 1, "today occupied → routine sweep only"
    assert not summary.get("lookback_widened", False)


# ---------------------------------------------------------------------------
# Self-added — health gate, error tolerance, clamping.
# ---------------------------------------------------------------------------


def test_refresh_agenda_unhealthy_raises_chatlog_dead_even_after_review(tmp_path):
    from plobi.collectors.chatlog.pipeline import ChatlogDead

    client = RecordingClient({"班级群": []}, fail=True)
    pipeline = make_pipeline(tmp_path, client, known=["班级群"])

    with pytest.raises(ChatlogDead):
        refresh_agenda(pipeline=pipeline)

    assert client.calls == [], "dead chatlog must not be fetched at all"


def test_lookback_sweep_survives_chatlog_unavailable(tmp_path):
    client = RecordingClient({"班级群": []}, fail=True)
    pipeline = make_pipeline(tmp_path, client, known=["班级群"])

    report = pipeline.run_once(lookback_days=3)

    # Every day was still attempted, nothing crashed, nothing ingested.
    assert len(client.calls) == 3
    assert report.created == []
    assert pipeline.service.list_pending() == []
