"""TimetableCollector + one-shot import (WP-COLLECTOR-ICS).

The collector only *reads*: ``pull()`` returns parsed Occurrences and never
touches the agenda. Writing happens exclusively through
:func:`plobi.collectors.ingest.ingest_occurrences` (via :func:`import_ics`),
so the ics source can never fork the storage semantics the chatlog source
uses.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from plobi.agenda import get_service
from plobi.agenda.service import AgendaService

from ..base import Occurrence
from ..ingest import ApplyMode, ingest_occurrences
from .parser import parse_ics


class TimetableCollector:
    """:class:`plobi.collectors.base.Collector` over a local ``.ics`` file."""

    source_id = "timetable"

    def __init__(self, path: str | Path):
        self.path = str(path)

    def health(self) -> dict:
        """``{"ok": bool, "detail": str}`` — does the file exist and read?"""
        file_path = Path(self.path)
        if not file_path.is_absolute():
            return {"ok": False, "detail": f"path is not absolute: {self.path}"}
        if not file_path.is_file():
            return {"ok": False, "detail": f"file not found: {self.path}"}
        try:
            with file_path.open("rb") as handle:
                handle.read(1)
        except OSError as exc:
            return {"ok": False, "detail": f"file not readable: {exc}"}
        return {"ok": True, "detail": self.path}

    def pull(self, *, since: Any = None) -> list[Occurrence]:
        """All concrete course sessions in the file. Pure read, no DB write."""
        return parse_ics(self.path)


def import_ics(
    path: str | Path,
    *,
    apply: ApplyMode = "confirmed",
    service: Optional[AgendaService] = None,
) -> dict:
    """Parse + land: ``ingest_occurrences`` over every occurrence in the file.

    ``apply="confirmed"`` (default) writes rows straight to confirmed — the
    exported calendar already is the user's truth. Returns the shared ingest
    result ``{created, updated, unchanged, ids}``; a missing row in the file
    never deletes anything from the agenda.
    """
    occurrences = parse_ics(path)
    return ingest_occurrences(service or get_service(), occurrences, apply=apply)
