"""Webhook endpoint chatlog pushes new messages to.

Mounted at ``/api/chatlog``. The 10-minute sweep exists because this delivery
is best-effort; both paths share the dedupe ledger, so a message arriving
twice is ingested once.

2026-11-17（`WP-COLLECT-UNIFY`）：这一族**不再自己处理推送来的那批消息**——
两个入口都改成走公共触发面（:func:`trigger_round` → `sweep.run_source`），
因为逐条处理是**另一条落池路**，而落点必须只有一处。推来的条数照实报出去。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from .pipeline import ChatlogPipeline

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


def _notify(pipeline: ChatlogPipeline, pending_ids: list[str]) -> list[str]:
    """Push whatever this batch left awaiting the human.

    Notification lives here rather than in the pipeline: collecting and
    telling the user are separate concerns, and a delivery failure must not
    roll back a successful ingest (the entry stays pending and the next sweep
    retries the push).
    """
    if not pending_ids:
        return []

    from plobi.agenda.dispatch import get_dispatcher

    events = []
    for event_id in pending_ids:
        try:
            events.append(pipeline.service.get(event_id))
        except Exception:
            logger.exception("chatlog: could not load %s for notification", event_id)

    try:
        return get_dispatcher().notify_pending(events)
    except Exception:
        logger.exception("chatlog: notification dispatch failed")
        return []


def trigger_round(
    *,
    pipeline: Optional[ChatlogPipeline] = None,
    lookback_days: Optional[int] = None,
    records: Optional[list[Any]] = None,
) -> dict:
    """微信那一轮：**只走公共触发面**（`plobi.collectors.sweep.run_source`）。

    为什么这一族不再自己调 pipeline（2026-11-17 用户拍「全部收进公共触发面」）：
    逐条处理（webhook 推来的那批）是**另一条落池路**，而落点必须只有一处
    （`WP-COLLECT-UNIFY`：全仓把 `Occurrence` 写进池的代码只有 `base.land_occurrences`）。
    现在这一族的入口只有这一个。

    `pipeline` 是注入缝：**一个进程只有一份 `ChatlogPipeline`**（会话名单／准入状态／
    seen 账都在那一份上），所以这里默认取那个单例，测试与看门狗可以塞自己那份进来。

    `lookback_days`（回看几天）与 `records`（侧车推来的那批消息）是**这一族自己认**的
    参数，由触发方递进来——公共层不按信源名字分支，所以只能走 `params` 这条透传。
    """
    from plobi.collectors.sweep import run_source

    params: dict[str, Any] = {}
    if lookback_days:
        params["lookback_days"] = int(lookback_days)
    if records:
        params["records"] = list(records)
    return run_source(
        "wechat",
        params=params or None,
        collectors={"wechat": pipeline or get_pipeline()},
    )


def outcome_shape(outcome: dict) -> dict:
    """把公共触发面那份读数**还原成这一族从前那份形状**（端子上那一屏读的就是它）。

    ``outcome["report"]`` 是腿自己那份原始读数；``report["report"]`` 是它的完整读数
    （`IngestReport.as_dict()` 的形状：`created`／`updated` 是**逐条**的列表）。

    **失败那一路也回同一个形状**（空读数）——回一个 `{}` 会让端子的形状在失败时变样，
    读那一屏的人（桌面）就得为两种形状各写一套。
    """
    raw = outcome.get("report") or {}
    full = dict(raw.get("report") or {})
    if not full:
        from .pipeline import IngestReport

        full = IngestReport().as_dict()
    full["state"] = raw.get("state", outcome.get("state", ""))
    full["detail"] = outcome.get("detail", "")
    return full


def _ingest(payload: dict) -> dict:
    """侧车推来那批消息：**递进那一轮**（触发面只有一处），不再自己处理。

    为什么改（2026-11-17 用户拍「全部收进公共触发面」）：逐条处理是**另一条落池路**，
    而落点必须只有一处。那一批消息现在作为**参数**递进去，由那一族自己逐条过——
    落点、seen 账、准入过滤全走同一条路，**推送的消息一条不丢**。

    用户知情接受的代价是另一件事：webhook 从「推一条落一条」变成「推完触发一轮」
    （那一轮里同样会扫到这些消息，只是多走了一趟扫）。
    """
    records = _records(payload)
    outcome = trigger_round(records=records)
    result = outcome_shape(outcome)
    raw = outcome.get("report") or {}
    result["pushed"] = len(records)
    result["notified"] = _notify(get_pipeline(), raw.get("pending_ids") or [])
    return result


def _sweep() -> dict:
    outcome = trigger_round()
    result = outcome_shape(outcome)
    raw = outcome.get("report") or {}
    result["notified"] = _notify(get_pipeline(), raw.get("pending_ids") or [])
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
