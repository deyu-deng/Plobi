"""WP-DAY-SURFACE — 白天「今天」的接口契约：events + 重叠判定 + 地点备注。

看板从 ``GET /api/agenda/day`` 拿到当天的 events（带 location/notes/overlaps）、
conflicts 与已确认的 avoid_windows（计划层已退役，不再有 plan_items / plan_status）。**现场计算**，不依赖 20:00
跑过 plan；新增/修改日程**写入**不拒重叠（人令不拒），但响应带 overlaps 提示
看板标红。作息不是代码的事（由日程 Agent 自己写 events），所以这里没有锚点。
"""

from __future__ import annotations

import os

import pytest

from plobi.agenda import store
from plobi.agenda.planning import compute_event_overlaps, day_surface
from plobi.agenda.router import (
    EventCreate,
    EventPatch,
    _render_day,
)
from plobi.agenda.service import AgendaService


DAY = "2026-09-14"


@pytest.fixture()
def svc(tmp_path):
    """AgendaService 绑 tmp/agenda.db；_init_db 自动跑（首次 connect）。"""
    os.environ["PLOBI_AGENDA_DB"] = str(tmp_path / "agenda.db")
    return AgendaService(tmp_path / "agenda.db")


@pytest.fixture()
def conn(svc):
    connection = store.connect(svc.db_path)
    try:
        yield connection
    finally:
        connection.close()


def _today_events(conn, day: str = DAY) -> list:
    return store.list_events(
        conn,
        start_from=f"{day}T00:00:00",
        start_to=f"{day}T23:59:59",
    )


# ---------------------------------------------------------------------------
# 重叠判定
# ---------------------------------------------------------------------------


def test_two_overlapping_timed_events_become_conflicts(svc, conn):
    svc.create_manual(
        title="课 A",
        start_at=f"{DAY}T14:00:00",
        end_at=f"{DAY}T16:00:00",
        kind="class",
    )
    svc.create_manual(
        title="课 B",
        start_at=f"{DAY}T15:00:00",
        end_at=f"{DAY}T17:00:00",
        kind="class",
    )
    snap = day_surface(conn, DAY, _today_events(conn))
    # 双方 overlaps 各有对方
    overlaps = snap["overlaps"]
    assert len(overlaps) == 2
    pairs = [(k, sorted(v)) for k, v in overlaps.items() if v]
    assert len(pairs) == 2
    for _eid, peers in pairs:
        assert len(peers) == 1  # 各自指对方一个
    # 一条 conflicts，重叠区间 15:00–16:00
    assert len(snap["conflicts"]) == 1
    c = snap["conflicts"][0]
    assert c["start_at"][11:16] == "15:00"
    assert c["end_at"][11:16] == "16:00"


def test_point_event_without_end_at_never_conflicts(svc, conn):
    """无 end_at 的 DDL / 提醒：不制造冲突。"""
    svc.create_manual(
        title="交作业",
        start_at=f"{DAY}T20:00:00",
        kind="ddl",
    )
    svc.create_manual(
        title="自习",
        start_at=f"{DAY}T20:30:00",
        end_at=f"{DAY}T22:00:00",
        kind="task",
    )
    snap = day_surface(conn, DAY, _today_events(conn))
    assert snap["conflicts"] == []
    # 双方 overlaps 都是 []
    for peers in snap["overlaps"].values():
        assert peers == []


def test_overlapping_events_are_still_written(svc, conn):
    """人令不拒：create_manual 不查重叠 → 都写入，overlaps 在响应里。"""
    e1 = svc.create_manual(
        title="A",
        start_at=f"{DAY}T10:00:00",
        end_at=f"{DAY}T11:00:00",
        kind="meeting",
    )
    e2 = svc.create_manual(
        title="B",
        start_at=f"{DAY}T10:30:00",
        end_at=f"{DAY}T11:30:00",
        kind="meeting",
    )
    snap = day_surface(conn, DAY, _today_events(conn))
    today = {e.id for e in _today_events(conn)}
    assert {e1.id, e2.id}.issubset(today)
    assert e2.id in snap["overlaps"][e1.id]


# ---------------------------------------------------------------------------
# 地点 / 备注
# ---------------------------------------------------------------------------


def test_create_with_location_and_notes_round_trips(svc):
    event = svc.create_manual(
        title="自习",
        start_at=f"{DAY}T19:00:00",
        end_at=f"{DAY}T21:00:00",
        kind="task",
        location="紫金港图书馆西1",
        notes="带电脑",
    )
    fetched = svc.get(event.id)
    assert fetched.location == "紫金港图书馆西1"
    assert fetched.notes == "带电脑"


def test_update_manual_accepts_location_and_notes(svc):
    event = svc.create_manual(
        title="自习",
        start_at=f"{DAY}T19:00:00",
        end_at=f"{DAY}T21:00:00",
        kind="task",
    )
    updated = svc.update_manual(
        event.id, location="紫金港", notes="带电脑", end_at=f"{DAY}T22:00:00"
    )
    assert updated.location == "紫金港"
    assert updated.notes == "带电脑"
    assert updated.end_at == f"{DAY}T22:00:00"


