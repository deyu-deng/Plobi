"""Intercept agenda confirmations before they reach the agent.

A reply like ``确认 3`` needs two words parsed and one row updated. Routing it
through the agent would spend flagship-model tokens on clerical work and add
seconds of latency, so this plugin resolves it inline and answers on the same
channel, then tells the gateway to stop.

Everything else falls through untouched — this hook must never swallow a real
conversation.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Protocol-shaped but unparseable replies (「确认」「忽略12。」) get a usage hint
# instead of falling through to the model. Deliberately limited to the two
# protocol verbs — anything else must reach the agent untouched.
_USAGE_HINT = "[Plobi] 回复格式：确认 N / 忽略 N（N 为推送编号）"
_LOOSE_REPLY = re.compile(r"^\s*(确认|忽略)\s*\d{0,4}[。.，,，]?\s*$")


def register(ctx) -> None:
    ctx.register_hook("pre_gateway_dispatch", _on_pre_gateway_dispatch)
    logger.info("plobi-agenda: confirm-reply interceptor registered")


def _answer(gateway: Any, event: Any, text: str) -> bool:
    """Best-effort synchronous ack through the inbound adapter."""
    try:
        import asyncio

        source = getattr(event, "source", None)
        platform = getattr(source, "platform", None)
        adapter = (getattr(gateway, "adapters", None) or {}).get(platform)
        if adapter is None:
            return False

        coro = adapter.send(
            chat_id=getattr(source, "chat_id", ""),
            content=text,
            metadata=getattr(event, "metadata", None) or {},
        )
        loop = asyncio.get_running_loop()
        loop.create_task(coro)
        return True
    except Exception:
        logger.debug("plobi-agenda: same-channel reply unavailable", exc_info=True)
        return False


def _reply_on_same_channel(gateway: Any, event: Any, text: str) -> bool:
    return _answer(gateway, event, text)


def _on_pre_gateway_dispatch(event: Any = None, gateway: Any = None, **kwargs) -> Optional[dict]:
    text = (getattr(event, "text", None) or "").strip()
    if not text:
        return None

    try:
        from plobi.agenda.dispatch import get_dispatcher, parse_reply
    except Exception:
        logger.debug("plobi-agenda: agenda package unavailable", exc_info=True)
        return None

    # Cheap guard first: only pay for the resolver when the shape matches.
    command = parse_reply(text)
    if command is None:
        if _LOOSE_REPLY.match(text):
            # Protocol-shaped but invalid (bare verb, bad number) → usage hint.
            if not _reply_on_same_channel(gateway, event, _USAGE_HINT):
                _fallback_notify(_USAGE_HINT)
            return {"action": "skip", "reason": "agenda-usage-hint"}
        return None

    try:
        ack = get_dispatcher().handle_reply(text)
    except Exception:
        logger.exception("plobi-agenda: failed to resolve a confirmation reply")
        return None

    if ack is None:
        return None

    if not _reply_on_same_channel(gateway, event, ack):
        # No adapter to answer through — fall back to the configured notifier
        # so the user still learns the outcome.
        _fallback_notify(ack)

    return {"action": "skip", "reason": "agenda-confirmation"}


def _fallback_notify(text: str) -> None:
    try:
        from plobi.notify import get_notifier

        get_notifier().send(text)
    except Exception:
        logger.debug("plobi-agenda: fallback notify failed", exc_info=True)
