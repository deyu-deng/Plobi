"""Webhook endpoint chatlog pushes new messages to.

Mounted at ``/api/chatlog``. The 10-minute sweep exists because this delivery
is best-effort; both paths share the dedupe ledger, so a message arriving
twice is ingested once.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from .client import normalize_message
from .pipeline import ChatlogPipeline, IngestReport

logger = logging.getLogger(__name__)

router = APIRouter()

_pipeline: Optional[ChatlogPipeline] = None


def get_pipeline() -> ChatlogPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = ChatlogPipeline()
    return _pipeline


def set_pipeline(pipeline: Optional[ChatlogPipeline]) -> None:
    """Injection seam for tests and for reloading configuration."""
    global _pipeline
    _pipeline = pipeline


class WebhookPayload(BaseModel):
    # chatlog versions differ; accept a single record or a batch under any of
    # the usual keys and normalize downstream.
    model_config = {"extra": "allow"}

    data: Optional[Any] = None
    messages: Optional[Any] = None


def _records(payload: dict) -> list[Any]:
    for key in ("data", "messages", "items", "list"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            return [value]
    # A bare message object posted at the top level.
    return [payload] if payload else []


def _notify(pipeline: ChatlogPipeline, report: IngestReport) -> list[str]:
    """Push whatever this batch left awaiting the human.

    Notification lives here rather than in the pipeline: collecting and
    telling the user are separate concerns, and a delivery failure must not
    roll back a successful ingest (the entry stays pending and the next sweep
    retries the push).
    """
    if not report.pending_ids:
        return []

    from plobi.agenda.dispatch import get_dispatcher

    events = []
    for event_id in report.pending_ids:
        try:
            events.append(pipeline.service.get(event_id))
        except Exception:
            logger.exception("chatlog: could not load %s for notification", event_id)

    try:
        return get_dispatcher().notify_pending(events)
    except Exception:
        logger.exception("chatlog: notification dispatch failed")
        return []


def _ingest(payload: dict) -> dict:
    pipeline = get_pipeline()
    report = IngestReport()

    for raw in _records(payload):
        message = normalize_message(raw)
        if message is None or message.is_empty:
            continue
        try:
            pipeline.handle_message(message, report)
        except Exception:
            # One bad message must not sink the batch; the sweep will retry it
            # only if it was never marked seen.
            logger.exception("chatlog webhook: failed to ingest a message")

    result = report.as_dict()
    result["notified"] = _notify(pipeline, report)
    return result


def _sweep() -> dict:
    pipeline = get_pipeline()
    report = pipeline.run_once()
    result = report.as_dict()
    result["notified"] = _notify(pipeline, report)
    return result


@router.post("/webhook")
async def receive(payload: WebhookPayload):
    return await run_in_threadpool(_ingest, payload.model_dump())


@router.post("/sweep")
async def sweep():
    """Manual trigger for the incremental sweep (also used by cron)."""
    return await run_in_threadpool(_sweep)


@router.get("/status")
async def status():
    pipeline = get_pipeline()
    healthy = await run_in_threadpool(pipeline.client.healthy)
    return {
        "enabled": pipeline.config.enabled,
        "mode": pipeline.config.mode,
        "base_url": pipeline.config.base_url,
        "talkers": len(pipeline.config.talkers),
        "chatlog_reachable": healthy,
        "poll_minutes": pipeline.config.poll_minutes,
        # ADR-0010 blacklist-mode visibility for ops and the board.
        "review_done": pipeline.talkers.review_done(),
        "known": len(pipeline.talkers.known()),
        "excluded": len(pipeline.talkers.excluded()),
        "pending": len(pipeline.talkers.pending()),
    }
