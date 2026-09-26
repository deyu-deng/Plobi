"""Model-backed confirmer — the fallback for what the heuristic cannot pin.

The heuristic confirmer (``confirm.py``) refuses any message it cannot pin to a
real calendar day, because a bare clock ("三点见") is genuinely ambiguous. Those
land as ``unresolved`` today. This module spends a cheap L2 call on them.

Two contracts it must not break:

**Privacy (ADR-0010)** — only :func:`plobi.agenda.rules.snippet` output ever
reaches the model. Never the raw message, never a history of the conversation.
The single narrow opening (WP-EXTRACT-CONTEXT) is the ±1 neighbour snippet,
which is needed to read "明天/下午/约一下" correctly and is capped by
``rules.snippet`` just like the message itself.

**Cost (ADR-0011)** — this is L2 work. The route is resolved through
:mod:`plobi.routing` and the secretary's model is rejected outright; wiring the
flagship here would put it back in the per-message loop ADR-0011 exists to end.

This is the first real consumer of ``plobi/routing``. If it ever stops calling
:meth:`ModelConfirmer.route`, the routing table becomes decoration again.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from datetime import date, datetime, time
from typing import Callable, Optional, Protocol

from plobi.agenda.rules import RuleHit, snippet
from plobi.routing import L2_AGENDA, ModelRoute, ModelRouter, RoutingError, get_router

from .client import ChatMessage
from .confirm import Candidate, ConfirmContext, HeuristicConfirmer, _title_from
from .timeparse import parse_sent_at

logger = logging.getLogger(__name__)

# Environment variable holding the *full* chat-completions URL. We deliberately
# do not guess a path: providers disagree on ``/v1`` prefixes and guessing wrong
# produces a confusing 404 deep inside the collector.
CHAT_URL_ENV = "PLOBI_L2_CHAT_URL"
API_KEY_ENV = "PLOBI_L2_API_KEY"

_DEFAULT_TIMEOUT = 20.0

_JSON_BLOCK = re.compile(r"\{[^{}]*\}", re.DOTALL)

_SYSTEM_PROMPT = (
    "你是日程抽取器。给定一条消息及其上下文，判断它是否是「用户本人」要执行或参加的安排。\n"
    "只输出一行 JSON，不要任何解释、不要 markdown 代码块：\n"
    '{"is_users_plan":true,"date":"YYYY-MM-DD","time":"HH:MM","end_time":"HH:MM或null","title":"不超过20字"}\n'
    "规则：\n"
    "- 先判断 is_users_plan：消息里是用户本人要去做/参加的事 → true；"
    "别人的课表、群里起哄、转发别人的安排、纯闲聊 → false。\n"
    "- is_users_plan 为 false 时，date/time/end_time/title 全部填 null，不要硬凑。\n"
    "- 用「这条消息的发送时间」作为锚点解析「今天/明天/后天/下午」等相对时间，"
    "不要用其他时间（例如当前时间）推算。\n"
    "- date 无法确定具体日期就填 null。\n"
    "- time 无法从消息确定就填 null，禁止用默认时间（如 09:00）代替。\n"
    "- end_time 无法确定就填 null，不要编造时长。\n"
)


class Completer(Protocol):
    """Sends a prompt to one model and returns its reply text."""

    def __call__(self, prompt: str, route: ModelRoute) -> Optional[str]:
        ...


def openai_compatible_completer(
    prompt: str,
    route: ModelRoute,
    *,
    chat_url: Optional[str] = None,
    api_key: Optional[str] = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> Optional[str]:
    """POST to any OpenAI-compatible ``/chat/completions`` endpoint.

    Raises when unconfigured, so callers can distinguish "no endpoint" from
    "model said nothing" — the first is an operator error worth surfacing.
    """
    url = (chat_url or os.environ.get(CHAT_URL_ENV, "")).strip()
    key = (api_key or os.environ.get(API_KEY_ENV, "")).strip()
    if not url:
        raise RoutingError(f"model confirmer has no endpoint; set {CHAT_URL_ENV}")
    if not key:
        raise RoutingError(f"model confirmer has no credential; set {API_KEY_ENV}")

    body = json.dumps(
        {
            "model": route.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        },
        ensure_ascii=False,
    ).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))

    choices = payload.get("choices") or []
    if not choices:
        return None
    message = choices[0].get("message") or {}
    content = message.get("content")
    return content if isinstance(content, str) else None


def _is_self_text(is_self: Optional[bool]) -> str:
    if is_self is True:
        return "是本人发出"
    if is_self is False:
        return "不是本人发出（是别人发的）"
    return "未知（chatlog 未提供，无法判断是否本人发出）"


def _build_prompt(
    message: ChatMessage,
    hit: RuleHit,
    now: datetime,
    context: Optional[ConfirmContext] = None,
) -> str:
    """Assemble the prompt for one message.

    Only snippets ever leave the machine (ADR-0010): the message body is the
    ``snippet`` of ``message.content`` and the neighbours are the snippets the
    pipeline clipped. The prompt always carries *who* sent it, *when* it was
    sent (the anchor for all relative times), the conversation's display name,
    and whether it was sent by the account owner — that is the whole point of
    the extraction (WP-EXTRACT-CONTEXT).
    """
    # The message's own sent time is the anchor for "明天/下午"; the wall clock
    # is only a last resort when chatlog gave us nothing parseable.
    sent_raw = (message.sent_at or "").strip()
    sent_ref = parse_sent_at(sent_raw, fallback=now)
    weekday = "一二三四五六日"[sent_ref.weekday()]

    sender = (message.sender or "").strip()
    talker_name = (context.talker_name if context else None) or ""
    talker_label = talker_name.strip() or (message.talker.strip() or "未知")

    lines: list[str] = []
    if sent_raw:
        lines.append(
            f"这条消息发送于 {sent_raw}（{sent_ref:%Y-%m-%d %H:%M}，周{weekday}）。"
            "所有「今天/明天/后天/上午/下午」等相对时间都以这条消息的发送时间为准，"
            "不要用当前时间推算。"
        )
    else:
        lines.append(
            f"这条消息没有可用的发送时间；参考当前时间 {now:%Y-%m-%d %H:%M}（周{weekday}）。"
        )
    lines.append(f"发送人：{sender if sender else '未知'}")
    lines.append(f"会话：{talker_label}")
    lines.append(f"是否为本人发出：{_is_self_text(message.is_self)}")
    lines.append(
        f"消息已判定为类别「{hit.category}」"
        f"{'，且这是对既有安排的变更。' if hit.is_change else '。'}"
    )

    prev_snippet = (context.prev_snippet if context else None) or ""
    next_snippet = (context.next_snippet if context else None) or ""
    if prev_snippet or next_snippet:
        lines.append(
            "同一会话里这条消息前后的邻居（仅供理解语气/指代，"
            "绝对不要把邻居本身当成用户的安排）："
        )
        if prev_snippet:
            lines.append(f"上文：{prev_snippet}")
        if next_snippet:
            lines.append(f"下文：{next_snippet}")

    lines.append("当前这条消息的片段（只依据它判断）：")
    lines.append('"""')
    # Clip here too: the message body is the only part that must be a snippet.
    lines.append(snippet(message.content))
    lines.append('"""')
    return "\n".join(lines)


