"""WP-APP-DAY-SINK —「同一份快照，两条腿条数不差」这条判据的免设备档。

手机 App 的**一周清单**走 ``GET /api/handset/agenda?from=&to=``（分桶用），**点进某一天**
走 ``GET /api/handset/agenda/day?date=`` —— 后者就是桌面看板那块 ``day_surface``（``_render_day``）。
架构师 2026-10-13 给的关门判据是「模拟器看到的今日/明日/待确认与桌面 ``day_surface``
同一份快照条数不差」。这条**不需要真机、不需要 LAN、不需要 provider** 就能验，
而且第一次验就抓出真缺陷：App 曾把**裸日期**当 ``to`` 发出去，而后端 ``to`` 是闭区间的
**时刻**端点（``store.normalize_dt('2026-10-11')`` → ``2026-10-11T00:00:00``），于是周日整天
被滤掉（8 条只剩 6 条）。鸿蒙侧已修（``Plobi-Harmony-App`` ``3fdb80e``：``dayStartBound`` /
``dayEndBound``）。本文件钉两件事：

① 补到日界的周窗口 == 该周 7 份单日快照的并集（**条数与 id 集合都一致**）；
② 裸日期当 ``to`` 会漏掉最后一整天 —— 谁哪天把 ``normalize_dt`` 的语义改了，这条就红，
   提醒「后端」与「两个前端」必须同步改，不许一侧悄悄换成日界语义。

不碰写入面、不起 HTTP、不依赖本机时钟之外的东西：全部在 tmp 库里跑。
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import pytest

from plobi.agenda import store
from plobi.agenda.router import _render_day
from plobi.agenda.service import AgendaService

MONDAY = "2026-10-05"
SUNDAY = "2026-10-11"
WEEK = ["2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08",
        "2026-10-09", "2026-10-10", "2026-10-11"]


@pytest.fixture()
def svc(tmp_path, monkeypatch):
    db = tmp_path / "agenda.db"
    monkeypatch.setenv("PLOBI_AGENDA_DB", str(db))
    service = AgendaService(db)
    yield service
    monkeypatch.delenv("PLOBI_AGENDA_DB", raising=False)


def _seed(service: AgendaService) -> dict[str, str]:
    """一周七天各钉一条 confirmed（时刻递增），周日那条刻意放到下午。"""
    ids: dict[str, str] = {}
    for index, day in enumerate(WEEK):
        minute = 10 + index
        event = service.create_manual(
            title="day %s" % day,
            start_at="%sT09:%02d:00" % (day, minute),
        )
        ids[day] = event.id
    sunday_afternoon = service.create_manual(
        title="sunday afternoon", start_at="%sT15:30:00" % SUNDAY
    )
    ids["sunday-afternoon"] = sunday_afternoon.id
    return ids


def _day_snapshot_ids(conn: Any, day: str) -> list[str]:
    return [row["id"] for row in _render_day(conn, day)["events"]]


def test_week_window_and_day_snapshots_are_the_same_snapshot(svc):
    """判据本体：补到日界的周窗口 == 7 份单日快照的并集，条数与 id 集合都不差。"""
    ids = _seed(svc)
    conn = store.connect(svc.db_path)
    try:
        week = svc.list_agenda(MONDAY + "T00:00:00", SUNDAY + "T23:59:59")
        union: list[str] = []
        per_day: dict[str, int] = {}
        for day in WEEK:
            day_ids = _day_snapshot_ids(conn, day)
            per_day[day] = len(day_ids)
            union.extend(day_ids)
    finally:
        conn.close()

    assert sorted(e.id for e in week) == sorted(union), (
        "周列表与单日快照不是同一份快照：App 的周日那一列会与点进去的内容不一致"
    )
    assert len(week) == len(ids) == 8
    # 每天各自一条，周日两条（下午那条也算周日）——逐日条数是"条数不差"的最小可读形状
    assert per_day == {
        "2026-10-05": 1, "2026-10-06": 1, "2026-10-07": 1, "2026-10-08": 1,
        "2026-10-09": 1, "2026-10-10": 1, "2026-10-11": 2,
    }


def test_bare_date_upper_bound_drops_the_last_day(svc):
    """把踩过的坑钉成语义：``to`` 是时刻端点，裸日期 = 那天 00:00:00，整天丢光。"""
    ids = _seed(svc)
    with svc._conn() as conn:
        bare = svc.list_agenda(MONDAY, SUNDAY)
        day_rows = _day_snapshot_ids(conn, SUNDAY)
    kept = {e.id for e in bare}

    assert len(bare) == 6, "to 的语义变了：这条要红着提醒同步改两个前端与文档"
    assert ids["sunday-afternoon"] not in kept
    assert ids[SUNDAY] not in kept
    assert len(day_rows) == 2  # 同一天的单日快照一条不少 ⇒ 差异只来自窗口端点


def test_pending_list_is_unbounded_so_confirm_column_never_shrinks(svc):
    """「待确认」走 /api/handset/agenda/pending：**没有区间参数**。

    鸿蒙侧 WeekView 的注释依赖这一点（输入是全量、自己按天分桶）。后端哪天给它加窗口，
    手机上的待确认列就会跟着周界变短——这条把当前语义钉住。
    """
    _seed(svc)
    with svc._conn() as conn:
        far_pending = store.create_event(
            conn, title="next month", start_at="2026-11-20T09:00:00",
            kind="task", status="pending", source="manual",
        )
    pending = svc.list_pending()
    assert [e.id for e in pending] == [far_pending.id]
    assert all(e.status == "pending" for e in pending)
    assert datetime.fromisoformat(far_pending.start_at).year == 2026
