"""C2: daily-plan confirm/dismiss shares the ADR-0009 确认N machine."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from plobi.agenda import store
from plobi.agenda.dispatch import (
    PendingDispatcher,
    format_pending_plan,
)
from plobi.agenda.service import (
    AgendaService,
    ConfirmSeqExpired,
    PlanNotFound,
)
from plobi.agenda.stats import change_report
from plobi.butler.report import build_morning, format_morning
from plobi.notify.base import RecordingNotifier
from plobi.quota.pool import QuotaPool
from plobi.quota.sources import CheapApiSource, HealthStatus, SourceStatus


@pytest.fixture()
def svc(tmp_path):
    return AgendaService(tmp_path / "agenda.db")


def _events_snapshot(svc: AgendaService) -> list[dict]:
    with svc._conn() as conn:
        return [event.to_dict() for event in store.list_events(conn, include_cancelled=True)]


def _seed_plan(
    svc: AgendaService,
    *,
    for_date: str,
    status: str = "pending",
    summary: str = "明天两件事",
    event_count: int = 1,
    confirm_seq: int | None = None,
    confirm_seq_at: str | None = None,
    items: list[dict] | None = None,
):
    with svc._conn() as conn:
        plan = store.upsert_daily_plan(
            conn,
            for_date=for_date,
            status=status,
            summary=summary,
            event_count=event_count,
            confirm_seq=confirm_seq,
            confirm_seq_at=confirm_seq_at or (store.now_iso() if confirm_seq else None),
        )
        if items:
            store.replace_plan_items(conn, plan.id, items)
        return store.get_daily_plan_by_id(conn, plan.id)


def test_confirm_plan_does_not_touch_events(svc):
    first = svc.create_manual(title="组会", start_at="2026-09-07T10:00:00")
    second = svc.ingest_candidate(title="答辩", start_at="2026-09-07T14:00:00")
    plan = _seed_plan(
        svc,
        for_date="2026-09-07",
        confirm_seq=9,
        event_count=2,
        items=[
            {
                "event_id": first.id,
                "title": first.title,
                "start_at": first.start_at,
                "kind": first.kind,
                "sort_order": 0,
                "evidence": {"kind": "event", "event_id": first.id},
            },
            {
                "event_id": second.event.id,
                "title": second.event.title,
                "start_at": second.event.start_at,
                "kind": second.event.kind,
                "sort_order": 1,
                "evidence": {"kind": "event", "event_id": second.event.id},
            },
        ],
    )
    before = _events_snapshot(svc)

    confirmed = svc.confirm_plan(plan.id)
    assert confirmed.status == "confirmed"
    assert confirmed.confirm_seq is None
    assert _events_snapshot(svc) == before
    assert svc.get(second.event.id).status == "pending"


def test_dismiss_plan_does_not_touch_events(svc):
    event = svc.create_manual(title="组会", start_at="2026-09-07T10:00:00")
    plan = _seed_plan(
        svc,
        for_date="2026-09-07",
        confirm_seq=4,
        items=[
            {
                "event_id": event.id,
                "title": event.title,
                "start_at": event.start_at,
                "kind": event.kind,
                "evidence": {"kind": "event", "event_id": event.id},
            }
        ],
    )
    before = _events_snapshot(svc)

    dismissed = svc.dismiss_plan(plan.id)
    assert dismissed.status == "dismissed"
    assert _events_snapshot(svc) == before
    assert svc.get(event.id).status == "confirmed"


def test_empty_plan_confirm_skips_action_log_and_change_rate(svc):
    plan = _seed_plan(
        svc,
        for_date="2026-09-08",
        status="empty",
        summary="明天没有日程。今夜计划为空，不是没跑。",
        event_count=0,
        confirm_seq=None,
    )
    confirmed = svc.confirm_plan(plan.id)
    assert confirmed.status == "confirmed"

    with svc._conn() as conn:
        assert store.list_actions(conn) == []
    report = change_report(svc)
    assert report["total"] == 0
    assert report["accepted"] == 0


def test_nonempty_plan_confirm_counts_in_change_rate(svc):
    plan = _seed_plan(svc, for_date="2026-09-07", confirm_seq=2, event_count=1)
    svc.confirm_plan(plan.id)
    report = change_report(svc)
    assert report["accepted"] == 1
    assert report["total"] == 1


def test_nonempty_plan_dismiss_counts_in_change_rate(svc):
    plan = _seed_plan(svc, for_date="2026-09-07", confirm_seq=2, event_count=1)
    svc.dismiss_plan(plan.id)
    report = change_report(svc)
    assert report["dismissed"] == 1
    assert report["total"] == 1


def test_resolve_by_seq_prefers_pending_event_over_plan(svc):
    result = svc.ingest_candidate(title="组会", start_at="2026-09-07T10:00:00")
    seq = result.event.confirm_seq
    assert seq is not None
    plan = _seed_plan(svc, for_date="2026-09-07", confirm_seq=seq, event_count=1)

    resolved = svc.resolve_by_seq(seq, accept=True)
    assert resolved is not None
    assert getattr(resolved, "id") == result.event.id
    assert svc.get(result.event.id).status == "confirmed"
    assert svc.get_plan("2026-09-07").status == "pending"
    assert plan.status == "pending"


def test_resolve_by_seq_confirms_pending_plan_when_no_event(svc):
    plan = _seed_plan(svc, for_date="2026-09-07", confirm_seq=7, event_count=1)
    resolved = svc.resolve_by_seq(7, accept=True)
    assert resolved is not None
    assert resolved.id == plan.id
    assert resolved.status == "confirmed"


def test_resolve_by_seq_expired_plan_raises(svc):
    stale = (datetime.now() - timedelta(hours=25)).replace(microsecond=0).isoformat()
    _seed_plan(
        svc,
        for_date="2026-09-07",
        confirm_seq=8,
        confirm_seq_at=stale,
        event_count=1,
    )
    with pytest.raises(ConfirmSeqExpired):
        svc.resolve_by_seq(8, accept=True)
    assert svc.get_plan("2026-09-07").status == "pending"


def test_missing_plan_raises(svc):
    with pytest.raises(PlanNotFound):
        svc.confirm_plan("plan_missing")


def test_format_pending_plan_uses_same_reply_shape():
    plan = store.DailyPlan(
        id="plan_2026-09-07",
        for_date="2026-09-07",
        status="pending",
        summary="明天两件事，有 1 处重叠。",
        confirm_seq=3,
    )
    body = format_pending_plan(plan)
    assert body.startswith("【待确认 #3】明日计划")
    assert "明天两件事，有 1 处重叠。" in body
    assert "回复: 确认3 / 忽略3" in body


def test_handle_reply_confirms_and_dismisses_plan(svc):
    notifier = RecordingNotifier()
    dispatcher = PendingDispatcher(service=svc, notifier=notifier)
    plan = _seed_plan(svc, for_date="2026-09-07", confirm_seq=5, event_count=1)

    ack = dispatcher.handle_reply("确认5")
    assert ack is not None and "已确认" in ack and "明日计划" in ack
    assert svc.get_plan("2026-09-07").status == "confirmed"

    other = _seed_plan(svc, for_date="2026-09-08", confirm_seq=6, event_count=1)
    ack = dispatcher.handle_reply("忽略6")
    assert ack is not None and "已忽略" in ack
    assert svc.get_plan("2026-09-08").status == "dismissed"
    assert other.id.startswith("plan_")


def test_notify_pending_sends_plan_after_events(svc):
    notifier = RecordingNotifier()
    dispatcher = PendingDispatcher(service=svc, notifier=notifier)
    result = svc.ingest_candidate(title="组会", start_at="2026-09-07T10:00:00")
    plan = _seed_plan(
        svc,
        for_date="2026-09-07",
        confirm_seq=result.event.confirm_seq + 1,
        summary="明天先组会。",
        event_count=1,
    )

    sent = dispatcher.notify_pending()
    assert sent == [result.event.id, plan.id]
    assert notifier.sent[0].startswith("【待确认 #1】组会")
    assert notifier.sent[1].startswith(f"【待确认 #{plan.confirm_seq}】明日计划")
    assert "明天先组会。" in notifier.sent[1]


def test_notify_pending_sends_plan_when_no_events(svc):
    notifier = RecordingNotifier()
    dispatcher = PendingDispatcher(service=svc, notifier=notifier)
    plan = _seed_plan(svc, for_date="2026-09-07", confirm_seq=2, event_count=1)
    assert dispatcher.notify_pending() == [plan.id]


def test_notify_pending_skips_plans_when_caller_passes_events(svc):
    notifier = RecordingNotifier()
    dispatcher = PendingDispatcher(service=svc, notifier=notifier)
    result = svc.ingest_candidate(title="组会", start_at="2026-09-07T10:00:00")
    _seed_plan(svc, for_date="2026-09-07", confirm_seq=9, event_count=1)
    sent = dispatcher.notify_pending([result.event])
    assert sent == [result.event.id]
    assert all("明日计划" not in body for body in notifier.sent)


def test_list_agenda_follows_confirmed_plan_sort_order(svc):
    tomorrow = (datetime.now().date() + timedelta(days=1)).isoformat()
    early = svc.create_manual(title="早会", start_at=f"{tomorrow}T09:00:00")
    late = svc.create_manual(title="晚会", start_at=f"{tomorrow}T18:00:00")
    assert [e.title for e in svc.list_agenda()] == ["早会", "晚会"]

    plan = _seed_plan(
        svc,
        for_date=tomorrow,
        status="pending",
        event_count=2,
        items=[
            {
                "event_id": late.id,
                "title": late.title,
                "start_at": late.start_at,
                "kind": late.kind,
                "sort_order": 0,
                "evidence": {"kind": "event", "event_id": late.id},
            },
            {
                "event_id": early.id,
                "title": early.title,
                "start_at": early.start_at,
                "kind": early.kind,
                "sort_order": 1,
                "evidence": {"kind": "event", "event_id": early.id},
            },
        ],
    )
    assert [e.title for e in svc.list_agenda()] == ["早会", "晚会"]
    svc.confirm_plan(plan.id)
    assert [e.title for e in svc.list_agenda()] == ["晚会", "早会"]


def test_morning_report_renders_plan_states(svc):
    cheap = CheapApiSource("zhipu-air", model="m", base_url="http://x/v1", api_key="k")
    cheap._probe_fn = lambda s: SourceStatus(
        s.name, s.kind, HealthStatus.HEALTHY, None, "t", 0
    )
    pool = QuotaPool({"zhipu-air": cheap}, order=["zhipu-air"])
    today = datetime(2026, 9, 7, 7, 30)

    text = format_morning(build_morning(svc, pool, now=today))
    assert "昨夜计划: 无" in text

    _seed_plan(svc, for_date="2026-09-07", status="empty", event_count=0, summary="空")
    assert "昨夜计划: 空" in format_morning(build_morning(svc, pool, now=today))

    _seed_plan(svc, for_date="2026-09-07", status="pending", event_count=1, confirm_seq=1)
    assert "昨夜计划: 待批" in format_morning(build_morning(svc, pool, now=today))

    plan = svc.get_plan("2026-09-07")
    assert plan is not None
    svc.confirm_plan(plan.id)
    assert "昨夜计划: 已批" in format_morning(build_morning(svc, pool, now=today))

    _seed_plan(svc, for_date="2026-09-07", status="pending", event_count=1, confirm_seq=2)
    plan = svc.get_plan("2026-09-07")
    assert plan is not None
    svc.dismiss_plan(plan.id)
    assert "昨夜计划: 已忽略" in format_morning(build_morning(svc, pool, now=today))


def test_format_morning_unknown_plan_status_is_missing():
    data = {
        "date": "2026-09-07",
        "change": {"days": 7, "total": 0, "rate": None, "meets_target": True, "target": 0.2},
        "pending": [],
        "quota": [],
        "plan": {"status": "mystery"},
    }
    assert "昨夜计划: 无" in format_morning(data)
