"""Push pending changes to the phone and resolve the replies.

Implements the short-sequence protocol from
docs/adr/0009-agenda-change-pending-confirmation.md; the card format is
hard-frozen by Docs/specs/console-ui.md §7:

    【待确认 #12】高数课改期
    原值: 明日 08:00  →  新值: 明日 10:00
    来源: 课程群「2026高数」09:12 消息（摘录≤50字）
    回复: 确认12 / 忽略12

Replies are matched here rather than by an agent: parsing two words does not
justify a model call, and L1 runs a flagship model (ADR-0011 cost discipline).
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from plobi.notify import Notifier, get_notifier

from .service import (
    AgendaService,
    ConfirmSeqExpired,
    EventNotFound,
    get_service,
)
from .store import DailyPlan, Event

logger = logging.getLogger(__name__)

_ACCEPT_WORDS = ("确认", "确定", "同意", "confirm", "ok", "yes")
_REJECT_WORDS = ("忽略", "取消", "不用", "拒绝", "dismiss", "no")

_REPLY = re.compile(
    r"^\s*(?P<verb>[\u4e00-\u9fff]+|[a-zA-Z]+)\s*(?P<seq>\d{1,4})\s*$",
)


@dataclass
class ReplyCommand:
    seq: int
    accept: bool


def parse_reply(text: str) -> Optional[ReplyCommand]:
    """Match ``确认 3`` / ``忽略3`` / ``ok 3``. Anything else returns ``None``."""
    match = _REPLY.match(text or "")
    if not match:
        return None

    verb = match.group("verb").strip().lower()
    seq = int(match.group("seq"))

    if verb in _ACCEPT_WORDS:
        return ReplyCommand(seq=seq, accept=True)
    if verb in _REJECT_WORDS:
        return ReplyCommand(seq=seq, accept=False)
    return None


def _clock(value: Optional[str]) -> str:
    """Human clock: 今日/明日/后日 + HH:MM, falling back to MM-DD HH:MM."""
    if not value:
        return "—"
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return value.replace("T", " ")[:16]
    today = datetime.now().date()
    labels = {0: "今日", 1: "明日", 2: "后日"}
    label = labels.get((moment.date() - today).days)
    if label is None:
        return moment.strftime("%m-%d %H:%M")
    return f"{label} {moment.strftime('%H:%M')}"


def _source_line(event: Event) -> Optional[str]:
    """§7 来源行：群名 + 时刻 + 摘录（≤50 字）。无证据字段则省略。"""
    evidence = event.evidence or {}
    snippet = (evidence.get("snippet") or "").strip()
    if not snippet and not evidence.get("talker"):
        return None
    sent_at = str(evidence.get("sent_at") or "")
    moment = sent_at[11:16] if len(sent_at) >= 16 else sent_at
    talker = str(evidence.get("talker") or "微信")
    return f"来源: 「{talker}」{moment} 消息（{snippet[:50]}）"


def format_pending(event: Event) -> str:
    """One notification body. Short: it is read on a phone lock screen."""
    seq = event.confirm_seq if event.confirm_seq is not None else "?"
    lines = [f"【待确认 #{seq}】{event.title}"]

    previous = (event.prev_value or {}).get("start_at")
    if previous and previous != event.start_at:
        lines.append(f"原值: {_clock(previous)}  →  新值: {_clock(event.start_at)}")
    else:
        lines.append(f"时间: {_clock(event.start_at)}")

    source = _source_line(event)
    if source:
        lines.append(source)

    lines.append(f"回复: 确认{seq} / 忽略{seq}")
    return "\n".join(lines)


def format_pending_plan(plan: DailyPlan) -> str:
    """Same §7 card skeleton as events: header + one summary + 确认N/忽略N."""
    seq = plan.confirm_seq if plan.confirm_seq is not None else "?"
    lines = [f"【待确认 #{seq}】明日计划"]
    summary = (plan.summary or "").strip()
    if summary:
        lines.append(summary)
    lines.append(f"回复: 确认{seq} / 忽略{seq}")
    return "\n".join(lines)


class PendingDispatcher:
    def __init__(
        self,
        *,
        service: Optional[AgendaService] = None,
        notifier: Optional[Notifier] = None,
    ):
        self._service = service
        self._notifier = notifier

    @property
    def service(self) -> AgendaService:
        return self._service or get_service()

    @property
    def notifier(self) -> Notifier:
        return self._notifier or get_notifier()

    def notify_pending(self, events: Optional[list[Event]] = None) -> list[str]:
        """Push one message per pending event.

        Pending plans go out only on a full sweep (``events is None``).
        Watchdog/webhook pass a specific event list — those already have a
        20:00 evening-plan card and must not be re-pushed every 10 minutes.
        """
        targets = events if events is not None else self.service.list_pending()
        plans = self.service.list_pending_plans() if events is None else []
        if not targets and not plans:
            return []

        if not self.notifier.configured:
            logger.info(
                "agenda: %d pending change(s) + %d pending plan(s) but no notifier configured",
                len(targets),
                len(plans),
            )
            return []

        sent: list[str] = []
        for event in targets:
            outcome = self.notifier.send(format_pending(event))
            if outcome.ok:
                sent.append(event.id)
            else:
                # Leave it pending; the next sweep retries rather than losing it.
                logger.warning("agenda: notify failed for %s: %s", event.id, outcome.detail)
        for plan in plans:
            outcome = self.notifier.send(format_pending_plan(plan))
            if outcome.ok:
                sent.append(plan.id)
            else:
                logger.warning("agenda: notify failed for %s: %s", plan.id, outcome.detail)
        return sent

    def handle_reply(self, text: str) -> Optional[str]:
        """Resolve a phone reply. Returns an ack, or ``None`` if not for us."""
        command = parse_reply(text)
        if command is None:
            return None

        try:
            resolved = self.service.resolve_by_seq(command.seq, accept=command.accept)
        except ConfirmSeqExpired:
            return f"[Plobi] 序号 {command.seq} 已过期（超过 24 小时），请在看板上处理。"
        except EventNotFound:
            return f"[Plobi] 没有待确认的 {command.seq} 号，可能已经处理过了。"

        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")

        if isinstance(resolved, DailyPlan):
            if command.accept:
                return f"[Plobi] 已确认：明日计划（{stamp}）"
            return f"[Plobi] 已忽略 {command.seq} 号，今夜计划已否决（{stamp}）。"

        if command.accept:
            title = resolved.title if resolved else str(command.seq)
            return f"[Plobi] 已确认：{title}（{stamp}）"

        if resolved is None:
            return f"[Plobi] 已忽略 {command.seq} 号，该条目未加入日程（{stamp}）。"
        return (
            f"[Plobi] 已忽略 {command.seq} 号，已恢复为 {_clock(resolved.start_at)}（{stamp}）。"
        )


_DEFAULT: Optional[PendingDispatcher] = None
_DEFAULT_LOCK = threading.Lock()


def get_dispatcher() -> PendingDispatcher:
    global _DEFAULT
    if _DEFAULT is None:
        with _DEFAULT_LOCK:
            if _DEFAULT is None:
                _DEFAULT = PendingDispatcher()
    return _DEFAULT


def set_dispatcher(dispatcher: Optional[PendingDispatcher]) -> None:
    global _DEFAULT
    _DEFAULT = dispatcher
