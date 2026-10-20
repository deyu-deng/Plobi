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

from typing import Any, Literal

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


def authoritative_round(service: AgendaService, items: list[Occurrence]) -> dict:
    """Enforce the declared ``authoritative_replace`` capability on one collector round.

    Ruling 191 asked for exactly this: the capability slot has existed in
    ``registry.CAPABILITY_KEYS`` since day one, but *nothing consumed it* — so "a row
    that is no longer in the source's own list" was never acted on, and the only way to
    close a submitted homework was the user ticking a box. This function is the consumer.
    It is deliberately **source-agnostic**: it reads the declared bit, and there is no
    per-source branch, no per-source module and no separate reconciler here.

    Semantics, both directions:

    * a row of a class this round did **not** return → 承认已完成（``completed_at``），
      **status 不改、行不删**（ruling 42.2 第 5 项：归档只标失效）。The field is
      ``completed_at`` rather than ``status='cancelled'`` on purpose:
      ``store.STATUSES`` is a closed set with no ``submitted`` in it, and the planner
      already filters on exactly this column
      (``planning.py:382`` / ``llm_planning.py:88``, ``include_completed=False``) —
      "早就交了的作业第二天又被排进来" 的病根就是这一格没人写。
    * a row previously marked completed that **is** back in this round → cleared again.
      Absence-based inference without a way back is how one network hiccup kills a
      deadline; the reverse edge is part of the contract, not an extra.

    Guards, because "not in the list" is an inference and not a field the platform sent:

    * the capability must read ``True``. ``False``（chatlog / 钉钉 / 课表）与
      ``None``（登记了但没实测）都**不动一行**，各自回一句不同的话——把"没实测"读成
      "不权威"就是下一颗假绿。
    * only the ``(source, kind)`` classes **represented in this round** are reconciled.
      A round that pulls homework says nothing about a class it never asked for.
    * an **empty round closes nothing**. Zero rows is far more likely a broken scraper,
      an expired session or a changed selector than "the student has no work left";
      the difference is one extra round of latency, the mistake is a whole term of
      deadlines marked done.
    * plan artifacts are out of reach: ``list_events`` excludes rows with
      ``evidence.created_by == 'plan'`` by default, so the scheduler's own rows can never
      be touched from here.
    """
    report: dict[str, Any] = {"closed": [], "reopened": [], "skipped": {}}
    if not items:
        report["skipped"]["(round)"] = "empty_round：这一轮什么都没拉回来 ⇒ 一行都不关"
        return report

    classes = sorted({(item.source, item.kind or "") for item in items})
    # **延迟 import**：注册表的发现正在导入采集器包，而这一层被它们调——顶层 import 会成环。
    from plobi.collectors import registry

    round_keys: dict[str, set[str]] = {}
    for item in items:
        evidence = item.evidence or {}
        round_keys.setdefault(item.source, set()).add(str(evidence.get("uid") or item.external_id))

    conn = store.connect(service.db_path)
    try:
        events_by_source: dict[str, list[store.Event]] = {}
        for source, kind in classes:
            capability = registry.capability(source, "authoritative_replace")
            if capability is not True:
                report["skipped"][source] = (
                    "not_authoritative：这一条自己声明本轮缺的不算数"
                    if capability is False else
                    "unmeasured：这一位登记了但没实测 ⇒ 不许拿猜测去关一行"
                )
                continue
            if source not in events_by_source:
                events_by_source[source] = [
                    event for event in store.list_events(conn, source=source, include_cancelled=True)
                    if not store.is_plan_artifact(event)
                ]

            present = round_keys.get(source, set())
            for event in events_by_source[source]:
                if (event.kind or "") != kind:
                    continue
                keys = _row_keys(event)
                is_done = bool(str(event.completed_at or ""))
                if keys & present:
                    if is_done:
                        service.set_completed(event.id, completed=False)
                        report["reopened"].append(event.id)
                    continue
                if not is_done and event.status != "cancelled":
                    service.set_completed(event.id, completed=True)
                    report["closed"].append(event.id)
    finally:
        conn.close()

    report["closed_count"] = len(report["closed"])
    report["reopened_count"] = len(report["reopened"])
    return report
