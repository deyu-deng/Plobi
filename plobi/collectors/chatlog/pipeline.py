"""Collect → filter → confirm → ingest.

Order matters and is the privacy contract:

1. scope       — `CollectorConfig.mode` decides which talkers are in scope:
                  blacklist enumerates every conversation and drops blacklisted
                  ones (empty blacklist = all); whitelist reads only named
                  talkers (fail-closed).
2. dedupe      — webhook and sweep both deliver; only one wins
3. local rules — on-device; decides what may leave the machine at all
4. confirm     — heuristic first, model only for the remainder (snippet only)
5. ingest      — lands as ``pending`` for the human to confirm (ADR-0009)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from plobi.agenda import AgendaService, get_service
from plobi.agenda.rules import match as rule_match
from plobi.agenda.rules import snippet
from plobi.collectors.base import Occurrence
from plobi.collectors.ingest import ingest_occurrences

from .client import ChatlogClient, ChatlogUnavailable, ChatMessage
from .config import CollectorConfig
from .confirm import Candidate, Confirmer, ConfirmContext, HeuristicConfirmer
from .state import SeenStore, TalkerStore

logger = logging.getLogger(__name__)

# Two events count as "the same thing rescheduled" when they fall on the same
# day and share this much of their keyword set.
_MATCH_KEYWORDS = ("组会", "例会", "会议", "开会", "答辩", "面试", "课", "培训", "宣讲", "聚餐")

# The review-complete kick and POST /api/collect/sweep sweep this many days
# (today included); the routine watchdog sweep stays at 1.
DEFAULT_LOOKBACK_DAYS = 3


@dataclass
class IngestReport:
    scanned: int = 0
    skipped_duplicate: int = 0
    skipped_not_whitelisted: int = 0
    # ADR-0010 (blacklist mode): a reviewed-but-excluded talker, or a brand-new
    # talker we refused to touch (fail-closed, recorded as pending).
    skipped_excluded: int = 0
    skipped_new_talker: int = 0
    # First-run review not done yet: candidates enumerated but nothing swept.
    review_gated: int = 0
    filtered_out: int = 0
    unresolved: int = 0
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)

    @property
    def pending_ids(self) -> list[str]:
        return [*self.created, *self.updated]

    def as_dict(self) -> dict:
        return {
            "scanned": self.scanned,
            "skipped_duplicate": self.skipped_duplicate,
            "skipped_not_whitelisted": self.skipped_not_whitelisted,
            "skipped_excluded": self.skipped_excluded,
            "skipped_new_talker": self.skipped_new_talker,
            "review_gated": self.review_gated,
            "filtered_out": self.filtered_out,
            "unresolved": self.unresolved,
            "created": list(self.created),
            "updated": list(self.updated),
        }


def _topic_tokens(text: str) -> set[str]:
    return {word for word in _MATCH_KEYWORDS if word in text}


class ChatlogPipeline:
    # Collector protocol (WP-COLLECTOR-ICS): the chatlog source is the first
    # implementation of the pluggable collector surface.
    source_id = "wechat"

    def __init__(
        self,
        *,
        config: Optional[CollectorConfig] = None,
        client: Optional[ChatlogClient] = None,
        service: Optional[AgendaService] = None,
        seen: Optional[SeenStore] = None,
        talkers: Optional[TalkerStore] = None,
        confirmer: Optional[Confirmer] = None,
    ):
        self.config = config or CollectorConfig.load()
        self.client = client or ChatlogClient(self.config.base_url)
        self.service = service or get_service()
        self.seen = seen or SeenStore()
        self.talkers = talkers or TalkerStore()
        self.confirmer = confirmer or self._default_confirmer()
        # talker id → chatlog display name, resolved once, best-effort.
        self._talker_names: Optional[dict[str, str]] = None

    def health(self) -> dict:
        """Collector-protocol health — reuses the existing chatlog probe."""
        ok = chatlog_is_healthy(self.client)
        return {
            "ok": ok,
            "detail": (
                "chatlog /health ok" if ok else "chatlog unreachable (/health failed)"
            ),
        }

    def _default_confirmer(self) -> Confirmer:
        """Heuristic first, L2 model for the remainder.

        The ordering is the cost + privacy discipline (a message the heuristic
        can pin never leaves the machine). Construction is deliberately lazy and
        fault-tolerant: if the quota pool / routing / model wiring is unavailable
        we degrade to the heuristic alone rather than refusing to start. When the
        model path is reached but has no usable source it returns ``None``, so
        the message simply stays ``unresolved`` — never a made-up 9:00.
        L1 is never involved (``ModelConfirmer.route`` enforces it).
        """
        heuristic = HeuristicConfirmer(tier_of=self.config.tier_of)
        try:
            from plobi.quota.route import QuotaAwareCompleter
            from plobi.routing import L2_AGENDA

            from .model_confirm import FallbackConfirmer, ModelConfirmer

            model = ModelConfirmer(completer=QuotaAwareCompleter(), role=L2_AGENDA)
        except Exception as exc:  # pragma: no cover - import/registration seam
            logger.warning(
                "chatlog: model fallback unavailable (%s); using heuristic only", exc
            )
            return heuristic
        return FallbackConfirmer(primary=heuristic, fallback=model)

    def _talker_name_map(self) -> dict[str, str]:
        """Best-effort id → display-name map from chatlog's session list.

        Cached for the pipeline's lifetime. Any failure (chatlog down, client
        without the method) yields an empty map — the board falls back to the id
        and nothing about collection changes.
        """
        if self._talker_names is not None:
            return self._talker_names

        mapping: dict[str, str] = {}
        lister = getattr(self.client, "list_talker_sessions", None)
        if callable(lister):
            try:
                for session in lister():
                    session_id = getattr(session, "id", None)
                    session_name = getattr(session, "name", None)
                    if session_id and session_name:
                        mapping[str(session_id)] = str(session_name)
            except Exception as exc:  # best-effort; never block collection
                logger.debug("chatlog: talker display-name lookup failed: %s", exc)
        self._talker_names = mapping
        return mapping

    # --- single message -----------------------------------------------------

    def handle_message(
        self,
        message: ChatMessage,
        report: IngestReport,
        context: Optional[ConfirmContext] = None,
    ) -> None:
        report.scanned += 1

        if not self.config.allows(message.talker):
            report.skipped_not_whitelisted += 1
            return

        # ADR-0010 (blacklist mode): the talker status table gates admission.
        # Everything that is not `known` is refused — a new talker is recorded
        # as pending (board-visible) but never ingested.
        if self.config.mode == "blacklist":
            status = self.talkers.status_of(message.talker)
            if status == "excluded":
                report.skipped_excluded += 1
                return
            if status != "known":
                self.talkers.mark_pending(message.talker)
                report.skipped_new_talker += 1
                return

        if not self.seen.mark_seen(message.msg_id):
            report.skipped_duplicate += 1
            return

        hit = rule_match(message.content)
        if hit is None:
            report.filtered_out += 1
            return

        candidate = self.confirmer.confirm(message, hit, context)
        if candidate is None:
            report.unresolved += 1
            return

        evidence = {
            "msg_id": message.msg_id,
            "talker": message.talker,
            # Who said it — the board shows this ("谁说的"). Empty string means
            # chatlog did not name the sender; we never substitute the talker id.
            "sender": message.sender,
            "sent_at": message.sent_at,
            # Only the matched snippet is ever persisted or forwarded.
            "snippet": snippet(message.content),
            # Source weight: "task"-tier talkers get a higher number so the
            # board / report can surface them above "info" noise. This is the
            # only behaviour change tied to tiers — everything else is gated
            # upstream by the scope layer.
            "tier": candidate.tier,
            "source_weight": candidate.weight,
        }
        talker_name = context.talker_name if context is not None else None
        if talker_name:
            evidence["talker_name"] = talker_name

        target_id = self._find_change_target(candidate) if candidate.is_change else None
        if target_id:
            # Consumed by ingest_occurrences (never stored): turns this
            # message into an ADR-0009 pending change on the target event.
            evidence["target_event_id"] = target_id

        # WP-COLLECTOR-ICS: chatlog is the first Collector implementation —
        # the final write goes through the same shared landing path as the
        # timetable (and every future) source. apply="pending": message-
        # derived facts still await the human decision (ADR-0009).
        result = ingest_occurrences(
            self.service,
            [
                Occurrence(
                    source=self.source_id,
                    external_id=message.msg_id,
                    title=candidate.title,
                    start_at=candidate.start_at,
                    end_at=candidate.end_at,
                    kind=candidate.kind,
                    location=None,
                    evidence=evidence,
                )
            ],
            apply="pending",
        )

        if result["created"]:
            report.created.append(result["ids"][0])
        elif result["updated"]:
            report.updated.append(result["ids"][0])
        # unchanged (change target matched but nothing moved): no report entry,
        # exactly like the pre-Protocol "not result.changed_fields" early return.

    def _find_change_target(self, candidate: Candidate) -> Optional[str]:
        """Best-effort: which existing event is this message rescheduling?

        Same day plus a shared topic word is a deliberately conservative test —
        a wrong guess would rewrite an unrelated entry, and the fallback
        (creating a separate pending entry) is cheap for the user to dismiss.

        Both ``manual`` and ``confirmed`` events are valid re-targets
        (ARCH-RULING 28.4, which supersedes the earlier "skip manual" rule from
        ruling 27): a message may reschedule a slot the user entered by hand —
        e.g. "the maths lecture moved to 10:00" should become a pending change
        on that manual entry, not a brand-new one. This is safe because the
        change lands as ``status=pending`` with the pre-change values snapshotted
        into ``prev_value`` (ADR-0009): nothing is overwritten until the user
        confirms, and ignoring/dismissing the card rolls the event back. It is a
        deferral, not a silent write — so manually-authored entries are no longer
        skipped. Only ``confirmed`` events are still eligible, so an already
        pending entry can't be re-targeted and start a notification loop.
        """
        try:
            day = datetime.fromisoformat(candidate.start_at).date()
        except ValueError:
            return None

        wanted = _topic_tokens(candidate.title)
        if not wanted:
            return None

        start = datetime.combine(day, datetime.min.time())
        end = datetime.combine(day, datetime.max.time())

        for event in self.service.list_agenda(start, end):
            # Only match against *confirmed* events — pending events are
            # already awaiting user decision and should not be re-targeted
            # by a new change message, which would create notification loops.
            if event.status == "confirmed":
                if _topic_tokens(event.title) & wanted:
                    return event.id
        return None

    # --- batch --------------------------------------------------------------

    def run_once(self, day: Optional[date] = None, lookback_days: int = 1) -> IngestReport:
        """Sweep conversations for one day, or for a lookback window.

        ``lookback_days=3`` sweeps three days ending today (or ending at
        ``day`` when one is given), oldest first; the default ``1`` keeps the
        exact single-day behaviour the 10-minute watchdog relies on.

        Mode drives which talkers are swept:
          * blacklist — enumerate every conversation (``/api/v1/session``),
            drop the blacklisted ones, then sweep only ``known`` talkers.
            Unreviewed talkers are never touched: during the first-run review
            (``review_done`` false) nothing is swept at all; afterwards a
            brand-new talker is recorded as ``pending`` for the board instead
            of being collected (fail-closed, ADR-0010 revision).
          * whitelist (default, fail-closed) — only the named ``talkers``.
        """
        report = IngestReport()

        if not self.config.enabled:
            logger.debug("chatlog collector disabled in config; nothing scanned")
            return report

        if self.config.mode == "blacklist":
            try:
                candidates = self.client.list_talkers()
            except ChatlogUnavailable as exc:
                logger.warning("chatlog session enumeration failed: %s", exc)
                return report
            black = set(self.config.blacklist)
            candidates = [t for t in candidates if t not in black]
            if not candidates:
                logger.warning(
                    "blacklist mode: no talkers enumerated (chatlog empty or down)"
                )
                return report

            # First-run review gate: nothing is swept until the user has
            # reviewed the existing sessions (see /api/collect/review-complete).
            if not self.talkers.review_done():
                report.review_gated = len(candidates)
                logger.info(
                    "blacklist mode: %d candidate talkers gated until the "
                    "first-run review completes (POST /api/collect/review-complete)",
                    report.review_gated,
                )
                return report

            talkers = []
            for talker in candidates:
                status = self.talkers.status_of(talker)
                if status == "excluded":
                    report.skipped_excluded += 1
                    continue
                if status == "known":
                    talkers.append(talker)
                    continue
                # Fail-closed: never collected, surfaced as pending instead.
                self.talkers.mark_pending(talker)
                report.skipped_new_talker += 1

            if not talkers:
                logger.warning("blacklist mode: no known talkers to sweep")
                return report
        else:
            if not self.config.talkers:
                logger.warning("chatlog collector enabled but the whitelist is empty")
                return report
            talkers = self.config.talkers

        # Lookback window: ``lookback_days`` days ending today (or ending at
        # ``day``), oldest first. The default single-day sweep passes ``day``
        # through untouched so the routine watchdog behaves exactly as before.
        lookback_days = max(1, int(lookback_days))
        if lookback_days > 1:
            anchor = day or datetime.now().date()
            days: list[Optional[date]] = [
                anchor - timedelta(days=offset)
                for offset in range(lookback_days - 1, -1, -1)
            ]
        else:
            days = [day]

        name_map = self._talker_name_map()

        for talker in talkers:
            talker_name = name_map.get(talker)
            for sweep_day in days:
                try:
                    messages = self.client.fetch(talker, sweep_day)
                except ChatlogUnavailable as exc:
                    # Service down or WeChat logged out — surface once, keep going.
                    logger.warning(
                        "chatlog fetch failed for %s (%s): %s", talker, sweep_day, exc
                    )
                    continue

                # ±1 neighbours within the same talker+day batch: "约一下" or
                # "明天" only makes sense with the line before/after. Capped at
                # one on each side and clipped by ``rules.snippet`` (ADR-0010
                # narrow opening for WP-EXTRACT-CONTEXT).
                for index, message in enumerate(messages):
                    context = ConfirmContext(
                        talker_name=talker_name,
                        prev_snippet=snippet(messages[index - 1].content)
                        if index > 0
                        else None,
                        next_snippet=snippet(messages[index + 1].content)
                        if index + 1 < len(messages)
                        else None,
                    )
                    self.handle_message(message, report, context)

        return report


class ChatlogDead(RuntimeError):
    """Dead door: chatlog is down or consecutively failing.

    §8.2 Z4 — L1 must say collection is unreachable. Never return an empty
    successful agenda as if the sweep ran.
    """


def chatlog_is_healthy(client) -> bool:
    """True when ``client.healthy()`` succeeds. Any exception is unhealthy."""
    healthy = getattr(client, "healthy", None)
    if healthy is None:
        return False
    try:
        return bool(healthy())
    except Exception:
        return False


def _event_brief(event) -> dict:
    return {
        "id": event.id,
        "title": event.title,
        "start_at": event.start_at,
        "end_at": event.end_at,
        "kind": event.kind,
        "status": event.status,
        "source": event.source,
    }


def agenda_window_summary(service=None, *, now: Optional[datetime] = None) -> dict:
    """Structured today + tomorrow events for L1 to turn into speech (Q1).

    Zero chat API — this is a SQLite read through :class:`AgendaService`.
    """
    from plobi.agenda import get_service

    moment = now or datetime.now()
    today = moment.date()
    tomorrow = today + timedelta(days=1)
    svc = service or get_service()
    events = svc.list_agenda(
        datetime.combine(today, datetime.min.time()),
        datetime.combine(tomorrow, datetime.max.time()),
    )
    today_events: list[dict] = []
    tomorrow_events: list[dict] = []
    for event in events:
        try:
            day = datetime.fromisoformat(str(event.start_at)).date()
        except ValueError:
            day = today
        brief = _event_brief(event)
        if day == tomorrow:
            tomorrow_events.append(brief)
        else:
            today_events.append(brief)
    return {
        "today": today.isoformat(),
        "tomorrow": tomorrow.isoformat(),
        "today_events": today_events,
        "tomorrow_events": tomorrow_events,
        "events": [*today_events, *tomorrow_events],
    }


def refresh_agenda(
    *,
    pipeline: Optional[ChatlogPipeline] = None,
    now: Optional[datetime] = None,
) -> tuple[IngestReport, dict]:
    """One collection sweep + board snapshot for ``plobi_secretary_ask``.

    Raises :class:`ChatlogDead` when chatlog is not healthy. Does not invent
    events. Uncertain new messages still go through the pipeline confirmer
    (H2: heuristic, then existing ``model_confirm`` — not aigw).
    """
    pipe = pipeline or ChatlogPipeline()
    if not chatlog_is_healthy(pipe.client):
        raise ChatlogDead("chatlog 未启动或 /health 失败，采集不通")
    report = pipe.run_once()
    summary = agenda_window_summary(pipe.service, now=now)
    # One-shot widening (WP-M1-COLLECT-CLOSE): after the first-run review,
    # an empty today+tomorrow board gets a single 3-day lookback sweep before
    # L1 reports an honest zero. Boards that already show events — and
    # states where the review has not completed — are left alone so a
    # routine ask never pays 3 days × all known talkers.
    if (
        pipe.talkers.review_done()
        and not summary["today_events"]
        and not summary["tomorrow_events"]
    ):
        report = pipe.run_once(lookback_days=DEFAULT_LOOKBACK_DAYS)
        summary = agenda_window_summary(pipe.service, now=now)
        summary["lookback_widened"] = True
    summary["ingest"] = report.as_dict()
    return report, summary
