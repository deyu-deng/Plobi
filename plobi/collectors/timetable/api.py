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
      → {path, calendar_name, event_count, imported_at} (last import state;
        404 = never imported, the board treats that as null)

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
from plobi.collectors.ingest import ingest_occurrences

from .parser import IcsParseError, parse_ics_with_stats

logger = logging.getLogger(__name__)

router = APIRouter()

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
    """``PLOBI_HOME/plobi/timetable.json`` — the last-import state file."""
    try:
        from plobi_constants import get_plobi_home

        root = get_plobi_home() / "plobi"
    except Exception:  # pragma: no cover - fallback mirrors store.agenda_db_path
        root = Path.home() / ".plobi" / "plobi"
    return root / "timetable.json"


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
    """Last import state; 404 when nothing was ever imported (board → null)."""
    status = _read_status()
    if status is None:
        raise HTTPException(status_code=404, detail="no timetable imported yet")
    return {
        "path": status.get("path", ""),
        "calendar_name": status.get("calendar_name", ""),
        "event_count": status.get("event_count", 0),
        "imported_at": status.get("imported_at", ""),
    }


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now().replace(microsecond=0).isoformat()
