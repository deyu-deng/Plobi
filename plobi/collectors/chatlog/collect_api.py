"""Board-facing collection API: the first-run review and the pending queue.

Mounted at ``/api/collect`` (see ``plobi_cli/web_server.py``). Consumed by
``apps/desktop/src/app/agenda/talker/api.ts`` — the desktop board's "session
collection" entry (A7 slice).

Contract (matches the desktop store):

* ``GET /api/collect/talkers``
      -> {reviewComplete: bool, talkers: [{id, name, status}]}
      status is one of known/excluded/pending; an unreviewed talker is
      reported as ``pending`` (board: "new session awaiting your decision")
      without being written to the status table.
* ``POST /api/collect/talkers/{talker}/mode``  body {mode: "collect"|"exclude"}
      -> {id, status}
      One-click collect/exclude from the board.
* ``POST /api/collect/talkers/bulk``  body {talkers: [...], status}
      Batch decision for the first-run review ("排除这批").
* ``POST /api/collect/review-complete``
      -> {reviewComplete: true}
      Flips the first-run gate; until this is called blacklist mode sweeps
      nothing (ADR-0010 revision). Completing the review also (a) bulk-excludes
      official accounts (``gh_*``) that are still pending — talkers the user
      already marked known/excluded are never rewritten (verdict 23) — and
      (b) kicks a background 3-day lookback sweep over every known talker so
      the board fills immediately instead of waiting for the next watchdog
      tick. The response returns at once; a failed sweep only logs and never
      rolls the review flag back.
* ``POST /api/collect/sweep``  optional query ``lookback_days`` (default 3)
      -> IngestReport dict
      On-demand lookback sweep over every known talker. Refuses with 409
      until the first-run review has completed (ADR-0010 fail-closed).
"""

from __future__ import annotations

import logging
import threading
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from .pipeline import DEFAULT_LOOKBACK_DAYS, ChatlogPipeline
from . import webhook as _webhook_module

logger = logging.getLogger(__name__)

router = APIRouter()


def get_pipeline() -> ChatlogPipeline:
    """Delegate to the webhook module's canonical singleton.

    There must be exactly one ``ChatlogPipeline`` per process — both the
    webhook ingest path and the board API operate on the same pipeline /
    talker store / seen store.  The webhook module owns the instance;
    this module only reads/writes through it.
    """
    return _webhook_module.get_pipeline()


def set_pipeline(pipeline: Optional[ChatlogPipeline]) -> None:
    """Injection seam for tests — proxies to the canonical holder."""
    _webhook_module.set_pipeline(pipeline)


def _status_for(talker: str) -> str:
    status = get_pipeline().talkers.status_of(talker)
    return status if status is not None else "pending"


class ModeDecision(BaseModel):
    mode: Literal["collect", "exclude"]


class BulkDecision(BaseModel):
    talkers: list[str]
    status: Literal["known", "excluded"]


@router.get("/talkers")
async def list_talkers():
    pipeline = get_pipeline()
    store = pipeline.talkers
    # Enumerate what chatlog actually has so the review covers every active
    # session; unknown entries surface as pending for a one-click decision.
    # `list_talker_sessions` also carries the chatlog display name (topicName)
    # so the board shows the real group/contact name, not the raw @chatroom id.
    try:
        sessions = await run_in_threadpool(pipeline.client.list_talker_sessions)
    except Exception as exc:  # pragma: no cover - network seam
        logger.warning("collect: session enumeration failed: %s", exc)
        sessions = []

    talkers = [
        {"id": session.id, "name": session.name or session.id, "status": _status_for(session.id)}
        for session in sessions
    ]
    return {
        "reviewComplete": store.review_done(),
        "talkers": talkers,
    }


@router.post("/talkers/{talker}/mode")
async def decide_talker(talker: str, decision: ModeDecision):
    store = get_pipeline().talkers
    status = "known" if decision.mode == "collect" else "excluded"
    store.set_status(talker, status)
    return {"id": talker, "status": status}


@router.post("/talkers/bulk")
async def decide_talkers_bulk(decision: BulkDecision):
    store = get_pipeline().talkers
    for talker in decision.talkers:
        store.set_status(talker, decision.status)
    return {"updated": len(decision.talkers), "status": decision.status}


def kick_lookback_sweep(pipeline: ChatlogPipeline, lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> dict:
    """Synchronous lookback sweep over every known talker.

    The injectable core of both the review-complete background kick and
    ``POST /api/collect/sweep``: tests call this directly instead of waiting
    on the daemon thread. Chatlog being down is already tolerated inside
    ``run_once`` (logged, per-talker); a report is always returned.
    """
    return pipeline.run_once(lookback_days=max(1, int(lookback_days))).as_dict()


def _kick_in_background(pipeline: ChatlogPipeline) -> None:
    """Run the lookback sweep off the request path; failures only log.

    The review flag is deliberately never rolled back on failure: completing
    the review is a user decision, the sweep is best-effort and the
    10-minute watchdog will catch up regardless.
    """
    try:
        kick_lookback_sweep(pipeline)
    except Exception:
        logger.exception("review-complete: background lookback sweep failed")


@router.post("/review-complete")
async def complete_review():
    pipeline = get_pipeline()
    store = pipeline.talkers

    # Official accounts (gh_*) still awaiting a decision are excluded in bulk:
    # completing the review must not turn channels into collection targets.
    # Enumeration may fail (chatlog down) — degrade to the pending rows
    # already on disk, never block, never raise.
    try:
        sessions = await run_in_threadpool(pipeline.client.list_talker_sessions)
    except Exception as exc:
        logger.warning("collect: session enumeration failed during review-complete: %s", exc)
        sessions = []

    candidates = store.pending() | {session.id for session in sessions}
    for talker in sorted(candidates):
        # Only still-pending (or never-recorded) gh_ ids are touched; a
        # talker the user already marked known/excluded is never rewritten
        # (verdict 23), and neither are non-official pending rows.
        if talker.startswith("gh_") and store.status_of(talker) in (None, "pending"):
            store.mark_excluded(talker)

    store.set_review_done(True)

    # First collection happens now, off the request path: a 3-day lookback
    # over every known talker. The response returns immediately.
    threading.Thread(
        target=_kick_in_background,
        args=(pipeline,),
        name="collect-review-lookback",
        daemon=True,
    ).start()

    return {"reviewComplete": True}


@router.post("/sweep")
async def sweep(lookback_days: int = DEFAULT_LOOKBACK_DAYS):
    """On-demand lookback sweep over every known talker (default 3 days).

    Refuses with 409 until the first-run review has completed (ADR-0010:
    nothing is collected from unreviewed conversations).
    """
    pipeline = get_pipeline()
    if not pipeline.talkers.review_done():
        raise HTTPException(
            status_code=409,
            detail="first-run review not completed — POST /api/collect/review-complete first",
        )
    return await run_in_threadpool(kick_lookback_sweep, pipeline, lookback_days)


# WP-COLLECTOR-ICS: the timetable (.ics) collector owns its routes in its own
# package but is served under the same /api/collect prefix — one collection
# surface for the board, no new prefix. Kept at module bottom so the chatlog
# router is fully defined first and the timetable import (icalendar) stays
# out of the chatlog module's own import cost.
from plobi.collectors.timetable.api import router as _timetable_router  # noqa: E402

router.include_router(_timetable_router)
