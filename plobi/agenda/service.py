"""Agenda business rules — the only surface other modules should use.

Owns the semantics the spec cares about: message-derived changes land as
``pending`` with a new/old diff, the user confirms or dismisses them, and
dismissal restores whatever was true before (ADR-0009). Manual entries are
facts and land ``confirmed`` immediately.

No HTTP, no LLM, no Mind knowledge. The collector hands over already-confirmed
candidates; the Mind writer pulls :meth:`AgendaService.daily_summary`.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Optional

from . import store
from .store import AgendaValidationError, DailyPlan, Event

# Fields a pending change may overwrite, and which we snapshot into prev_value.
_DIFFABLE = ("title", "start_at", "end_at")


class AgendaError(Exception):
    """Business-rule failure (not found, illegal transition, expired seq)."""


class EventNotFound(AgendaError):
    pass


class PlanNotFound(AgendaError):
    pass


class ConfirmSeqExpired(AgendaError):
    pass


class ManualTargetMissing(AgendaError):
    """Conversational change/cancel matched zero events (never guess)."""


class ManualTargetAmbiguous(AgendaError):
    """Conversational change/cancel matched 2+ events; carries candidates."""

    def __init__(self, candidates: list[dict]):
        super().__init__("multiple candidate events matched")
        self.candidates = candidates


def _candidate_brief(event: Event) -> dict:
    return {
        "id": event.id,
        "title": event.title,
        "start_at": event.start_at,
    }


def _plan_is_empty(plan: DailyPlan) -> bool:
    """Empty plans are honest zero-event snapshots; they stay out of 改动率."""
    return plan.status == "empty" or plan.event_count <= 0


@dataclass
class IngestResult:
    event: Event
    created: bool
    changed_fields: list[str]


class AgendaService:
    def __init__(self, db_path: Path | str | None = None):
        self._db_path = db_path

    @property
    def db_path(self) -> Path | str | None:
        """Public accessor for the database path (used by stats, router, etc.)."""
        return self._db_path

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = store.connect(self._db_path)
        try:
            yield conn
        finally:
            conn.close()

    # --- reads --------------------------------------------------------------

    def list_agenda(
        self,
        start_from: Any = None,
        start_to: Any = None,
        *,
        include_cancelled: bool = False,
    ) -> list[Event]:
        """Default window is today 00:00 through tomorrow 23:59:59.

        Dedupes by event.id after the ordering pass (WP-AXIS-INSIGHT root
        cause fix): upstream callers sometimes append the same event twice
        (e.g. when a day-window query overlaps with the default today+tomorrow
        window) and the SQL boundary comparison would pass both copies
        through. Frontend dedupe is one line of defense; this is the root
        — the SQL boundary *does* pass `2026-09-16T11:30:00 >= 2026-09-17T00:00:00`
        = False, so duplicates in real DBs are not the SQLite fault the user
        suspected, but the dedupe is still the right safety. First occurrence
        wins; ordering from `_order_by_confirmed_plans` is preserved.
        """
        if start_from is None and start_to is None:
            today = datetime.now().date()
            start_from = datetime.combine(today, datetime.min.time())
            start_to = datetime.combine(today + timedelta(days=1), datetime.max.time())
        with self._conn() as conn:
            events = store.list_events(
                conn,
                start_from=start_from,
                start_to=start_to,
                include_cancelled=include_cancelled,
            )
            ordered = self._order_by_confirmed_plans(conn, events)
            seen_ids: set[str] = set()
            deduped: list[Event] = []
            for event in ordered:
                if event.id in seen_ids:
                    continue
                seen_ids.add(event.id)
                deduped.append(event)
            return deduped

    def get(self, event_id: str) -> Event:
        with self._conn() as conn:
            event = store.get_event(conn, event_id)
        if event is None:
            raise EventNotFound(event_id)
        return event

    def list_pending(self) -> list[Event]:
        with self._conn() as conn:
            return store.list_events(conn, status="pending")

    def get_plan(self, for_date: str | None = None) -> DailyPlan | None:
        """Plan header for ``for_date`` (default: today). Missing → ``None``."""
        day = for_date or datetime.now().date().isoformat()
        with self._conn() as conn:
            return store.get_daily_plan(conn, day)

    def list_pending_plans(self) -> list[DailyPlan]:
        """All ``pending`` daily plans, oldest ``for_date`` first."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id FROM daily_plans WHERE status = 'pending' ORDER BY for_date ASC"
            ).fetchall()
            plans: list[DailyPlan] = []
            for row in rows:
                plan = store.get_daily_plan_by_id(conn, row["id"])
                if plan is not None:
                    plans.append(plan)
            return plans

    def _order_by_confirmed_plans(
        self, conn: sqlite3.Connection, events: list[Event]
    ) -> list[Event]:
        """C3: days with a confirmed plan follow ``plan_items.sort_order``.

        Dates without a confirmed plan keep the store's start_at order.
        Events on a planned day that are not in the plan stay after plan items.
        """
        if not events:
            return events

        groups: dict[str, list[Event]] = {}
        date_order: list[str] = []
        for event in events:
            day = event.start_at[:10] if len(event.start_at) >= 10 else ""
            if day not in groups:
                groups[day] = []
                date_order.append(day)
            groups[day].append(event)

        ordered: list[Event] = []
        for day in date_order:
            chunk = groups[day]
            if not day:
                ordered.extend(chunk)
                continue
            plan = store.get_daily_plan(conn, day)
            if plan is None or plan.status != "confirmed":
                ordered.extend(chunk)
                continue
            items = store.list_plan_items(conn, plan.id)
            if not items:
                ordered.extend(chunk)
                continue
            rank = {item.event_id: item.sort_order for item in items}
            in_plan = [event for event in chunk if event.id in rank]
            rest = [event for event in chunk if event.id not in rank]
            in_plan.sort(key=lambda event: rank[event.id])
            ordered.extend(in_plan)
            ordered.extend(rest)
        return ordered

    # --- manual edits (user is the source of truth) -------------------------

    def create_manual(
        self,
        *,
        title: str,
        start_at: Any,
        end_at: Any = None,
        kind: str = "task",
        location: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> Event:
        """WP-DAY-SURFACE: 手写日程可带地点/备注；空串视为未填。

        时间轴重叠是「事实」，**不**拒绝写入（人令不拒；重叠由看板标红）。
        """
        text = (str(location).strip() if location is not None else "") or None
        text_notes = (str(notes).strip() if notes is not None else "") or None
        with self._conn() as conn:
            return store.create_event(
                conn,
                title=title,
                start_at=start_at,
                end_at=end_at,
                kind=kind,
                status="confirmed",
                source="manual",
                location=text,
                notes=text_notes,
            )

    def update_manual(self, event_id: str, **fields: Any) -> Event:
        allowed = {"title", "start_at", "end_at", "kind", "location", "notes"}
        unknown = set(fields) - allowed
        if unknown:
            raise AgendaValidationError(f"cannot edit field(s): {sorted(unknown)}")
        patch = {}
        for k, v in fields.items():
            if v is None:
                continue
            if k in {"location", "notes"}:
                patch[k] = (str(v).strip() or None) if v is not None else None
            else:
                patch[k] = v
        with self._conn() as conn:
            current = store.get_event(conn, event_id)
            if current is None:
                raise EventNotFound(event_id)
            updated = store.update_event(conn, event_id, **patch)
            if patch:
                store.log_action(
                    conn,
                    event_id=event_id,
                    action="manual_edit",
                    event_source=current.source,
                )
        assert updated is not None
        return updated

    def delete(self, event_id: str) -> None:
        with self._conn() as conn:
            current = store.get_event(conn, event_id)
            if current is None or not store.delete_event(conn, event_id):
                raise EventNotFound(event_id)
            store.log_action(
                conn,
                event_id=event_id,
                action="manual_delete",
                event_source=current.source,
            )

    def resolve_manual_target(
        self,
        *,
        event_id: Optional[str] = None,
        title: Optional[str] = None,
        day: Optional[str] = None,
        statuses: Optional[tuple[str, ...]] = None,
    ) -> Event:
        """Locate the single event a conversational change/cancel refers to.

        WP-L1-AGENDA-MUTATE: talking to the secretary must reach manual,
        confirmed and pending rows alike. The chatlog collector no longer
        skips manual rows when a group message re-schedules one — that path
        lands as ``pending`` + ``prev_value`` (裁定 28.4 with ADR-0009), so a
        hand-written row can be re-arranged without being overwritten, and
        rejecting the change rolls it back.

        ``event_id`` wins when present and existing. Otherwise ``title``
        (case-insensitive substring) is matched within ``day``'s events
        (``YYYY-MM-DD``); without ``day`` the default agenda window
        (today..tomorrow) is searched. ``statuses`` (WP-SEC-VOCAB) narrows
        candidates — by id and by title alike — for callers that may only act
        on ``pending`` rows. Zero hits →
        :class:`ManualTargetMissing`; 2+ hits →
        :class:`ManualTargetAmbiguous` with candidate briefs. Never guesses.
        """
        allowed = set(statuses) if statuses is not None else None

        if event_id:
            event = self.get(event_id)
            if allowed is not None and event.status not in allowed:
                raise ManualTargetMissing(
                    f"「{event.title}」现在是 {event.status}，"
                    f"不在 {'/'.join(sorted(allowed))} 里"
                )
            return event

        keyword = (title or "").strip().lower()
        if not keyword:
            raise ManualTargetMissing("需要 event_id 或 title 才能定位那条日程")

        if day:
            target_date = datetime.fromisoformat(str(day)).date()
            start_from = datetime.combine(target_date, datetime.min.time())
            start_to = datetime.combine(target_date, datetime.max.time())
            events = self.list_agenda(start_from, start_to)
        else:
            events = self.list_agenda()

        hits = [event for event in events if keyword in event.title.lower()]
        if allowed is not None:
            hits = [event for event in hits if event.status in allowed]
        if not hits:
            raise ManualTargetMissing(
                f"找不到标题含 {title!r} 的那条日程"
                + (f"（{day}）" if day else "")
            )
        if len(hits) > 1:
            raise ManualTargetAmbiguous([_candidate_brief(e) for e in hits])
        return hits[0]

    # --- message-derived changes (land as pending) --------------------------

    def ingest_candidate(
        self,
        *,
        title: str,
        start_at: Any,
        end_at: Any = None,
        kind: str = "task",
        source: str = "wechat",
        evidence: Optional[dict] = None,
        target_event_id: Optional[str] = None,
    ) -> IngestResult:
        """Record a model-confirmed candidate as a pending change.

        ``target_event_id`` marks this as a change to an existing event: the
        current values are snapshotted into ``prev_value`` so the board can
        show a before/after and ``dismiss`` can roll back.
        """
        if source == "manual":
            raise AgendaValidationError("manual entries must use create_manual()")

        with self._conn() as conn:
            seq = store.next_confirm_seq(conn)
            stamp = store.now_iso()

            if target_event_id:
                current = store.get_event(conn, target_event_id)
                if current is None:
                    raise EventNotFound(target_event_id)

                incoming = {
                    "title": (title or current.title).strip() or current.title,
                    "start_at": store.normalize_dt(start_at, field_name="start_at"),
                    "end_at": store.normalize_dt(end_at, field_name="end_at", required=False),
                }
                previous = {k: getattr(current, k) for k in _DIFFABLE}
                changed = [k for k in _DIFFABLE if incoming[k] != previous[k]]
                if not changed:
                    return IngestResult(event=current, created=False, changed_fields=[])

                updated = store.update_event(
                    conn,
                    target_event_id,
                    **incoming,
                    kind=kind or current.kind,
                    status="pending",
                    source=source,
                    evidence=evidence,
                    # Keep the oldest known-good snapshot if one is already
                    # pending, so repeated re-schedules stay rollback-able.
                    prev_value=current.prev_value or previous,
                    confirm_seq=seq,
                    confirm_seq_at=stamp,
                )
                assert updated is not None
                return IngestResult(event=updated, created=False, changed_fields=changed)

            created = store.create_event(
                conn,
                title=title,
                start_at=start_at,
                end_at=end_at,
                kind=kind,
                status="pending",
                source=source,
                evidence=evidence,
                confirm_seq=seq,
                confirm_seq_at=stamp,
            )
            return IngestResult(event=created, created=True, changed_fields=list(_DIFFABLE))

    # --- human decisions ----------------------------------------------------

    def confirm(self, event_id: str) -> Event:
        with self._conn() as conn:
            current = store.get_event(conn, event_id)
            if current is None:
                raise EventNotFound(event_id)
            if current.status != "pending":
                return current
            updated = store.update_event(
                conn,
                event_id,
                status="confirmed",
                prev_value=None,
                confirm_seq=None,
                confirm_seq_at=None,
            )
            store.log_action(
                conn,
                event_id=event_id,
                action="confirm",
                event_source=current.source,
            )
        assert updated is not None
        return updated

    def dismiss(self, event_id: str) -> Optional[Event]:
        """Reject a pending change.

        Rolls back to ``prev_value`` when the event existed before; deletes it
        when the message proposed a brand-new event. Returns ``None`` after a
        delete so callers can tell the two outcomes apart.

        Safety: if ``prev_value`` was a non-empty string that failed to parse
        (corrupted JSON), we refuse to delete and instead keep the event in
        ``pending`` state — data loss from a silently corrupted field is worse
        than a stuck pending entry.
        """
        with self._conn() as conn:
            current = store.get_event(conn, event_id)
            if current is None:
                raise EventNotFound(event_id)
            if current.status != "pending":
                return current

            if not current.prev_value:
                store.log_action(
                    conn,
                    event_id=event_id,
                    action="dismiss",
                    event_source=current.source,
                )
                store.delete_event(conn, event_id)
                return None

            restore = {k: current.prev_value.get(k) for k in _DIFFABLE}
            updated = store.update_event(
                conn,
                event_id,
                **restore,
                status="confirmed",
                prev_value=None,
                confirm_seq=None,
                confirm_seq_at=None,
            )
            store.log_action(
                conn,
                event_id=event_id,
                action="dismiss",
                event_source=current.source,
            )
        return updated

    # --- daily plan decisions (same machine as events; events stay put) -----

    def confirm_plan(self, plan_id: str) -> DailyPlan:
        """``pending`` / ``empty`` → ``confirmed``. Never mutates ``events``."""
        with self._conn() as conn:
            current = store.get_daily_plan_by_id(conn, plan_id)
            if current is None:
                raise PlanNotFound(plan_id)
            if current.status not in ("pending", "empty"):
                return current
            updated = store.set_plan_status(
                conn, plan_id, "confirmed", clear_seq=True
            )
            if not _plan_is_empty(current):
                store.log_action(
                    conn,
                    event_id=plan_id,
                    action="confirm",
                    event_source="plan",
                    detail="plan",
                )
        assert updated is not None
        return updated

    def dismiss_plan(self, plan_id: str) -> DailyPlan:
        """``pending`` / ``empty`` → ``dismissed``. Never mutates ``events``."""
        with self._conn() as conn:
            current = store.get_daily_plan_by_id(conn, plan_id)
            if current is None:
                raise PlanNotFound(plan_id)
            if current.status not in ("pending", "empty"):
                return current
            updated = store.set_plan_status(
                conn, plan_id, "dismissed", clear_seq=True
            )
            if not _plan_is_empty(current):
                store.log_action(
                    conn,
                    event_id=plan_id,
                    action="dismiss",
                    event_source="plan",
                    detail="plan",
                )
        assert updated is not None
        return updated

    # --- DingTalk short-sequence protocol -----------------------------------

    def resolve_by_seq(self, seq: int, *, accept: bool) -> Event | DailyPlan | store.CheckinCard | None:
        """Pending event → plan → checkin card. Expired stays expired."""
        with self._conn() as conn:
            event = store.find_by_confirm_seq(conn, seq)
            plan = None if event is not None else store.find_plan_by_confirm_seq(conn, seq)
            card = (
                None
                if event is not None or plan is not None
                else store.find_card_by_confirm_seq(conn, seq)
            )

        if event is not None:
            if not store.confirm_seq_is_live(event):
                raise ConfirmSeqExpired(f"confirm_seq={seq}")
            return self.confirm(event.id) if accept else self.dismiss(event.id)

        if plan is not None:
            if not store.confirm_seq_is_live(plan):
                raise ConfirmSeqExpired(f"confirm_seq={seq}")
            return self.confirm_plan(plan.id) if accept else self.dismiss_plan(plan.id)

        if card is not None:
            if not store.confirm_seq_is_live(card):
                raise ConfirmSeqExpired(f"confirm_seq={seq}")
            return self.confirm_card(card.id) if accept else self.dismiss_card(card.id)

        raise EventNotFound(f"confirm_seq={seq}")

    # --- C6 checkin cards ----------------------------------------------------

    def confirm_card(self, card_id: str) -> store.CheckinCard:
        """确认回访/提案卡；配置提案卡在此刻落地（R3：人批才改）。"""
        with self._conn() as conn:
            card = store.get_checkin_card_by_id(conn, card_id)
            if card is None:
                raise EventNotFound(card_id)
            if card.kind == "config_proposal":
                from .checkin import apply_proposal

                apply_proposal(conn, card)
            store.log_action(
                conn,
                event_id=card.id,
                action="confirm",
                event_source="card",
                detail=card.kind,
            )
            updated = store.set_card_status(conn, card_id, "confirmed", clear_seq=True)
        assert updated is not None
        return updated

    def dismiss_card(self, card_id: str) -> store.CheckinCard:
        with self._conn() as conn:
            card = store.get_checkin_card_by_id(conn, card_id)
            if card is None:
                raise EventNotFound(card_id)
            store.log_action(
                conn,
                event_id=card.id,
                action="dismiss",
                event_source="card",
                detail=card.kind,
            )
            updated = store.set_card_status(conn, card_id, "dismissed", clear_seq=True)
        assert updated is not None
        return updated

    # --- Mind hand-off (consumed by the serial writer, not by this module) --

    def daily_summary(self, day: Optional[datetime] = None) -> str:
        """Human-readable markdown digest for one day."""
        target = (day or datetime.now()).date()
        start = datetime.combine(target, datetime.min.time())
        end = datetime.combine(target, datetime.max.time())
        events = self.list_agenda(start, end)

        lines = [f"# 日程摘要 {target.isoformat()}", ""]
        if not events:
            lines.append("- （当日无日程）")
            return "\n".join(lines) + "\n"

        for event in events:
            when = event.start_at[11:16] if len(event.start_at) >= 16 else event.start_at
            flag = "（待确认）" if event.status == "pending" else ""
            lines.append(f"- {when} [{event.kind}] {event.title}{flag} — 来源 {event.source}")
        return "\n".join(lines) + "\n"


    # --- source-direct creation (WP-COLLECTOR-ICS) --------------------------

    def create_from_source(
        self,
        *,
        title: str,
        start_at: Any,
        end_at: Any = None,
        kind: str = "task",
        source: str,
        evidence: Optional[dict] = None,
    ) -> Event:
        """Land a confirmed event straight from a trusted structured source.

        Timetable .ics import is the first user: the calendar file already is
        the truth, so rows skip the pending queue — unlike message-derived
        candidates, which must land as ``pending`` (ADR-0009). ``source``
        must be a non-manual entry of ``store.SOURCES``; manual creation
        stays on :meth:`create_manual`.
        """
        if source == "manual":
            raise AgendaValidationError("manual entries must use create_manual()")
        with self._conn() as conn:
            return store.create_event(
                conn,
                title=title,
                start_at=start_at,
                end_at=end_at,
                kind=kind,
                status="confirmed",
                source=source,
                evidence=evidence,
            )


_DEFAULT: Optional[AgendaService] = None
_DEFAULT_LOCK = threading.Lock()


def get_service() -> AgendaService:
    """Process-wide default service (used by the HTTP layer)."""
    global _DEFAULT
    if _DEFAULT is None:
        with _DEFAULT_LOCK:
            if _DEFAULT is None:
                _DEFAULT = AgendaService()
    return _DEFAULT
