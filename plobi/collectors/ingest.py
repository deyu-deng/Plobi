"""Shared landing path for every collector (WP-COLLECTOR-ICS).

One function, one set of semantics — chatlog, timetable ics and future sources
all call :func:`ingest_occurrences`; none of them talks to the store directly.

Dedupe contract: an item is identified by ``(source, evidence["uid"] or
external_id)``. Collectors that carry a richer source id put it in
``evidence["uid"]``; otherwise ``external_id`` is the key.

Behaviour matrix (mirrors the task ruling / ADR-0009):

* no existing row, ``apply="pending"``   → ``ingest_candidate`` (pending card)
* no existing row, ``apply="confirmed"`` → ``create_from_source`` (straight to
  confirmed; never fakes ``source="manual"``)
* existing row and title/start/end changed → ``ingest_candidate(...,
  target_event_id=existing)`` → pending change with ``prev_value`` snapshot
* existing row, nothing changed           → skip (``unchanged``)
* a row missing from this pull is NEVER deleted — sources are additive.

Change-target seam: chatlog's reschedule matcher (``_find_change_target``)
sets ``evidence["target_event_id"]`` when the message re-schedules a known
event. This module consumes (pops) that key and turns it into
``ingest_candidate(target_event_id=...)``; it never reaches stored evidence,
so the snippet-only evidence contract stays intact.
"""

from __future__ import annotations

from typing import Literal

from plobi.agenda import store
from plobi.agenda.service import AgendaService

from .base import Occurrence

ApplyMode = Literal["pending", "confirmed"]


def _row_keys(event: store.Event) -> set[str]:
    """Every id an existing row can be looked up by.

    The events table has no external-id column, so source ids survive only in
    evidence. ``uid`` is the primary contract; ``msg_id`` / ``external_id``
    keep rows written before (or beside) the uid convention dedupe-able.
    """
    evidence = event.evidence or {}
    keys = set()
    for field_name in ("uid", "msg_id", "external_id"):
        value = evidence.get(field_name)
        if value:
            keys.add(str(value))
    if not keys:
        # Nothing persisted to dedupe on — fall back to the row's own id so
        # the row is at least idempotent against itself.
        keys.add(event.id)
    return keys


def _existing_index(service: AgendaService, items: list[Occurrence]) -> dict:
    """Map ``(source, uid or external_id)`` → Event for the sources in play.

    Cancelled rows are included on purpose: a class the user cancelled stays
    cancelled on re-import (the row matches and is unchanged) instead of being
    silently resurrected as a new confirmed entry.
    """
    sources = sorted({item.source for item in items})
    index: dict[tuple[str, str], store.Event] = {}
    conn = store.connect(service.db_path)
    try:
        for source in sources:
            for event in store.list_events(conn, source=source, include_cancelled=True):
                for key in _row_keys(event):
                    index[(event.source, key)] = event
    finally:
        conn.close()
    return index


def ingest_occurrences(
    service: AgendaService,
    items: list[Occurrence],
    *,
    apply: ApplyMode,
) -> dict:
    """Land collector output through the one agenda write path.

    Returns ``{"created": int, "updated": int, "unchanged": int,
    "ids": [event ids touched, created first then updated]}``.
    """
    counts = {"created": 0, "updated": 0, "unchanged": 0}
    ids: list[str] = []
    index = _existing_index(service, items)

    for occurrence in items:
        evidence = dict(occurrence.evidence or {})
        # Chatlog change-target seam — consumed here, never stored.
        target_id = evidence.pop("target_event_id", None)
        # The events table has no location column; structured location rides
        # in evidence (explicit evidence value wins over the field).
        if occurrence.location and not evidence.get("location"):
            evidence["location"] = occurrence.location

        title = (occurrence.title or "").strip()
        start_at = store.normalize_dt(occurrence.start_at, field_name="start_at")
        end_at = store.normalize_dt(
            occurrence.end_at, field_name="end_at", required=False
        )

        key = str(evidence.get("uid") or occurrence.external_id)
        existing = index.get((occurrence.source, key))

        if existing is None:
            if apply == "confirmed":
                event = service.create_from_source(
                    title=title,
                    start_at=start_at,
                    end_at=end_at,
                    kind=occurrence.kind,
                    source=occurrence.source,
                    evidence=evidence,
                )
                index[(occurrence.source, key)] = event
                counts["created"] += 1
                ids.append(event.id)
            else:
                result = service.ingest_candidate(
                    title=title,
                    start_at=start_at,
                    end_at=end_at,
                    kind=occurrence.kind,
                    source=occurrence.source,
                    evidence=evidence,
                    target_event_id=target_id,
                )
                if result.created:
                    index[(occurrence.source, key)] = result.event
                    counts["created"] += 1
                    ids.append(result.event.id)
                elif result.changed_fields:
                    index[(occurrence.source, key)] = result.event
                    counts["updated"] += 1
                    ids.append(result.event.id)
                else:
                    # A change target that matched but did not move: no-op,
                    # exactly like the pre-Protocol direct ingest path.
                    counts["unchanged"] += 1
            continue

        changed = (
            title != existing.title
            or start_at != existing.start_at
            or end_at != existing.end_at
        )
        if not changed:
            counts["unchanged"] += 1
            continue

        result = service.ingest_candidate(
            title=title,
            start_at=start_at,
            end_at=end_at,
            kind=occurrence.kind,
            source=occurrence.source,
            evidence=evidence,
            target_event_id=existing.id,
        )
        if result.changed_fields:
            index[(occurrence.source, key)] = result.event
            counts["updated"] += 1
            ids.append(result.event.id)
        else:
            counts["unchanged"] += 1

    return {
        "created": counts["created"],
        "updated": counts["updated"],
        "unchanged": counts["unchanged"],
        "ids": ids,
    }
