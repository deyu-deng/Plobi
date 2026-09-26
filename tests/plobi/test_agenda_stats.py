"""改动率仪表：台账埋点与口径（切片 A5，MVP §3.1）。"""

from __future__ import annotations

from datetime import datetime

import pytest

from plobi.agenda.service import AgendaService
from plobi.agenda.stats import change_report, format_report


@pytest.fixture()
def svc(tmp_path):
    return AgendaService(tmp_path / "agenda.db")


def test_confirm_dismiss_and_overrides_are_ledgered(svc):
    accepted = svc.ingest_candidate(title="组会", start_at="2026-08-26T16:00:00")
    svc.confirm(accepted.event.id)

    rejected = svc.ingest_candidate(title="临时会", start_at="2026-08-26T10:00:00")
    svc.dismiss(rejected.event.id)

    report = change_report(svc)
    assert report["accepted"] == 1
    assert report["dismissed"] == 1
    assert report["total"] == 2
    assert report["rate"] == 0.5
    assert not report["meets_target"]


def test_manual_source_entries_do_not_count(svc):
    entry = svc.create_manual(title="自习", start_at="2026-08-26T19:00:00")
    svc.update_manual(entry.id, title="自习（改）")
    svc.delete(entry.id)

    report = change_report(svc)
    assert report["total"] == 0
    assert report["rate"] is None
    assert report["meets_target"]  # 无数据不评定


def test_editing_a_system_entry_counts_as_override(svc):
    original = svc.create_manual(title="组会", start_at="2026-08-26T15:00:00")
    change = svc.ingest_candidate(
        title="组会", start_at="2026-08-26T16:00:00", target_event_id=original.id
    )
    assert svc.get(change.event.id).source == "wechat"
    svc.update_manual(original.id, title="组会（人改）")

    report = change_report(svc)
    assert report["manual_edits"] == 1
    assert report["overrides"] == 1


def test_window_bounds_exclude_actions_outside(svc, tmp_path):
    """上界过滤：窗口结束前的动作不计入（store 层验证边界语义）。"""
    from plobi.agenda import store

    result = svc.ingest_candidate(title="组会", start_at="2026-08-26T16:00:00")
    svc.confirm(result.event.id)

    conn = store.connect(tmp_path / "agenda.db")
    try:
        assert store.list_actions(conn) != []
        future_only = store.list_actions(conn, since="2999-01-01T00:00:00")
        assert future_only == []
        past_only = store.list_actions(conn, until="2000-01-01T00:00:00")
        assert past_only == []
    finally:
        conn.close()


def test_format_report_renders_target_and_counts(svc):
    result = svc.ingest_candidate(title="组会", start_at="2026-08-26T16:00:00")
    svc.confirm(result.event.id)

    text = format_report(change_report(svc))
    assert "改动率日报" in text
    assert "确认 1" in text
    assert "目标" in text

    empty = format_report(
        change_report(svc, now=datetime(2999, 1, 1, 12, 0))
    )
    assert "暂无落定动作" in empty
