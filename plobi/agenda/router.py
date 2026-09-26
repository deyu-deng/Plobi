"""HTTP adapter for the agenda service.

Thin by design: validate, call the service, map business errors to status
codes. Mounted at ``/api/agenda`` by ``hermes_cli.web_server`` so the desktop
board does not depend on an opt-in plugin being enabled (ADR-0008).

WP-DAY-SURFACE 新增 ``GET /api/agenda/day?date=YYYY-MM-DD`` —— 白天看板用的
「今天」快照：events（含 location/notes/overlaps），**现场**计算出的 anchors
（含弹性窗落位结果），可用的 plan_items（当日 daily_plan 存在才有），以及
conflicts。**不**写库、**不**要求先跑过 20:00；与晚间 generate_evening_plan
共用同一份落位算法。
"""

from __future__ import annotations

from datetime import date as _date, datetime as _datetime
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from .planning import day_surface
from . import store as _store
from .service import (
    AgendaService,
    ConfirmSeqExpired,
    EventNotFound,
    get_service,
)
from .store import AgendaValidationError

router = APIRouter()


class EventCreate(BaseModel):
    # WP-DAY-SURFACE: 手动日程可带结束时间、地点、备注；空字符串视为未填。
    title: str = Field(min_length=1)
    start_at: str
    end_at: Optional[str] = None
    kind: str = "task"
    location: Optional[str] = None
    notes: Optional[str] = None


class EventPatch(BaseModel):
    title: Optional[str] = None
    start_at: Optional[str] = None
    end_at: Optional[str] = None
    kind: Optional[str] = None
    location: Optional[str] = None
    notes: Optional[str] = None


def _service() -> AgendaService:
    return get_service()


def _serialize(event: Any) -> dict:
    """Row → JSON; location/notes 摊平 evidence.location（WP-DAY-SURFACE）。"""
    data = event.to_dict()
    if not data.get("location"):
        evidence = data.get("evidence") or {}
        loc = evidence.get("location")
        if isinstance(loc, str) and loc.strip():
            data["location"] = loc.strip()
    return data


async def _call(func, *args, **kwargs):
    """Run blocking SQLite work off the event loop, mapping domain errors."""
    try:
        return await run_in_threadpool(func, *args, **kwargs)
    except EventNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConfirmSeqExpired as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    except AgendaValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("")
async def list_agenda(
    from_: Optional[str] = Query(default=None, alias="from"),
    to: Optional[str] = Query(default=None),
):
    events = await _call(_service().list_agenda, from_, to)
    return [_serialize(e) for e in events]


@router.get("/pending")
async def list_pending():
    events = await _call(_service().list_pending)
    return [_serialize(e) for e in events]


def _render_day(conn, target: str) -> dict:
    """``GET /api/agenda/day`` 的纯渲染函数。

    date 验证在路由层做；这里只吃合法 ``YYYY-MM-DD``。连接由调用方管。
    """
    from .planning import day_surface

    window_from = _datetime.combine(_date.fromisoformat(target), _datetime.min.time())
    window_to = (
        _datetime.combine(_date.fromisoformat(target), _datetime.max.time())
        .replace(microsecond=0)
    )
    events = _store.list_events(
        conn,
        start_from=window_from,
        start_to=window_to,
    )
    snap = day_surface(conn, target, events)
    rows = []
    for event in events:
        data = _serialize(event)
        data["overlaps"] = snap["overlaps"].get(event.id, [])
        rows.append(data)
    return {
        "date": snap["date"],
        "events": rows,
        "anchors": snap["kept_anchors"],
        "plan_items": snap["plan_items"],
        "conflicts": snap["conflicts"],
        "plan_status": snap["plan_status"],
        "avoid_windows": snap.get("avoid_windows", []),
    }


@router.get("/day")
async def get_day(date: Optional[str] = Query(default=None)):
    """WP-DAY-SURFACE: 白天看板用的「今天」快照。

    ``date`` 缺省 = 今天（本地日期）。锚点**现场**计算（不要求 20:00 跑过
    plan），即使没有任何 daily_plan 也会返回 anchors；plan_items 仅在当日
    daily_plan 已生成时才非空。
    """
    target = (date or _date.today().isoformat()).strip()[:10]
    try:
        _date.fromisoformat(target)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"date must be YYYY-MM-DD: {date!r}") from exc

    def _go() -> dict:
        conn = _store.connect()
        try:
            return _render_day(conn, target)
        finally:
            conn.close()

    return await _call(_go)


@router.post("")
async def create_event(body: EventCreate):
    event = await _call(
        _service().create_manual,
        title=body.title,
        start_at=body.start_at,
        end_at=body.end_at,
        kind=body.kind,
        location=body.location,
        notes=body.notes,
    )
    return _serialize(event)


@router.patch("/{event_id}")
async def patch_event(event_id: str, body: EventPatch):
    event = await _call(
        _service().update_manual,
        event_id,
        **body.model_dump(exclude_none=True),
    )
    return _serialize(event)


@router.delete("/{event_id}")
async def delete_event(event_id: str):
    await _call(_service().delete, event_id)
    return {"ok": True, "id": event_id}


@router.post("/{event_id}/confirm")
async def confirm_event(event_id: str):
    event = await _call(_service().confirm, event_id)
    return _serialize(event)


@router.post("/{event_id}/dismiss")
async def dismiss_event(event_id: str):
    event = await _call(_service().dismiss, event_id)
    if event is None:
        return {"ok": True, "id": event_id, "deleted": True}
    return _serialize(event)
