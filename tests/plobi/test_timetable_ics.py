"""Timetable (.ics) collector: parse, dedupe, import, board API (WP-COLLECTOR-ICS).

The fixture mirrors the real Celechron export's structure (TZID parameters,
``SUMMARY;LANGUAGE``, ``教师:`` DESCRIPTION line, per-event VALARM) in three
truncated VEVENTs — the 225-entry personal calendar itself never enters git.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plobi.agenda import store as agenda_store
from plobi.agenda.service import AgendaService
from plobi.collectors.chatlog import collect_api
from plobi.collectors.timetable import (
    IcsParseError,
    TimetableCollector,
    import_ics,
    parse_ics,
    parse_ics_with_stats,
)
from plobi.collectors.timetable import api as timetable_api

# Truncated clone of the real export: same property shapes, 3 usable VEVENTs
# (one of them a weekly RRULE), one DTSTART-less event, VALARMs everywhere.
ICS_FIXTURE = """BEGIN:VCALENDAR\r
X-WR-CALNAME:测试课表-2026秋冬\r
PRODID:-//Celechron//Course Calendar 1.0//CN\r
VERSION:2.0\r
METHOD:PUBLISH\r
BEGIN:VTIMEZONE\r
TZID:Asia/Shanghai\r
BEGIN:STANDARD\r
DTSTART:16010101T000000\r
TZOFFSETFROM:+0800\r
TZOFFSETTO:+0800\r
END:STANDARD\r
END:VTIMEZONE\r
BEGIN:VEVENT\r
CLASS:PUBLIC\r
DESCRIPTION:教师: 孙晖\r
DTSTAMP:20260912T085200Z\r
DTSTART;TZID=Asia/Shanghai:20260914T100000\r
DTEND;TZID=Asia/Shanghai:20260914T122500\r
LOCATION:紫金港西2-415\r
SUMMARY;LANGUAGE=zh-cn:电工电子学\r
UID:1f29de0e68b04b86f1528ab6cabe3b88e6d2b2a7\r
BEGIN:VALARM\r
TRIGGER:-PT15M\r
ACTION:DISPLAY\r
DESCRIPTION:提醒\r
END:VALARM\r
END:VEVENT\r
BEGIN:VEVENT\r
CLASS:PUBLIC\r
DESCRIPTION:教师: 孙晖\r
DTSTAMP:20260912T085200Z\r
DTSTART;TZID=Asia/Shanghai:20260921T100000\r
DTEND;TZID=Asia/Shanghai:20260921T122500\r
LOCATION:紫金港西2-415\r
SUMMARY;LANGUAGE=zh-cn:电工电子学\r
UID:3a7cc0f1e9d84c22b1a5f0c8d4e2b6a9\r
BEGIN:VALARM\r
TRIGGER:-PT15M\r
ACTION:DISPLAY\r
DESCRIPTION:提醒\r
END:VALARM\r
END:VEVENT\r
BEGIN:VEVENT\r
CLASS:PUBLIC\r
DESCRIPTION:教师: 王晓珂\r
DTSTAMP:20260912T085200Z\r
DTSTART;TZID=Asia/Shanghai:20260901T080000\r
DTEND;TZID=Asia/Shanghai:20260901T093000\r
RRULE:FREQ=WEEKLY;COUNT=3\r
LOCATION:紫金港东1-201\r
SUMMARY;LANGUAGE=zh-cn:数学分析\r
UID:rrule-math-001\r
END:VEVENT\r
BEGIN:VEVENT\r
CLASS:PUBLIC\r
DTSTAMP:20260912T085200Z\r
LOCATION:紫金港西2-301\r
SUMMARY;LANGUAGE=zh-cn:缺开始时间的课\r
UID:broken-no-dtstart\r
END:VEVENT\r
END:VCALENDAR\r
"""


@pytest.fixture()
def ics_file(tmp_path):
    path = tmp_path / "schedule.ics"
    path.write_text(ICS_FIXTURE, encoding="utf-8", newline="")
    return path


@pytest.fixture()
def service(tmp_path):
    return AgendaService(tmp_path / "agenda.db")


def _wide_events(service: AgendaService):
    """All non-cancelled events across the whole semester window."""
    with service._conn() as conn:
        return agenda_store.list_events(
            conn,
            start_from=datetime(2026, 8, 1),
            start_to=datetime(2027, 1, 31),
        )


# --- parse ------------------------------------------------------------------


def test_parse_counts_and_core_fields(ics_file):
    occurrences = parse_ics(ics_file)

    # 2 single VEVENTs + a COUNT=3 weekly RRULE = 5 concrete sessions;
    # the DTSTART-less VEVENT is skipped, never defaulted to 9:00.
    assert len(occurrences) == 5
    assert all(o.source == "timetable" and o.kind == "class" for o in occurrences)

    elec = next(o for o in occurrences if o.title == "电工电子学")
    assert elec.start_at == "2026-09-14T10:00:00"
    assert elec.end_at == "2026-09-14T12:25:00"
    assert elec.location == "紫金港西2-415"
    assert elec.external_id == "1f29de0e68b04b86f1528ab6cabe3b88e6d2b2a7"
    assert elec.evidence["teacher"] == "孙晖"
    assert elec.evidence["uid"] == elec.external_id
    assert elec.evidence["calendar_name"] == "测试课表-2026秋冬"
    assert elec.evidence["location"] == "紫金港西2-415"


def test_parse_converts_tzid_to_naive_local_shanghai(ics_file):
    occurrences = parse_ics(ics_file)

    # DTSTART;TZID=Asia/Shanghai must land as offset-free local ISO — the
    # events-table format. No "+08:00" anywhere.
    for occurrence in occurrences:
        assert "+" not in occurrence.start_at
        assert occurrence.start_at == datetime.fromisoformat(
            occurrence.start_at
        ).isoformat()


def test_parse_is_idempotent(ics_file):
    first, second = parse_ics(ics_file), parse_ics(ics_file)

    assert first == second
    # Same file parsed twice → same stable dedupe keys, no counter drift.
    assert [o.external_id for o in first] == [o.external_id for o in second]


def test_valarm_never_reaches_occurrences_or_evidence(ics_file):
    occurrences, stats = parse_ics_with_stats(ics_file)

    assert stats["total"] == 4, "four VEVENTs in the fixture"
    assert len(occurrences) == 5, "RRULE expands; broken event skipped"
    for occurrence in occurrences:
        blob = repr(occurrence.evidence)
        assert "提醒" not in blob
        assert "VALARM" not in blob
        assert "TRIGGER" not in blob


def test_rrule_expands_to_concrete_dates(ics_file):
    occurrences = parse_ics(ics_file)

    math = sorted(
        (o for o in occurrences if o.title == "数学分析"),
        key=lambda o: o.start_at,
    )
    assert len(math) == 3
    assert [o.start_at for o in math] == [
        "2026-09-01T08:00:00",
        "2026-09-08T08:00:00",
        "2026-09-15T08:00:00",
    ]
    # Each lecture is its own stable dedupe key; the original UID is retained.
    assert len({o.external_id for o in math}) == 3
    assert all(o.external_id.startswith("rrule-math-001:") for o in math)


def test_missing_dtstart_is_skipped_and_counted(ics_file):
    occurrences, stats = parse_ics_with_stats(ics_file)

    assert stats["skipped_missing_dtstart"] == 1
    assert all(o.title != "缺开始时间的课" for o in occurrences)
    assert not any("9:00" in o.start_at[-8:] for o in occurrences)


def test_parse_garbage_file_raises(tmp_path):
    bad = tmp_path / "bad.ics"
    bad.write_text("this is not a calendar", encoding="utf-8")

    with pytest.raises(IcsParseError):
        parse_ics(bad)


# --- collector protocol -----------------------------------------------------


def test_collector_health_and_pull(ics_file, tmp_path):
    collector = TimetableCollector(ics_file)

    assert collector.source_id == "timetable"
    assert collector.health()["ok"] is True

    pulled = collector.pull()
    assert len(pulled) == 5

    missing = TimetableCollector(tmp_path / "nope.ics")
    assert missing.health()["ok"] is False


# --- import via the shared ingest path ---------------------------------------


def test_import_lands_confirmed_timetable_rows(ics_file, service):
    result = import_ics(ics_file, service=service)

    assert result["created"] == 5
    events = _wide_events(service)
    assert len(events) == 5
    assert all(e.status == "confirmed" for e in events)
    assert all(e.source == "timetable" for e in events)
    assert all(e.kind == "class" for e in events)


def test_reimport_same_file_is_a_noop(ics_file, service):
    import_ics(ics_file, service=service)
    result = import_ics(ics_file, service=service)

    assert result["created"] == 0
    assert result["updated"] == 0
    assert result["unchanged"] == 5
    assert len(_wide_events(service)) == 5, "no duplicate rows"


def test_changed_dtstart_becomes_pending_change_with_prev_value(
    ics_file, service, tmp_path
):
    import_ics(ics_file, service=service)

    edited = tmp_path / "edited.ics"
    edited.write_text(
        ICS_FIXTURE.replace("DTSTART;TZID=Asia/Shanghai:20260921T100000",
                            "DTSTART;TZID=Asia/Shanghai:20260921T140000"),
        encoding="utf-8",
        newline="",
    )
    result = import_ics(edited, service=service)

    assert result["updated"] == 1
    assert result["created"] == 0

    pending = [e for e in _wide_events(service) if e.status == "pending"]
    assert len(pending) == 1
    assert pending[0].title == "电工电子学"
    assert pending[0].start_at == "2026-09-21T14:00:00"
    # ADR-0009: the pre-change value is snapshotted for rollback.
    assert pending[0].prev_value["start_at"] == "2026-09-21T10:00:00"
    # The other four rows stay confirmed and untouched.
    assert len([e for e in _wide_events(service) if e.status == "confirmed"]) == 4


def test_missing_rows_are_never_deleted(ics_file, service, tmp_path):
    import_ics(ics_file, service=service)

    partial = tmp_path / "partial.ics"
    # Only the first VEVENT survives: the other sessions "disappear" from the
    # source, but the agenda keeps them (sources are additive).
    head = ICS_FIXTURE.split("BEGIN:VEVENT")
    partial.write_text(
        head[0] + "BEGIN:VEVENT" + head[1] + "END:VCALENDAR\r\n",
        encoding="utf-8",
        newline="",
    )
    import_ics(partial, service=service)

    events = _wide_events(service)
    assert len(events) == 5
    assert len([e for e in events if e.title == "电工电子学"]) == 2


def test_uid_dedupes_across_confirmed_and_pending(ics_file, service):
    # First import lands pending; second import with apply="confirmed" must
    # still match the same rows and change nothing (not duplicate).
    import_ics(ics_file, service=service, apply="pending")
    result = import_ics(ics_file, service=service, apply="confirmed")

    assert result["created"] == 0
    assert result["unchanged"] == 5


# --- board API ---------------------------------------------------------------


@pytest.fixture()
def api_client(tmp_path, monkeypatch):
    service = AgendaService(tmp_path / "agenda.db")
    monkeypatch.setattr(timetable_api, "_get_service", lambda: service)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    monkeypatch.setattr(timetable_api, "_status_path", lambda: tmp_path / "hermes-home" / "plobi" / "timetable.json")

    app = FastAPI()
    app.include_router(collect_api.router, prefix="/api/collect")
    with TestClient(app) as client:
        yield client


def test_preview_returns_structure_and_writes_nothing(api_client, ics_file):
    response = api_client.post(
        "/api/collect/timetable/preview", json={"path": str(ics_file)}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["calendar_name"] == "测试课表-2026秋冬"
    assert body["count"] == 5
    assert body["courses"] == 2
    assert body["first"] == "2026-09-01T08:00:00"
    assert body["last"] == "2026-09-21T10:00:00"
    assert len(body["sample"]) == 5
    assert {"title", "start_at", "end_at", "location"} <= set(body["sample"][0])


def test_preview_writes_no_db_rows(api_client, ics_file, tmp_path):
    api_client.post("/api/collect/timetable/preview", json={"path": str(ics_file)})

    # The preview's service is a temp DB; assert it stayed empty.
    service = timetable_api._get_service()
    assert _wide_events(service) == []


def test_import_returns_counts_and_writes_status(api_client, ics_file):
    response = api_client.post(
        "/api/collect/timetable/import", json={"path": str(ics_file)}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 5 and body["updated"] == 0 and body["unchanged"] == 0
    assert body["calendar_name"] == "测试课表-2026秋冬"

    status_response = api_client.get("/api/collect/timetable")
    assert status_response.status_code == 200
    status = status_response.json()
    assert status["calendar_name"] == "测试课表-2026秋冬"
    assert status["event_count"] == 5
    assert status["path"] == str(ics_file)
    assert status["imported_at"]

    # Second import through the API → unchanged, and status persisted.
    again = api_client.post(
        "/api/collect/timetable/import", json={"path": str(ics_file)}
    ).json()
    assert again["unchanged"] == 5 and again["created"] == 0


def test_status_is_404_before_first_import(api_client):
    assert api_client.get("/api/collect/timetable").status_code == 404


def test_import_rejects_relative_and_traversal_paths(api_client, ics_file):
    relative = api_client.post(
        "/api/collect/timetable/import", json={"path": "relative/schedule.ics"}
    )
    assert relative.status_code == 400

    traversal = str(ics_file.parent / ".." / ics_file.name)
    assert ".." in traversal
    response = api_client.post(
        "/api/collect/timetable/import", json={"path": traversal}
    )
    assert response.status_code == 400

    preview = api_client.post(
        "/api/collect/timetable/preview", json={"path": "schedule.ics"}
    )
    assert preview.status_code == 400


def test_import_rejects_missing_file_with_400(api_client, tmp_path):
    response = api_client.post(
        "/api/collect/timetable/import",
        json={"path": str(tmp_path / "nope.ics")},
    )
    assert response.status_code == 400


def test_import_defaults_to_confirmed_not_manual(api_client, ics_file):
    body = api_client.post(
        "/api/collect/timetable/import", json={"path": str(ics_file)}
    ).json()

    assert body["created"] == 5
    service = timetable_api._get_service()
    events = _wide_events(service)
    assert all(e.source == "timetable" for e in events), "never fakes source=manual"
    assert all(e.status == "confirmed" for e in events)
