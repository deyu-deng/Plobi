"""B4 — gate ``delegate_task`` on a global concurrency cap and a quota breaker.

Core already caps each *call* (``delegation.max_concurrent_children``, enforced
by its ThreadPoolExecutor). This plugin adds what core has no notion of:

* a process-wide ceiling on concurrently running L3 children,
* a bounded wait so overflow queues instead of failing,
* a quota breaker (seam here, real sources land in B3),
* lifecycle tracking that feeds the L2 left rail.

It deliberately does NOT limit how many children an L2 may delegate in total —
only how many run at once and how much budget they may burn.

Everything lives in ``plobi/delegation/`` so this file stays a thin
registration shim (plugin dirs carry hyphens and cannot be imported as
packages anyway).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

DELEGATE_TOOL = "delegate_task"


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", on_pre_tool_call)
    ctx.register_hook("subagent_start", on_subagent_start)
    ctx.register_hook("subagent_stop", on_subagent_stop)
    ctx.register_hook("on_session_end", on_session_end)
    logger.info("plobi-delegation-guard: B4 guard registered")


# --------------------------------------------------------------------------
# gate
# --------------------------------------------------------------------------


def requested_children(args: Any) -> int:
    """How many children this one call wants (batch ``tasks`` or a single goal)."""
    if not isinstance(args, dict):
        return 1
    tasks = args.get("tasks")
    if isinstance(tasks, list) and tasks:
        return len(tasks)
    return 1


def on_pre_tool_call(
    tool_name: str = "",
    args: Any = None,
    session_id: str = "",
    turn_id: str = "",
    **kwargs,
) -> Optional[dict]:
    """Admit or defer a delegation. ``None`` means "let it through"."""
    if tool_name != DELEGATE_TOOL:
        return None

    try:
        from plobi.delegation import config, guard, quota
    except Exception:
        logger.debug("plobi-delegation-guard: guard package unavailable")
        return None

    try:
        cfg = config.load()
    except Exception:
        logger.debug("plobi-delegation-guard: config unavailable", exc_info=True)
        return None

    if not cfg.get("guard_enabled"):
        return None

    # Quota first: a hard breaker, and cheaper to evaluate than waiting.
    if cfg.get("quota_enabled"):
        try:
            verdict = quota.get_quota_circuit().check()
        except Exception:
            logger.debug("plobi-delegation-guard: quota check failed", exc_info=True)
            verdict = None
        if verdict is not None and not verdict.allowed and not cfg.get("quota_fail_open"):
            return {
                "action": "block",
                "message": (
                    f"[Plobi] 额度熔断已触发：{verdict.reason}。"
                    f"本次委派未执行；待额度恢复后重试，"
                    f"或调高 delegation.quota_daily_budget_usd。"
                ),
            }

    try:
        admission = guard.get_guard().acquire(
            requested=requested_children(args),
            session_id=session_id,
            turn_id=turn_id,
            label=_label_from_args(args),
            timeout=cfg.get("queue_timeout_seconds"),
        )
    except Exception:
        logger.debug("plobi-delegation-guard: acquire failed", exc_info=True)
        return None

    if admission.granted:
        return None

    return {
        "action": "block",
        "message": (
            f"[Plobi] L3 委派并发已达全局上限 {admission.cap} "
            f"（当前 {admission.active} 个在跑），"
            f"已等待 {admission.waited:.0f}s 仍无空位。"
            f"本次委派未执行，请稍后重试；"
            f"上限可用 delegation.global_max_children 调整。"
        ),
    }


def _label_from_args(args: Any) -> str:
    if not isinstance(args, dict):
        return ""
    goal = args.get("goal")
    if isinstance(goal, str):
        return goal[:40]
    tasks = args.get("tasks")
    if isinstance(tasks, list) and tasks:
        return f"batch x{len(tasks)}"
    return ""


# --------------------------------------------------------------------------
# lifecycle
# --------------------------------------------------------------------------


def on_subagent_start(**kwargs) -> None:
    try:
        from plobi.delegation import tracker
    except Exception:
        return
    try:
        tracker.get_tracker().on_start(
            child_session_id=kwargs.get("child_session_id") or "",
            child_subagent_id=kwargs.get("child_subagent_id") or "",
            parent_session_id=kwargs.get("parent_session_id") or "",
            child_role=kwargs.get("child_role") or "",
            child_goal=kwargs.get("child_goal") or "",
        )
    except Exception:
        logger.debug("plobi-delegation-guard: on_start failed", exc_info=True)


def on_subagent_stop(**kwargs) -> None:
    try:
        from plobi.delegation import guard, tracker
    except Exception:
        return
    try:
        tracker.get_tracker().on_stop(
            child_session_id=kwargs.get("child_session_id") or "",
            child_status=kwargs.get("child_status") or "",
            child_summary=kwargs.get("child_summary") or "",
            duration_ms=kwargs.get("duration_ms") or 0,
        )
        # Freeing the slot is what lets queued delegations through, so this
        # runs even though the tracker call above is best-effort.
        guard.get_guard().release_for(
            kwargs.get("parent_session_id") or "",
            kwargs.get("parent_turn_id") or "",
        )
    except Exception:
        logger.debug("plobi-delegation-guard: on_stop failed", exc_info=True)


def on_session_end(**kwargs) -> None:
    """Reclaim anything a session still holds — its children are gone with it."""
    session_id = kwargs.get("session_id") or ""
    if not session_id:
        return
    try:
        from plobi.delegation import guard, tracker
    except Exception:
        return
    try:
        freed = guard.get_guard().release_session(session_id)
        tracker.get_tracker().clear_session(session_id)
        if freed:
            logger.info(
                "plobi-delegation-guard: reclaimed %d slot(s) from session %s",
                freed,
                session_id,
            )
    except Exception:
        logger.debug("plobi-delegation-guard: session cleanup failed", exc_info=True)
