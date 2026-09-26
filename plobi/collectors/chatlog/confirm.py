"""Turning a rule hit into a concrete agenda candidate.

Two implementations behind one protocol:

- :class:`HeuristicConfirmer` — deterministic, on-device, zero token cost.
  Handles the common shapes ("明天下午三点开会") and refuses anything it cannot
  pin to a real time. It anchors relative dates on the message's **own sent
  time** (``message.sent_at``), never on the wall clock, and it never invents a
  clock — a date with no stated hour is handed to the model instead of being
  defaulted to a fake 9:00.
- an LLM confirmer (L2, cheap model) can be dropped in later for the messy
  remainder. Only the matched snippet (plus, per the narrow WP-EXTRACT-CONTEXT
  opening, the ±1 neighbour snippets) may be sent (ADR-0010); L1 never does
  this work (ADR-0011).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Protocol

from plobi.agenda.rules import RuleHit

from .client import ChatMessage
from .timeparse import ParsedWhen, parse_sent_at, parse_when


@dataclass
class ConfirmContext:
    """Extra, non-message context the pipeline can hand a confirmer.

    Kept separate from :class:`ChatMessage` so "the message" stays a faithful
    view of one chatlog record. Everything here is best-effort: a missing field
    simply means the confirmer has less to work with, never a hard failure.

    ``prev_snippet`` / ``next_snippet`` are the snippets of the message before
    and after this one within the same talker+day batch — at most one on each
    side. They are a deliberate, narrow carve-out from ADR-0010: neighbours may
    inform the *reading* of "明天/下午/约一下", but the model is told explicitly
    never to treat a neighbour as the user's own plan.
    """

    talker_name: Optional[str] = None
    prev_snippet: Optional[str] = None
    next_snippet: Optional[str] = None


@dataclass
class Candidate:
    title: str
    start_at: str
    kind: str
    is_change: bool = False
    end_at: Optional[str] = None
    # Provenance for the agenda heuristic. ``tier`` is the talker's declared
    # grade ("info" default, "task" human-prioritised); ``weight`` is the
    # numeric source weight the confirm module assigns (task > info). Both ride
    # through into the event evidence so the board / report can prioritise.
    tier: str = "info"
    weight: int = 1


class Confirmer(Protocol):
    def confirm(
        self,
        message: ChatMessage,
        hit: RuleHit,
        context: Optional[ConfirmContext] = None,
    ) -> Optional[Candidate]:
        ...


def _title_from(message: ChatMessage, hit: RuleHit) -> str:
    """Short human-readable label: the topical keywords plus a text fragment."""
    text = " ".join(message.content.split())
    if len(text) <= 40:
        return text

    keyword = hit.matched_keywords[0] if hit.matched_keywords else ""
    if keyword:
        index = text.find(keyword)
        if index != -1:
            start = max(0, index - 12)
            return text[start : start + 40].strip() + "…"
    return text[:40].strip() + "…"


class HeuristicConfirmer:
    """Deterministic confirmer — no network, no model, no token spend."""

    def __init__(self, *, now_factory=datetime.now, tier_of=None):
        self._now = now_factory
        # Resolves a talker's declared grade. Defaults to "info" when unset or
        # unknown — the collector never auto-grades, so a missing hook means
        # everything is treated as plain info-tier (unchanged behaviour).
        self._tier_of = tier_of or (lambda t: "info")

    def confirm(
        self,
        message: ChatMessage,
        hit: RuleHit,
        context: Optional[ConfirmContext] = None,
    ) -> Optional[Candidate]:
        # The anchor for "明天/后天/下午" is when this message was *sent*, not
        # when we swept it — a message sent at 23:00 saying "明天下午三点" must
        # land tomorrow, even if we process it the next morning. Only when the
        # sent time is unparseable do we fall back to the wall clock.
        base = parse_sent_at(message.sent_at, fallback=self._now())
        when = parse_when(message.content, now=base)

        tier = self._tier_of(message.talker)
        is_task = tier == "task"

        # A bare clock with no date is too ambiguous to schedule ("三点见"
        # could be today or tomorrow); info-tier messages let the model path
        # handle those. A task-tier talker is human-prioritised, so we relax
        # the refusal and pin it to the day the message was sent — this is the
        # "source weight" that lets task messages enter the confirm path ahead
        # of info ones.
        if when.day is None:
            if is_task and when.clock is not None:
                when = ParsedWhen(day=base.date(), clock=when.clock)
            else:
                return None

        # Never invent a clock. A date resolved but no hour stated ("明天开会")
        # is genuinely ambiguous — hand it to the model rather than filling a
        # fake 9:00 that would read as "有一个时间准确" when none is.
        if when.clock is None:
            return None

        start = datetime.combine(when.day, when.clock)

        return Candidate(
            title=_title_from(message, hit),
            start_at=start.replace(second=0, microsecond=0).isoformat(),
            kind=hit.category,
            is_change=hit.is_change,
            tier=tier,
            weight=2 if is_task else 1,
        )


class NullConfirmer:
    """Accepts nothing — used when a deployment wants collection disabled."""

    def confirm(
        self,
        message: ChatMessage,
        hit: RuleHit,
        context: Optional[ConfirmContext] = None,
    ) -> Optional[Candidate]:
        return None