def _extract_json(text: str) -> Optional[dict]:
    """Pull the first JSON object out of a reply that may carry prose."""
    match = _JSON_BLOCK.search(text or "")
    if match is None:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


_REFUSAL_STRINGS = {"false", "0", "no", "n", "否", "不是"}


def _explicitly_not_the_users_plan(parsed: dict) -> bool:
    """True when the model said this is not the user's own plan."""
    flag = parsed.get("is_users_plan")
    if flag is None:
        flag = parsed.get("is_user_plan")
    if flag is False:
        return True
    if isinstance(flag, str) and flag.strip().lower() in _REFUSAL_STRINGS:
        return True
    return False


def _to_candidate(
    reply: str,
    message: ChatMessage,
    hit: RuleHit,
    now: datetime,
    context: Optional[ConfirmContext] = None,
) -> Optional[Candidate]:
    parsed = _extract_json(reply)
    if parsed is None:
        logger.debug("model confirmer returned no JSON; leaving unresolved")
        return None

    # The model judged this is someone else's plan / not something the user will
    # do → not an agenda entry, regardless of any date it echoed.
    if _explicitly_not_the_users_plan(parsed):
        logger.debug("model confirmer: not the user's own plan; not ingesting")
        return None

    raw_day = parsed.get("date")
    if not isinstance(raw_day, str) or not raw_day.strip():
        return None
    try:
        day = date.fromisoformat(raw_day.strip())
    except ValueError:
        logger.debug("model confirmer returned an unparseable date: %r", raw_day)
        return None

    clock: Optional[time] = None
    raw_clock = parsed.get("time")
    if isinstance(raw_clock, str) and raw_clock.strip():
        try:
            clock = time.fromisoformat(raw_clock.strip())
        except ValueError:
            clock = None

    if clock is None:
        # No fake default. A date without a stated clock is genuinely
        # unresolved — better honestly "unresolved" than a made-up 09:00 that
        # the user would have to fix. (WP-EXTRACT-CONTEXT removed the old
        # _DEFAULT_CLOCK fill.)
        logger.debug("model confirmer gave a date but no clock; leaving unresolved")
        return None

    end_at: Optional[str] = None
    raw_end = parsed.get("end_time")
    if isinstance(raw_end, str) and raw_end.strip():
        try:
            end_clock = time.fromisoformat(raw_end.strip())
            end_at = (
                datetime.combine(day, end_clock)
                .replace(second=0, microsecond=0)
                .isoformat()
            )
        except ValueError:
            end_at = None

    title = parsed.get("title")
    if not isinstance(title, str) or not title.strip():
        title = _title_from(message, hit)

    return Candidate(
        title=" ".join(title.split())[:40],
        start_at=datetime.combine(day, clock).replace(second=0, microsecond=0).isoformat(),
        kind=hit.category,
        is_change=hit.is_change,
        end_at=end_at,
    )