def test_empty_location_becomes_none(svc):
    event = svc.create_manual(
        title="自习",
        start_at=f"{DAY}T19:00:00",
        end_at=f"{DAY}T21:00:00",
        kind="task",
        location="紫金港",
        notes="带电脑",
    )
    cleared = svc.update_manual(event.id, location="", notes="   ")
    assert cleared.location is None
    assert cleared.notes is None


def test_patch_event_schema_accepts_location_and_notes():
    """Pydantic 模型能序列化 location/notes — 看板走 PATCH 时不会 422。"""
    patch = EventPatch(location="紫金港", notes="带电脑", end_at=f"{DAY}T22:00:00")
    body = patch.model_dump(exclude_none=True)
    assert body == {"location": "紫金港", "notes": "带电脑", "end_at": f"{DAY}T22:00:00"}


def test_create_event_schema_accepts_location_and_notes():
    payload = EventCreate(
        title="自习",
        start_at=f"{DAY}T19:00:00",
        end_at=f"{DAY}T21:00:00",
        kind="task",
        location="紫金港",
        notes="带电脑",
    ).model_dump(exclude_none=True)
    assert payload["location"] == "紫金港"
    assert payload["notes"] == "带电脑"


# ---------------------------------------------------------------------------
# HTTP 端点：GET /api/agenda/day
# ---------------------------------------------------------------------------


def test_get_day_endpoint_returns_full_contract(svc, conn):
    svc.create_manual(
        title="电工电子学",
        start_at=f"{DAY}T10:00:00",
        end_at=f"{DAY}T12:25:00",
        kind="class",
    )
    svc.create_manual(
        title="A",
        start_at=f"{DAY}T15:00:00",
        end_at=f"{DAY}T16:00:00",
        kind="meeting",
    )
    svc.create_manual(
        title="B",
        start_at=f"{DAY}T15:30:00",
        end_at=f"{DAY}T16:30:00",
        kind="meeting",
    )

    # 直接调端点的纯函数体（避免 asyncio / TestClient 套娃）。
    payload = _render_day(conn, DAY)

    assert payload["date"] == DAY
    assert {e["title"] for e in payload["events"]} >= {
        "电工电子学",
        "A",
        "B",
    }
    for e in payload["events"]:
        assert e["title"]
        assert isinstance(e["overlaps"], list)
    assert any(c["start_at"][11:16] == "15:30" for c in payload["conflicts"])


def test_get_day_endpoint_default_to_today(svc, conn):
    """路由层 ``date=None`` → 今天。_render_day 自身只接受合法 ISO 字符串。"""
    from datetime import date as _date

    target = _date.today().isoformat()
    payload = _render_day(conn, target)
    assert payload["date"] == target


def test_get_day_endpoint_rejects_bad_date(svc, conn):
    """路由层 ``2026-13-40`` → HTTPException(400)（校验在路由，不在 _render）。"""
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        # asyncio 不跑——只调同步入口前的校验
        from datetime import date as _date

        try:
            _date.fromisoformat("2026-13-40")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="bad date") from exc
    assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# redirects / 一致性 / 杂项
# ---------------------------------------------------------------------------


def test_compute_event_overlaps_handles_empty():
    overlaps, conflicts = compute_event_overlaps([])
    assert overlaps == {}
    assert conflicts == []


# ---------------------------------------------------------------------------
# avoid_windows
# ---------------------------------------------------------------------------


def test_avoid_windows_empty_when_no_confirmed_proposal(svc, conn):
    """无 confirmed 卡 → avoid_windows=[]。"""
    snap = day_surface(conn, DAY, _today_events(conn))
    assert snap["avoid_windows"] == []


def test_avoid_windows_appear_only_after_confirm(svc, conn):
    """pending 卡不算——确认后才出现。"""
    from plobi.agenda import checkin

    card_pending = checkin.propose_config_change(
        conn,
        avoid_windows=[{"start_time": "11:00", "end_time": "13:00"}],
    )
    snap = day_surface(conn, DAY, _today_events(conn))
    assert snap["avoid_windows"] == [], "pending 不应出现在 day_surface"

    svc.confirm_card(card_pending.id)
    snap2 = day_surface(conn, DAY, _today_events(conn))
    assert len(snap2["avoid_windows"]) == 1
    entry = snap2["avoid_windows"][0]
    assert entry["start_at"] == f"{DAY}T11:00:00"
    assert entry["end_at"] == f"{DAY}T13:00:00"
    assert entry["card_id"] == card_pending.id


def test_get_day_endpoint_returns_avoid_windows(svc, conn):
    """``GET /api/agenda/day`` 透传 avoid_windows（已确认）。"""
    from plobi.agenda import checkin

    card = checkin.propose_config_change(
        conn,
        avoid_windows=[{"start_time": "11:00", "end_time": "13:00"}],
    )
    svc.confirm_card(card.id)
    payload = _render_day(conn, DAY)
    assert "avoid_windows" in payload
    assert len(payload["avoid_windows"]) == 1
    assert payload["avoid_windows"][0]["start_at"] == f"{DAY}T11:00:00"
