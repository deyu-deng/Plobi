"""Board API for timetable import (WP-COLLECTOR-ICS).

Mounted under the existing ``/api/collect`` prefix by
:mod:`plobi.collectors.chatlog.collect_api` — one collection surface, no new
prefix. Contract matches ``apps/desktop/src/app/agenda/timetable/api.ts``
(WP-ICS-BOARD):

* ``POST /api/collect/timetable/preview``  body {path}
      → {calendar_name, count, courses, first, last, sample[≤5]}  — no DB write
* ``POST /api/collect/timetable/import``   body {path, apply?}
      → preview fields + {created, updated, unchanged}; default confirmed
* ``GET  /api/collect/timetable``
      → {path, calendar_name, event_count, imported_at} —— 计数与时刻**由库里派生**，
        ``path`` / ``calendar_name`` 才来自状态文件；404 = 库里一条都没有（读不到库是 503）。

教务这一条**没有自动拉取实现**：本目录里没有任何 HTTP 客户端，240 条 ``kind=class`` 来自
2026-10-09 那一次 .ics 导入。``pull()`` 读的是用户自己放进来的本地文件，不是学校接口。
所以任何面上与文档都不许把这条说成"已经能自动拉取"（P6：不许描述不存在的机制）。

``path`` must be a local absolute path; ``..`` segments are refused so a
crafted body cannot climb out of its drive. The default never hardcodes the
user's file — the board passes it in.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from plobi.agenda import get_service
from plobi.agenda.service import AgendaService
from plobi.collectors import liveness
from plobi.collectors.ingest import ingest_occurrences
from plobi.collectors.liveness import read_source_liveness

from .parser import IcsParseError, parse_ics_with_stats

logger = logging.getLogger(__name__)

router = APIRouter()

#: 库里那一格 `events.source` 的值，也是活性探针的键——两头同一个名字，不另起。
SOURCE_ID = "timetable"

_SAMPLE_LIMIT = 5


class TimetablePathRequest(BaseModel):
    path: str


class TimetableImportRequest(TimetablePathRequest):
    apply: Literal["pending", "confirmed"] = "confirmed"


def _get_service() -> AgendaService:
    """Indirection seam so tests can pin a temp DB-backed service."""
    return get_service()


def _resolve_path(raw: str) -> Path:
    candidate = Path(raw.strip())
    if not raw.strip() or not candidate.is_absolute():
        raise HTTPException(status_code=400, detail="path must be an absolute local path")
    if ".." in candidate.parts:
        raise HTTPException(status_code=400, detail="path must not contain '..'")
    return candidate


def _parse_or_400(path: Path) -> tuple[list, dict]:
    try:
        return parse_ics_with_stats(path)
    except IcsParseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _preview_payload(occurrences: list, stats: dict) -> dict:
    starts = sorted(occurrence.start_at for occurrence in occurrences)
    sample = [
        {
            "title": occurrence.title,
            "start_at": occurrence.start_at,
            "end_at": occurrence.end_at,
            "location": occurrence.location,
        }
        for occurrence in occurrences[:_SAMPLE_LIMIT]
    ]
    return {
        "calendar_name": stats["calendar_name"],
        "count": len(occurrences),
        # Distinct courses = distinct SUMMARY values (the backend's best
        # answer to "how many courses"; sessions count N times each).
        "courses": len({occurrence.title for occurrence in occurrences}),
        "first": starts[0] if starts else "",
        "last": starts[-1] if starts else "",
        "sample": sample,
    }


def _status_path() -> Path:
    """``PLOBI_HOME/plobi/timetable.json`` — 只回答"这份课表是哪来的"。

    计数与时刻**不在这里**（见 :func:`_timetable_liveness`）：这份文件是一次性写入的
    旁证，被清过 / 换过机器 / 手工删掉都不代表库里没有东西。它曾经同时充当计数与
    时刻的真源，于是"文件不在"在面上长成"这课表从没导入过"，而库里躺着 240 条。
    """
    try:
        from plobi_constants import get_plobi_home

        root = get_plobi_home() / "plobi"
    except Exception:  # pragma: no cover - fallback mirrors store.agenda_db_path
        root = Path.home() / ".plobi" / "plobi"
    return root / "timetable.json"


def _resolved_db_path():
    """这个 API 真正写入的那个库；service 没指定时按 store 的同一把尺解析。"""
    return _as_path(_get_service().db_path) or (liveness._default_db() or None)


def _as_path(value) -> Optional[Path]:
    """``service.db_path`` 允许是 str 或 None；探针要的是 Path 或 None。"""
    return Path(value) if value else None


def _timetable_liveness() -> dict:
    """``timetable`` 这一条的库内读数：条数 + 最后一次真落库时刻。

    库的路径**问这个 API 自己写入的那个 service 要**（``service.db_path``），不在这里
    再解析一遍默认值：否则"导入写进 A、计数读 B"，面上就永远是"没导入"。
    复用 :func:`plobi.collectors.liveness.read_source_liveness` 而不是在这儿开第二条
    SQL——活性那把尺（只认 ``created_at``；读不到就说读不到）已经在那一处钉过。
    """
    report = read_source_liveness(_as_path(_get_service().db_path), expected_sources=(SOURCE_ID,))
    for row in report["sources"]:
        if row["source"] == SOURCE_ID:
            return row
    # 探针没给这一条（不该发生）：宁可说"读不到"，也不替面编一个 0。
    return {"source": SOURCE_ID, "state": liveness.STATE_NO_STORE, "events": 0,
            "last_created_at": "", "note": "活性探针没返回 timetable 这一行"}


def _read_status() -> Optional[dict]:
    try:
        raw = _status_path().read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        status = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("timetable status file is corrupted; reporting never-imported")
        return None
    return status if isinstance(status, dict) else None


def _write_status(status: dict) -> None:
    path = _status_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as exc:  # state file is best-effort; the import already landed
        logger.warning("timetable: could not write status file %s: %s", path, exc)


@router.post("/timetable/preview")
async def preview_timetable(request: TimetablePathRequest) -> dict:
    """Read-only parse of the given .ics — never touches the agenda DB."""
    path = _resolve_path(request.path)
    occurrences, stats = _parse_or_400(path)
    return _preview_payload(occurrences, stats)


@router.post("/timetable/import")
async def import_timetable(request: TimetableImportRequest) -> dict:
    """Parse + land through the shared ingest path, then record import state."""
    path = _resolve_path(request.path)
    occurrences, stats = _parse_or_400(path)
    payload: dict[str, Any] = _preview_payload(occurrences, stats)
    result = ingest_occurrences(
        _get_service(), occurrences, apply=request.apply
    )
    payload["created"] = result["created"]
    payload["updated"] = result["updated"]
    payload["unchanged"] = result["unchanged"]
    _write_status(
        {
            "path": str(path),
            "calendar_name": stats["calendar_name"],
            "event_count": len(occurrences),
            "imported_at": _now_iso(),
        }
    )
    return payload


@router.get("/timetable")
async def timetable_status() -> dict:
    """最后一次真落库的读数从库里派生；状态文件只回答"这份课表是哪来的"。

    改之前的形状：计数与时刻都读 ``$PLOBI_HOME/plobi/timetable.json``，那份文件不在就
    直接 404。而它是一次性旁证（清过 / 换过机器 / 手工删过都会不见），当时库里正躺着
    240 条 ``kind=class`` ⇒ 面上把"有货"读成"从没导入过"。现在 404 只有一种含义：
    库里 `timetable` 一条都没有。**读不到库另开一档 503**——修"没货"的假阴性不能
    换来"读不到＝没导入"这个新的假阴性。
    """
    live = _timetable_liveness()
    if live["state"] == liveness.STATE_NO_STORE:
        # 两种"读不到"不是一回事：库文件本来还没建 ＝ 一条都没落过账（404）；
        # 库在、却读不出来（被独占 / 不是个 sqlite / 表没了）＝ 故障，
        # 不许冒充"没导入"（503）。
        path = _resolved_db_path()
        if path is None or not path.is_file():
            raise HTTPException(status_code=404, detail="no timetable imported yet")
        raise HTTPException(
            status_code=503, detail="日程库在，但读不出来：说不出课表进没进过账（这不等于「没导入」）"
        )
    count = int(live["events"] or 0)
    if count == 0:
        raise HTTPException(status_code=404, detail="no timetable imported yet")
    status = _read_status() or {}
    return {
        "path": str(status.get("path") or ""),
        "calendar_name": str(status.get("calendar_name") or ""),
        "event_count": count,
        "imported_at": str(live["last_created_at"] or ""),
    }


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now().replace(microsecond=0).isoformat()
