"""Pluggable collector surface (WP-COLLECTOR-ICS).

Every source — WeChat chatlog today, timetable .ics and DingTalk tomorrow —
implements :class:`Collector` and yields :class:`Occurrence` rows. Landing in
the agenda always goes through
:func:`plobi.collectors.ingest.ingest_occurrences` so no source can fork the
storage semantics (pending vs confirmed, dedupe, ADR-0009 change diffs).

An ``Occurrence`` is one *concrete* event: a weekly course repeated 16 times
is 16 occurrences, each with its own stable ``external_id``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable


@dataclass(frozen=True)
class Occurrence:
    """One concrete event a collector pulled from a source.

    ``source``       — one of ``plobi.agenda.store.SOURCES`` (never ``manual``).
    ``external_id``  — stable dedupe key (ics UID / WeChat msg_id / …); must
                       survive re-pulls of the same source unchanged.
    ``start_at``     — local naive ISO (``YYYY-MM-DDTHH:MM:SS``), matching how
                       the events table stores timestamps.
    ``evidence``     — source-specific provenance; for chatlog this is the
                       snippet-only privacy contract, for ics the UID/teacher.
    """

    source: str
    external_id: str
    title: str
    start_at: str
    end_at: Optional[str] = None
    kind: str = "task"
    location: Optional[str] = None
    evidence: dict = field(default_factory=dict)


@runtime_checkable
class Collector(Protocol):
    """Structural contract every source collector satisfies.

    Implementations do NOT write the agenda — :meth:`pull` only reads the
    source; landing is ``ingest_occurrences(service, pull(), apply=...)``.
    """

    source_id: str

    def health(self) -> dict:
        """``{"ok": bool, "detail": str}`` — is this source reachable?"""
        ...

    def pull(self, *, since: Any = None) -> list[Occurrence]:
        """Full or incremental pull. Pure read: never writes the agenda."""
        ...
