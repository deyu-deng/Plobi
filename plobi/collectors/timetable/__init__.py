"""Timetable (.ics) collector — the second Collector implementation.

Public surface: :func:`parse_ics` / :func:`parse_ics_with_stats`,
:class:`TimetableCollector`, :func:`import_ics`, and the board API router
(:mod:`.api`, mounted under ``/api/collect``).
"""

from .collector import TimetableCollector, import_ics
from .parser import IcsParseError, parse_ics, parse_ics_with_stats

__all__ = [
    "IcsParseError",
    "TimetableCollector",
    "import_ics",
    "parse_ics",
    "parse_ics_with_stats",
]
