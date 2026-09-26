"""Service semantics: pending changes, rollback on dismiss, confirm sequences."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from plobi.agenda import store
from plobi.agenda.service import (
    AgendaService,
    ConfirmSeqExpired,
    EventNotFound,
)
from plobi.agenda.store import AgendaValidationError


@pytest.fixture()
def svc(tmp_path):
    return AgendaService(tmp_path / "agenda.db")


def test_manual_entries_are_confirmed_immediately(svc):
    event = svc.create_manual(title="自习", start_at="2026-08-25T19:00:00")
    assert event.status == "confirmed"
    assert event.source == "manual"
    assert event.confirm_seq is None


def test_manual_source_rejected_by_ingest(svc):
    with pytest.raises(AgendaValidationError):
        svc.ingest_candidate(title="x", start_at="2026-08-25T09:00:00", source="manual")


def test_new_candidate_lands_pending_with_sequence(svc):
    result = svc.ingest_candidate(
        title="组会",
        start_at="2026-08-25T14:00:00",
        kind="meeting",
        evidence={"msg_id": "m1", "snippet": "明天两点组会"},
    )
    assert result.created is True
    assert result.event.status == "pending"
    assert result.event.confirm_seq == 1
    assert result.event.evidence["msg_id"] == "m1"


def test_change_to_existing_event_snapshots_previous_values(svc):
    original = svc.create_manual(
        title="组会", start_at="2026-08-25T14:00:00", kind="meeting"
    )
    result = svc.ingest_candidate(
        title="组会",
        start_at="2026-08-25T16:00:00",
        kind="meeting",
        target_event_id=original.id,
        evidence={"msg_id": "m2", "snippet": "组会改到四点"},
    )

    assert result.created is False
    assert result.changed_fields == ["start_at"]
    assert result.event.status == "pending"
    assert result.event.prev_value["start_at"] == "2026-08-25T14:00:00"
    assert result.event.start_at == "2026-08-25T16:00:00"


def test_noop_change_does_not_create_pending(svc):
    original = svc.create_manual(title="组会", start_at="2026-08-25T14:00:00")
    result = svc.ingest_candidate(
        title="组会", start_at="2026-08-25T14:00:00", target_event_id=original.id
    )
    assert result.changed_fields == []
    assert result.event.status == "confirmed"


def test_repeated_reschedules_keep_oldest_known_good(svc):
    original = svc.create_manual(title="组会", start_at="2026-08-25T14:00:00")
    svc.ingest_candidate(
        title="组会", start_at="2026-08-25T16:00:00", target_event_id=original.id
    )
    svc.ingest_candidate(
        title="组会", start_at="2026-08-25T18:00:00", target_event_id=original.id
    )
    current = svc.get(original.id)
    # Rollback must return to the last confirmed truth, not the intermediate guess.
    assert current.prev_value["start_at"] == "2026-08-25T14:00:00"


def test_confirm_clears_pending_bookkeeping(svc):
    result = svc.ingest_candidate(title="组会", start_at="2026-08-25T14:00:00")
    confirmed = svc.confirm(result.event.id)
    assert confirmed.status == "confirmed"
    assert confirmed.prev_value is None
    assert confirmed.confirm_seq is None


def test_dismiss_rolls_back_a_change(svc):
    original = svc.create_manual(title="组会", start_at="2026-08-25T14:00:00")
    svc.ingest_candidate(
        title="组会", start_at="2026-08-25T16:00:00", target_event_id=original.id
    )
    restored = svc.dismiss(original.id)
    assert restored is not None
    assert restored.start_at == "2026-08-25T14:00:00"
    assert restored.status == "confirmed"
    assert restored.prev_value is None


def test_dismiss_deletes_a_newly_proposed_event(svc):
    result = svc.ingest_candidate(title="临时会", start_at="2026-08-25T14:00:00")
    assert svc.dismiss(result.event.id) is None
    with pytest.raises(EventNotFound):
        svc.get(result.event.id)


def test_confirm_and_dismiss_are_noops_when_not_pending(svc):
    event = svc.create_manual(title="自习", start_at="2026-08-25T19:00:00")
    assert svc.confirm(event.id).status == "confirmed"
    assert svc.dismiss(event.id).status == "confirmed"


def test_missing_event_raises(svc):
    with pytest.raises(EventNotFound):
        svc.get("evt_nope")
    with pytest.raises(EventNotFound):
        svc.confirm("evt_nope")
    with pytest.raises(EventNotFound):
        svc.delete("evt_nope")


def test_resolve_by_seq_confirms(svc):
    result = svc.ingest_candidate(title="组会", start_at="2026-08-25T14:00:00")
    resolved = svc.resolve_by_seq(result.event.confirm_seq, accept=True)
    assert resolved is not None and resolved.status == "confirmed"


def test_resolve_by_seq_rejects_expired(svc, tmp_path):
    result = svc.ingest_candidate(title="组会", start_at="2026-08-25T14:00:00")
    stale = (datetime.now() - timedelta(hours=25)).replace(microsecond=0).isoformat()
    conn = store.connect(tmp_path / "agenda.db")
    store.update_event(conn, result.event.id, confirm_seq_at=stale)
    conn.close()

    with pytest.raises(ConfirmSeqExpired):
        svc.resolve_by_seq(result.event.confirm_seq, accept=True)


def test_confirm_seq_is_live_follows_the_store_clock_seam(svc, monkeypatch):
    """TTL is judged on the store's own clock (``now_iso``), not raw wall time.

    ARCH-RULING 28.9 hygiene: ``confirm_seq_is_live`` reads time from the single
    ``store.now_iso`` seam, so a caller that pins that clock sees a consistent
    TTL judgement. A seq issued at T0 is live 1h later and expired 25h later —
    pure arithmetic on the pinned clock, no real wall clock involved.
    """
    monkeypatch.setattr(store, "now_iso", lambda: "2026-09-10T21:30:00")
    result = svc.ingest_candidate(title="组会", start_at="2026-09-11T15:00:00")

    # 1 hour later on the store clock -> still inside the 24h window.
    monkeypatch.setattr(store, "now_iso", lambda: "2026-09-10T22:30:00")
    assert store.confirm_seq_is_live(svc.get(result.event.id)) is True

    # 25 hours later on the store clock -> past the 24h TTL.
    monkeypatch.setattr(store, "now_iso", lambda: "2026-09-11T22:30:00")
    assert store.confirm_seq_is_live(svc.get(result.event.id)) is False


def test_resolve_by_seq_expires_on_the_store_clock(svc, monkeypatch):
    """Same seam through the reply path: a stale store clock expires the seq."""
    monkeypatch.setattr(store, "now_iso", lambda: "2026-09-10T21:30:00")
    result = svc.ingest_candidate(title="组会", start_at="2026-09-11T15:00:00")

    # Push the pinned clock past the TTL -> the reply is refused, not applied.
    monkeypatch.setattr(store, "now_iso", lambda: "2026-09-11T22:30:00")
    with pytest.raises(ConfirmSeqExpired):
        svc.resolve_by_seq(result.event.confirm_seq, accept=True)


def test_resolve_by_unknown_seq_raises(svc):
    with pytest.raises(EventNotFound):
        svc.resolve_by_seq(999, accept=True)


def test_default_window_covers_today_and_tomorrow(svc):
    today = datetime.now().date()
    svc.create_manual(
        title="今天", start_at=datetime.combine(today, datetime.min.time()).replace(hour=9)
    )
    svc.create_manual(
        title="明天",
        start_at=datetime.combine(today + timedelta(days=1), datetime.min.time()).replace(hour=9),
    )
    svc.create_manual(
        title="下周",
        start_at=datetime.combine(today + timedelta(days=7), datetime.min.time()).replace(hour=9),
    )

    titles = [e.title for e in svc.list_agenda()]
    assert titles == ["今天", "明天"]


def test_list_agenda_dedupes_duplicate_event_ids(svc, monkeypatch):
    """WP-AXIS-INSIGHT root-cause fix (service layer).

    ``events.id`` is PRIMARY KEY so the SQL layer can't legitimately produce
    duplicates — but defense-in-depth matters because upstream callers
    sometimes append the same Event twice (an ``/api/agenda`` payload that
    concatenates a default-window and a day-window query, for example).
    Patch ``_order_by_confirmed_plans`` to return the same id twice, then
    assert ``list_agenda`` keeps only the first occurrence.
    """
    today = datetime.now().date()
    base = datetime.combine(today, datetime.min.time()).replace(hour=9)
    first = svc.create_manual(title="上午课", start_at=base)

    def _double(_self, _conn, events):
        # Same Event object appended twice (first occurrence wins by design).
        return list(events) + list(events)

    monkeypatch.setattr(
        "plobi.agenda.service.AgendaService._order_by_confirmed_plans", _double
    )

    rows = svc.list_agenda()
    assert [r.id for r in rows] == [first.id]
    assert len(rows) == 1


def test_update_manual_rejects_protected_fields(svc):
    event = svc.create_manual(title="自习", start_at="2026-08-25T19:00:00")
    with pytest.raises(AgendaValidationError):
        svc.update_manual(event.id, status="pending")


def test_daily_summary_marks_pending(svc):
    today = datetime.now().date()
    at_nine = datetime.combine(today, datetime.min.time()).replace(hour=9)
    svc.create_manual(title="确认过的", start_at=at_nine)
    svc.ingest_candidate(title="待确认的", start_at=at_nine.replace(hour=10))

    summary = svc.daily_summary()
    assert "确认过的" in summary
    assert "待确认的（待确认）" in summary


def test_daily_summary_handles_empty_day(svc):
    summary = svc.daily_summary(datetime(2030, 1, 1))
    assert "当日无日程" in summary
