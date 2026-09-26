"""RFC 5545 ``.ics`` → :class:`Occurrence` rows (WP-COLLECTOR-ICS).

Parsing uses ``icalendar`` (the Python iCalendar standard library, BSD-3).
Recurrence expansion goes through ``dateutil``'s ``rruleset`` fed by
icalendar's parsed RRULE/RDATE/EXDATE — never a hand-rolled expansion, so the
next course calendar that ships RRULEs cannot derail us.

Rulings baked in here:

* every ``VEVENT`` becomes one :class:`Occurrence` per concrete date; a
  weekly course already exported as 225 single events stays 225 rows;
* ``DTSTART`` is mandatory — a VEVENT without one is skipped and counted,
  never defaulted to 9:00 (and never invented to a 1-hour slot);
* ``VALARM`` sub-components are dropped (裁定 28.1: Plobi does not do
  reminders) and never reach evidence;
* timestamps are converted to Asia/Shanghai and stored as **naive** local
  ISO, matching how the events table stores ``start_at``/``end_at``.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from dateutil.rrule import rrulestr, rruleset
from icalendar import Calendar

from plobi.collectors.base import Occurrence

# Course calendars are Asia/Shanghai in practice; other TZIDs still convert
# correctly through zoneinfo, this constant is only the canonical target.
LOCAL_TZ = ZoneInfo("Asia/Shanghai")

# Guards against an unbounded RRULE (no COUNT/UNTIL): per-event expansion is
# capped both in count and by a two-year horizon from the event's DTSTART.
_MAX_EXPANSIONS = 1000
_EXPANSION_HORIZON = timedelta(days=730)

_TEACHER_PREFIX = "教师"


class IcsParseError(RuntimeError):
    """The file could not be read or is not a parseable iCalendar."""


def _as_local_naive(value: object) -> Optional[datetime]:
    """icalendar dt (aware datetime / all-day date) → naive local datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(LOCAL_TZ).replace(tzinfo=None)
        return value
    if isinstance(value, date):
        # All-day DATE: midnight local, no clock invented beyond that.
        return datetime(value.year, value.month, value.day)
    return None


def _extract_teacher(description: str) -> Optional[str]:
    """Teacher name from the DESCRIPTION ``教师:`` line; omitted when absent."""
    # icalendar already unescapes \n, but a literal escaped copy is harmless
    # to handle too — course exports are not known for their tidiness.
    normalized = description.replace("\r\n", "\n").replace("\\n", "\n")
    for raw_line in normalized.split("\n"):
        line = raw_line.strip()
        if line.startswith(_TEACHER_PREFIX):
            _, _, value = line.partition(":")
            value = value.strip()
            if value:
                return value
    return None


def _prop_dts(component: object, name: str) -> list:
    """All date values of a (possibly repeated, possibly multi-valued) property."""
    prop = component.get(name)  # type: ignore[attr-defined]
    if prop is None:
        return []
    entries = prop if isinstance(prop, list) else [prop]
    values: list = []
    for entry in entries:
        for holder in getattr(entry, "dts", []):
            values.append(holder.dt)
    return values


def _expand_recurrences(component: object, dtstart: datetime) -> list[datetime]:
    """Concrete dates for one VEVENT via dateutil's rruleset.

    No RRULE and no RDATE → ``[dtstart]``. With a rule: RRULE ∪ RDATE − EXDATE,
    bounded by the expansion horizon / hard cap so an open-ended rule cannot
    hang the import.
    """
    rules = rruleset()
    has_rule = False
    for vrecur in getattr(component, "rrules", None) or []:
        rule_text = vrecur.to_ical().decode("utf-8")
        rules.rrule(rrulestr(rule_text, dtstart=dtstart))
        has_rule = True

    rdates = [_as_local_naive(value) for value in _prop_dts(component, "RDATE")]
    if not has_rule and not any(value is not None for value in rdates):
        return [dtstart]

    for value in rdates:
        if value is not None:
            rules.rdate(value)
    for value in _prop_dts(component, "EXDATE"):
        excluded = _as_local_naive(value)
        if excluded is not None:
            rules.exdate(excluded)

    horizon = dtstart + _EXPANSION_HORIZON
    expanded: list[datetime] = []
    for occurrence in rules:
        if occurrence > horizon or len(expanded) >= _MAX_EXPANSIONS:
            break
        expanded.append(occurrence)
    return expanded or [dtstart]


