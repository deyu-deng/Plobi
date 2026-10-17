"""``GET /api/collect/timetable`` 的真源归位（WP-SOCIAL-SOURCES-PLUGGABLE 判据①）。

钉的是这一件事实：**计数与最后一次真落库时刻只能从库里派生**，状态文件只回答
"这份课表是哪来的"。改之前两者都读 ``$PLOBI_HOME/plobi/timetable.json``，那份一次性
旁证不在就直接 404 —— 而当时库里躺着 240 条 ``kind=class``，于是"有货"在面上被读成
"从没导入过"。

跑法：``bash scripts/run_tests.sh tests/plobi/test_collect_timetable_status.py -q``
（唯一入口）。不碰真家目录：日程库与状态文件都指到 tmp。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest
from fastapi import HTTPException

from plobi.agenda import AgendaService
from plobi.collectors.timetable import api


def _make_db(path, rows):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE events (id TEXT PRIMARY KEY, source TEXT, kind TEXT,"
        " title TEXT, status TEXT, created_at TEXT, updated_at TEXT)"
    )
    for i, (source, created, updated) in enumerate(rows):
        conn.execute(
            "INSERT INTO events (id, source, kind, title, status, created_at, updated_at)"
            " VALUES (?, ?, 'class', ?, 'confirmed', ?, ?)",
            (f"e{i}", source, f"课 {i}", created, updated),
        )
    conn.commit()
    conn.close()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """把两个落点都指到 tmp：日程库 + 状态文件。"""
    db = tmp_path / "agenda.db"
    status = tmp_path / "timetable.json"
    # 注入 service（这个 API 写入的就是它），而不是另指一个默认解析结果。
    monkeypatch.setattr(api, "_get_service", lambda: AgendaService(db))
    monkeypatch.setattr(api, "_status_path", lambda: status)
    return db, status


def _get():
    return asyncio.run(api.timetable_status())


def test_count_comes_from_the_db_when_the_status_file_is_gone(env):
    """那 240 条的场景：文件没了，库里有货 ⇒ 必须 200，且计数是库里的数。"""
    db, status = env
    _make_db(db, [("timetable", "2026-10-09T18:41:33", "2026-10-16T08:36:11")] * 3)
    assert not status.exists()

    body = _get()

    assert body["event_count"] == 3
    assert body["imported_at"] == "2026-10-09T18:41:33"
    # 来源说明确实只剩状态文件能给：文件不在就是空串，不拿库去编一个出处。
    assert body["path"] == "" and body["calendar_name"] == ""


def test_status_file_can_no_longer_overstate_the_count(env):
    """状态文件里那个数不再作数——它是"两处真源"里旧的那一处。"""
    db, status = env
    _make_db(db, [("timetable", "2026-10-09T00:00:00", "2026-10-09T00:00:00")] * 2)
    status.write_text(
        json.dumps({"path": "D:/课表.ics", "calendar_name": "2026 秋",
                    "event_count": 999, "imported_at": "2020-01-01T00:00:00"}),
        encoding="utf-8",
    )

    body = _get()

    assert body["event_count"] == 2          # 库里的数，不是文件里的 999
    assert body["imported_at"] == "2026-10-09T00:00:00"
    assert (body["path"], body["calendar_name"]) == ("D:/课表.ics", "2026 秋")


def test_empty_db_still_reads_as_never_imported(env):
    _make_db(env[0], [])

    with pytest.raises(HTTPException) as caught:
        _get()

    assert caught.value.status_code == 404


def test_a_store_that_was_never_created_still_reads_as_never_imported(env):
    """库文件还没建（一次都没写过）＝"没导入"，不是"读不到"。"""
    with pytest.raises(HTTPException) as caught:
        _get()

    assert caught.value.status_code == 404


def test_an_existing_but_unreadable_store_is_a_fault_not_a_zero(env):
    """修一个假阴性不许换来下一个：库在、读不出来时说"读不到"，不说"没导入"。"""
    db, _ = env
    db.write_bytes(b"this is not an agenda database at all")

    with pytest.raises(HTTPException) as caught:
        _get()

    assert caught.value.status_code == 503


def test_other_sources_do_not_leak_into_the_timetable_count(env):
    db, _ = env
    _make_db(db, [
        ("timetable", "2026-10-09T00:00:00", "2026-10-09T00:00:00"),
        ("zju", "2026-10-10T00:00:00", "2026-10-10T00:00:00"),
        ("manual", "2026-10-11T00:00:00", "2026-10-11T00:00:00"),
    ])

    assert _get()["event_count"] == 1


def test_the_timetable_source_has_no_auto_pull_implementation():
    """判据②（P6：不许描述不存在的机制）钉成机器能查的形状。

    这一条信源只有"读用户自己放进来的本地 .ics"。库里那 240 条来自 2026-10-09 那一次
    导入，**没有任何一方去教务接口拉过**。所以面上与文档都不许写"已能自动拉取"。
    哪天真加了拉取实现，这条会红——那时要同时改口径与判据，别让它悄悄不一致。
    """
    import pathlib
    import re

    root = pathlib.Path(api.__file__).parent
    needles = re.compile(r"\b(httpx|requests|urllib\.request|urllib\.http|aiohttp|http\.client)\b")
    offenders = [
        f"{path.name}:{lineno}"
        for path in sorted(root.glob("*.py"))
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if needles.search(line)
    ]

    assert offenders == [], f"课表这一条出现了 HTTP 客户端，口径要一起改：{offenders}"