class ModelConfirmer:
    """Resolve the messages the heuristic refused, using a cheap L2 model.

    Every failure path returns ``None`` so the pipeline keeps counting the
    message as ``unresolved`` instead of inventing a schedule entry.
    """

    def __init__(
        self,
        *,
        completer: Optional[Completer] = None,
        router: Optional[ModelRouter] = None,
        role: str = L2_AGENDA,
        now_factory: Callable[[], datetime] = datetime.now,
    ):
        self._completer = completer
        self._router = router
        self._role = role
        self._now = now_factory

    def route(self) -> ModelRoute:
        """The model route this confirmer will use.

        Raises :class:`RoutingError` when the role is L1 or points at a GUI-only
        surface — both would violate ADR-0011 and both are operator mistakes,
        so they fail loudly rather than silently degrading.
        """
        router = self._router or get_router()
        if router.is_l1(self._role):
            raise RoutingError(
                f"{self._role} is the secretary; ADR-0011 forbids routing "
                "per-message confirmation through L1"
            )
        route = router.resolve(self._role)
        if route.is_gui_surface:
            raise RoutingError(
                f"{self._role} points at a GUI-only surface ({route.qualified}); "
                "those belong to L3 workers"
            )
        return route

    def confirm(
        self,
        message: ChatMessage,
        hit: RuleHit,
        context: Optional[ConfirmContext] = None,
    ) -> Optional[Candidate]:
        try:
            route = self.route()
        except RoutingError as exc:
            logger.warning("model confirmer disabled: %s", exc)
            return None

        if self._completer is None:
            logger.debug("model confirmer has no completer injected; leaving unresolved")
            return None

        now = self._now()
        # The only text allowed to leave the machine is the message's own
        # snippet plus the ±1 neighbour snippets (ADR-0010 + WP-EXTRACT-CONTEXT).
        prompt = _build_prompt(message, hit, now, context)

        try:
            reply = self._completer(prompt, route)
        except (urllib.error.URLError, OSError, json.JSONDecodeError, KeyError, RoutingError) as exc:
            logger.warning("model confirmer call failed (%s); leaving unresolved", exc)
            return None

        if not reply:
            return None
        return _to_candidate(reply, message, hit, now, context)


class FallbackConfirmer:
    """Deterministic first, model only for the remainder.

    This ordering *is* the cost and privacy discipline: a message the heuristic
    can pin down never leaves the machine, so model spend and exposure both stay
    proportional to the genuinely ambiguous remainder rather than to message
    volume.
    """

    def __init__(
        self,
        *,
        primary: Optional[object] = None,
        fallback: Optional[ModelConfirmer] = None,
        now_factory: Callable[[], datetime] = datetime.now,
    ):
        self._primary = primary or HeuristicConfirmer(now_factory=now_factory)
        self._fallback = fallback

    def confirm(
        self,
        message: ChatMessage,
        hit: RuleHit,
        context: Optional[ConfirmContext] = None,
    ) -> Optional[Candidate]:
        candidate = self._primary.confirm(message, hit, context)
        if candidate is not None or self._fallback is None:
            return candidate
        return self._fallback.confirm(message, hit, context)
