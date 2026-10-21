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
from plobi.pool import query as pool_query, stats as pool_stats

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


@pytest.fixture(autouse=True)
def pool_db(tmp_path, monkeypatch):
    """把信息池钉进 tmp_path —— **不钉就是拿真的共享 `pool.db` 跑测试**。

    落点从日程改成池之后这一档必须存在：`plobi.pool.connect()` 不只是读，它会
    `CREATE TABLE` / `ALTER` 真的那份库（这台的读侧函数其实会写库那一族的坑）。
    """
    from plobi.pool import store as pool_store

    db = tmp_path / "pool.db"
    monkeypatch.setattr(pool_store, "path", lambda: db)
    return db


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


def test_import_lands_the_pool_and_writes_no_agenda_rows(ics_file, service, pool_db):
    """裁定 193 的靶子那一格：课表**一条都不许进 `events`**，全进池。"""
    result = import_ics(ics_file)

    assert result["created"] == 5 and result["landing"] == "pool"
    assert result["source"] == "timetable"
    assert _wide_events(service) == [], "采集器不再直落日程——一行都不行"
    rows = pool_query(source="timetable", db_path=pool_db)
    assert len(rows) == 5
    assert {row["kind"] for row in rows} == {"class"}


def test_reimport_same_file_is_a_noop(ics_file, service, pool_db):
    """幂等判据那一半：连着 land 两轮，池的 `total` 不涨。"""
    first = import_ics(ics_file)
    second = import_ics(ics_file)

    assert first["created"] == 5
    assert second["created"] == 0 and second["updated"] == 0
    assert pool_stats()["total"] == 5, "重复采集不许长出第二份"
    assert _wide_events(service) == []


def test_changed_dtstart_updates_the_pool_row_and_no_pending_card(
    ics_file, service, tmp_path, pool_db
):
    """**这一笔删掉的功能**要留一条钉子，别让下一个人当 bug 补回来。

    旧形状：改一节课的时间 ⇒ 日程那侧长一张待确认卡并快照 `prev_value` 好回滚（ADR-0009）。
    新形状（裁定 186／193）：池里那一行被 UPDATE（`external_id` 才是身份，uid 没变 ⇒ 同一行），
    待确认卡与 `prev_value` 在这一条信源上**没有了**——要不要跟着改日程是读池的 Agent 的判断。
    """
    import_ics(ics_file)

    edited = tmp_path / "edited.ics"
    edited.write_text(
        ICS_FIXTURE.replace("DTSTART;TZID=Asia/Shanghai:20260921T100000",
                            "DTSTART;TZID=Asia/Shanghai:20260921T140000"),
        encoding="utf-8",
        newline="",
    )
    result = import_ics(edited)

    assert result["updated"] == 1 and result["created"] == 0
    row = next(r for r in pool_query(source="timetable", db_path=pool_db)
               if r["external_id"] == "3a7cc0f1e9d84c22b1a5f0c8d4e2b6a9")
    assert row["occurred_at"] == "2026-09-21 14:00:00", "时间在写入口归一成一个形状（裁 189）"
    assert pool_stats()["total"] == 5, "改了时间是同一行，不是多一行"
    assert _wide_events(service) == []
    assert service.list_pending() == [], "课表不再产生待确认卡（这一格是删掉的功能）"


def test_missing_rows_are_never_deleted(ics_file, tmp_path, pool_db):
    """文件里少几条 ⇒ 池里那些**留着**（能力位 `authoritative_replace` 声明的就是 False）。

    为什么是 False、不跟着学在浙大填 True：`pull()` 的缺席分不清是**停课**、**改了排次**
    还是**导出少导了一节**，没实测就不许拿缺席当取消（裁定 191 那句口径）。
    """
    import_ics(ics_file)

    partial = tmp_path / "partial.ics"
    head = ICS_FIXTURE.split("BEGIN:VEVENT")
    partial.write_text(
        head[0] + "BEGIN:VEVENT" + head[1] + "END:VCALENDAR\r\n",
        encoding="utf-8",
        newline="",
    )
    result = import_ics(partial)

    assert result["created"] == 0
    assert len(pool_query(source="timetable", db_path=pool_db)) == 5, "少几条不删任何东西"