def _occurrence_id(
    uid: str, title: str, start: datetime, expanded_count: int
) -> str:
    """Stable per-occurrence dedupe key.

    A single event keeps the calendar UID; a recurrence expands the UID with
    the concrete date so each lecture is its own row while staying stable
    across re-imports of the same file.
    """
    base = uid or f"{title}@{start.date().isoformat()}"
    if expanded_count <= 1:
        return base
    return f"{base}:{start.isoformat()}"


def parse_ics(path: str | Path) -> list[Occurrence]:
    """Parse an .ics file into Occurrences (see :func:`parse_ics_with_stats`)."""
    occurrences, _stats = parse_ics_with_stats(path)
    return occurrences


def parse_ics_with_stats(path: str | Path) -> tuple[list[Occurrence], dict]:
    """Parse an .ics file into Occurrences plus a parse-statistics dict.

    Stats: ``calendar_name`` (X-WR-CALNAME), ``total`` VEVENTs seen,
    ``skipped_missing_dtstart`` and ``skipped_duplicate_uid`` counts.
    """
    file_path = Path(path)
    try:
        raw = file_path.read_bytes()
    except OSError as exc:
        raise IcsParseError(f"cannot read ics file {file_path}: {exc}") from exc
    try:
        calendar = Calendar.from_ical(raw)
    except Exception as exc:  # icalendar raises assorted ValueError subclasses
        raise IcsParseError(f"not a parseable iCalendar file: {exc}") from exc

    calendar_name = ""
    name_prop = calendar.get("X-WR-CALNAME")
    if name_prop is not None:
        calendar_name = str(name_prop)

    stats = {
        "calendar_name": calendar_name,
        "total": 0,
        "skipped_missing_dtstart": 0,
        "skipped_duplicate_uid": 0,
    }
    occurrences: list[Occurrence] = []
    seen_ids: set[str] = set()

    # walk("VEVENT") yields only VEVENTs — VALARM sub-components are
    # structurally excluded here, not filtered after the fact.
    for component in calendar.walk("VEVENT"):
        stats["total"] += 1

        start_prop = component.get("DTSTART")
        dtstart = (
            _as_local_naive(start_prop.dt) if start_prop is not None else None
        )
        if dtstart is None:
            # Never invent a 9:00 — an event without DTSTART is unusable.
            stats["skipped_missing_dtstart"] += 1
            continue

        title = str(component.get("SUMMARY", "") or "").strip()
        end_prop = component.get("DTEND")
        dtend = _as_local_naive(end_prop.dt) if end_prop is not None else None

        location_prop = component.get("LOCATION")
        location = str(location_prop).strip() if location_prop is not None else None
        teacher = _extract_teacher(str(component.get("DESCRIPTION", "") or ""))
        uid = str(component.get("UID", "") or "").strip()

        expanded = _expand_recurrences(component, dtstart)
        for occurrence_start in expanded:
            external_id = _occurrence_id(uid, title, occurrence_start, len(expanded))
            if external_id in seen_ids:
                stats["skipped_duplicate_uid"] += 1
                continue
            seen_ids.add(external_id)

            occurrence_end = (
                occurrence_start + (dtend - dtstart) if dtend is not None else None
            )

            # VALARMs never enter evidence: walk() already skipped them, and
            # the evidence dict below only carries provenance fields.
            evidence: dict = {"uid": external_id}
            if location:
                evidence["location"] = location
            if teacher:
                evidence["teacher"] = teacher
            if calendar_name:
                evidence["calendar_name"] = calendar_name

            occurrences.append(
                Occurrence(
                    source="timetable",
                    external_id=external_id,
                    title=title,
                    start_at=occurrence_start.isoformat(),
                    end_at=(
                        occurrence_end.isoformat()
                        if occurrence_end is not None
                        else None
                    ),
                    kind="class",
                    location=location,
                    evidence=evidence,
                )
            )

    occurrences.sort(key=lambda occurrence: occurrence.start_at)
    return occurrences, stats
