"""任务身份与事件流的设备面读口（骨架第 2、3 根 · Docs/specs/task-events.md）。

Nothing in here owns a task or writes an event. ``plobi_cli.kanban_db`` already
is the task kernel — ``tasks`` (identity), ``task_runs`` (each attempt), and the
append-only ``task_events`` table the dispatcher writes as it works, which
``plugins/plobi-north-star`` (L1's four narrow tools) already sit on too. This
module only *projects* those kernel events onto the five stable classes the
handset is promised, and serves them read-only.

Why a projection instead of the raw ``kind`` values: the kernel has 33 kinds and
grows; the phone must not break when it does. ``detail`` always carries the
original, so nothing is lost by grouping.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

router = APIRouter()

EVENT_CLASSES = ("status", "progress", "artifact", "approval", "error")

# kernel kind → outward class. ``approval`` has no native kernel kind yet (the
# spec §4 records that honestly); the mapping table is the only implementation
# of the split, and it lives next to the routes on purpose (ARCH-UI-MASTER §3.5
# "no parallel service layer" — same rule the console router follows).
_KIND_CLASS: dict[str, str] = {
    "created": "status",
    "assigned": "status",
    "linked": "status",
    "unlinked": "status",
    "edited": "status",
    "promoted": "status",
    "promoted_manual": "status",
    "specified": "status",
    "decomposed": "status",
    "scheduled": "status",
    "archived": "status",
    "unblocked": "status",
    "reclaim_deferred": "status",
    "claimed": "progress",
    "reclaimed": "progress",
    "claim_extended": "progress",
    "claim_rejected": "progress",
    "spawned": "progress",
    "respawn_guarded": "progress",
    "heartbeat": "progress",
    "stale": "progress",
    "dependency_wait": "progress",
    "block_loop_detected": "progress",
    "attached": "artifact",
    "attachment_removed": "artifact",
    "commented": "artifact",
    "completed": "artifact",
    "tip_scratch_workspace": "artifact",
    "blocked": "error",
    "timed_out": "error",
    "gave_up": "error",
    "completion_blocked_hallucination": "error",
    "suspected_hallucinated_references": "error",
}

# An unmapped (newer) kernel kind must still show up — as a state change, never
# dropped, never an error the phone cannot render.
_DEFAULT_CLASS = "status"

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def classify(kind: str) -> str:
    """One kernel kind → one of the five outward classes."""
    return _KIND_CLASS.get((kind or "").strip(), _DEFAULT_CLASS)


def _event_row(event: Any) -> dict:
    return {
        "id": event.id,
        "taskId": event.task_id,
        "runId": event.run_id,
        "kind": classify(event.kind),
        "detail": event.kind,
        "at": event.created_at,
        "payload": event.payload,
    }


def _db_path() -> Path:
    """The active board's DB. Tests patch this seam instead of adding an env var."""
    from plobi_cli import kanban_db

    return kanban_db.kanban_db_path()


def _open() -> Optional[sqlite3.Connection]:
    """A connection to the active board, or ``None`` when no board exists yet.

    A machine that never ran the dispatcher has no kanban DB; that is an honest
    empty list, not a schema to create on a read request and not a 500.
    """
    from plobi_cli import kanban_db

    path = _db_path()
    if path is None or not path.exists():
        return None
    return kanban_db.connect(db_path=path)


def _clamped_limit(limit: int) -> int:
    return max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))


# --------------------------------------------------------------------------- #
# read assembly (blocking sqlite, always called through run_in_threadpool)
# --------------------------------------------------------------------------- #


def _tasks_data(status: Optional[str], limit: int) -> list[dict]:
    from plobi_cli import kanban_db

    conn = _open()
    if conn is None:
        return []
    try:
        rows = kanban_db.list_tasks(conn, status=status, limit=limit)
        if not rows:
            return []
        # One grouped query instead of one MAX(id) per task row.
        placeholders = ",".join("?" * len(rows))
        last = {
            str(r[0]): int(r[1])
            for r in conn.execute(
                f"SELECT task_id, MAX(id) FROM task_events "
                f"WHERE task_id IN ({placeholders}) GROUP BY task_id",
                [task.id for task in rows],
            ).fetchall()
        }
        return [
            {
                "id": task.id,
                "title": task.title,
                "assignee": task.assignee,
                "status": task.status,
                "lastEventId": last.get(task.id, 0),
            }
            for task in rows
        ]
    finally:
        conn.close()


def _task_exists(conn: sqlite3.Connection, task_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM tasks WHERE id = ?", (task_id,)
    ).fetchone() is not None


def _task_events_data(
    task_id: str, after: int, limit: int
) -> Optional[list[dict]]:
    """``None`` = no such task (404); ``[]`` = task exists, nothing after the cursor."""
    from plobi_cli import kanban_db

    conn = _open()
    if conn is None:
        return None
    try:
        if not _task_exists(conn, task_id):
            return None
        return [
            _event_row(ev)
            for ev in kanban_db.list_events(conn, task_id)
            if ev.id > after
        ][:limit]
    finally:
        conn.close()


def _stream_data(after: int, limit: int) -> list[dict]:
    conn = _open()
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT id, task_id, run_id, kind, payload, created_at "
            "FROM task_events WHERE id > ? ORDER BY id ASC LIMIT ?",
            (max(0, int(after or 0)), limit),
        ).fetchall()
    finally:
        conn.close()
    from plobi_cli import kanban_db

    events = [
        kanban_db.Event(
            id=int(r["id"]),
            task_id=str(r["task_id"]),
            kind=str(r["kind"]),
            payload=_decode_payload(r["payload"]),
            created_at=int(r["created_at"]),
            run_id=(int(r["run_id"]) if r["run_id"] is not None else None),
        )
        for r in rows
    ]
    return [_event_row(ev) for ev in events]


def _decode_payload(raw: Any) -> Optional[Any]:
    if not raw:
        return None
    import json

    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        # A payload the kernel wrote that is not JSON stays a string rather
        # than taking the whole stream down.
        return str(raw)


# --------------------------------------------------------------------------- #
# routes — bare objects, matching the agenda surface (the {ok,data} envelope is
# the console's internal API rule and is not carried across surfaces)
# --------------------------------------------------------------------------- #


@router.get("/tasks")
async def list_tasks(
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
):
    from plobi_cli import kanban_db

    if status and status not in kanban_db.VALID_STATUSES:
        return JSONResponse(
            status_code=400,
            content={
                "detail": f"status must be one of {sorted(kanban_db.VALID_STATUSES)}"
            },
        )
    return await run_in_threadpool(_tasks_data, status, limit)


@router.get("/tasks/{task_id}/events")
async def task_events(
    task_id: str,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
):
    rows = await run_in_threadpool(_task_events_data, task_id, after, limit)
    if rows is None:
        return JSONResponse(
            status_code=404, content={"detail": f"task {task_id!r} not found"}
        )
    return rows


@router.get("/events")
async def event_stream(
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
):
    return await run_in_threadpool(_stream_data, after, limit)