def test_external_id_is_the_identity_not_the_title(ics_file, tmp_path, pool_db):
    """同标题不同 uid ⇒ 各成一行；**改标题不把老行顶掉**。

    钉的是 `(source, external_id)` 那把唯一键：身份是学校给的那一条 id，不是标题拼的。
    拿标题当身份，一节课改个名字就在池里"消失"一次。
    """
    import_ics(ics_file)
    before = pool_query(source="timetable", db_path=pool_db)
    assert len([r for r in before if r["title"] == "电工电子学"]) == 2, "同标题不同 uid ⇒ 各成一行"

    renamed = tmp_path / "renamed.ics"
    renamed.write_text(ICS_FIXTURE.replace("SUMMARY;LANGUAGE=zh-cn:电工电子学",
                                           "SUMMARY;LANGUAGE=zh-cn:电工电子学改名了"),
                       encoding="utf-8", newline="")
    result = import_ics(renamed)

    rows = pool_query(source="timetable", db_path=pool_db)
    assert len(rows) == 5, "改名不长新行"
    assert result["created"] == 0 and result["updated"] == 2
    assert len([r for r in rows if "改名了" in (r["title"] or "")]) == 2


# --- board API ---------------------------------------------------------------


@pytest.fixture()
def api_client(tmp_path, monkeypatch):
    service = AgendaService(tmp_path / "agenda.db")
    monkeypatch.setattr(timetable_api, "_get_service", lambda: service)
    monkeypatch.setenv("PLOBI_HOME", str(tmp_path / "plobi-home"))
    monkeypatch.setattr(timetable_api, "_status_path", lambda: tmp_path / "plobi-home" / "plobi" / "timetable.json")

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
    """面上那两键的**分工**：`event_count`＝历史直落行，`pool_count`＝这一家现在的进账。

    导入之后日程库里 0 行、池里 5 条 ⇒ 既不能拿"库里有货"冒充"这一轮采到了"，
    也不能反过来把新落点读成 404。`unchanged` 那一格池那层恒 0（内容一样就 continue，
    它不计数）——所以判的是 `total` 不涨，不是 `unchanged == 5`。
    """
    response = api_client.post(
        "/api/collect/timetable/import", json={"path": str(ics_file)}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 5 and body["landing"] == "pool"
    assert body["calendar_name"] == "测试课表-2026秋冬"
    assert "不生效" in body["apply_note"]

    status_response = api_client.get("/api/collect/timetable")
    assert status_response.status_code == 200
    status = status_response.json()
    assert status["event_count"] == 0, "一行都没进日程库"
    assert status["pool_count"] == 5, "新落点读得到"
    assert status["path"] == str(ics_file)

    again = api_client.post(
        "/api/collect/timetable/import", json={"path": str(ics_file)}
    ).json()
    assert again["created"] == 0 and again["updated"] == 0


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


def test_apply_is_accepted_but_changes_nothing(api_client, ics_file):
    """老桌面还会送 `apply`。这一条**替换**旧那句 defaults to confirmed not manual——

    那句在池落点下是假话；而且旧断言在空库上是 `all([])` 那种**真空绿**（零条也算全真），
    正好是这一族反复量到的那类假绿，所以换成"送 pending 也不许往日程里造东西"。
    """
    body = api_client.post(
        "/api/collect/timetable/import",
        json={"path": str(ics_file), "apply": "pending"},
    ).json()

    assert body["created"] == 5 and body["apply"] == "pending"
    service = timetable_api._get_service()
    assert _wide_events(service) == [], "送 pending 也不许往日程里造东西"
    assert service.list_pending() == []
