"""Deterministic Chinese/English date-time extraction.

Exists so the common cases ("明天下午三点开会") resolve **without** a model
call: cheaper, faster, and it keeps the message text on-device. The model
confirmer is the fallback for what this cannot parse, not the default path.

Returns the date and clock separately: only the caller knows what a missing
clock should mean (a deadline defaults to end-of-day, a meeting to morning).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

_WEEKDAY_CHARS = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}

_RELATIVE_DAYS = {
    "今天": 0, "今晚": 0, "今早": 0, "今日": 0,
    "明天": 1, "明早": 1, "明晚": 1, "明日": 1,
    "后天": 2, "後天": 2,
    "大后天": 3, "大後天": 3,
}

_MERIDIEM_SHIFT = ("下午", "晚上", "傍晚")
_MERIDIEM_MORNING = ("上午", "早上", "凌晨", "早晨")

_ISO_DATE = re.compile(r"(20\d{2})-(\d{1,2})-(\d{1,2})")
_CN_DATE = re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]")
_WEEKDAY = re.compile(r"(?:周|星期|礼拜)([一二三四五六日天])")
_CLOCK = re.compile(r"(\d{1,2})\s*[:：]\s*(\d{2})")
_CN_HOUR = re.compile(r"([0-9]{1,2}|[零一二两三四五六七八九十]{1,3})\s*点\s*(半|[0-9]{1,2}\s*分|[零一二两三四五六七八九十]{1,3}\s*分)?")


@dataclass(frozen=True)
class ParsedWhen:
    day: Optional[date] = None
    clock: Optional[time] = None

    @property
    def has_any(self) -> bool:
        return self.day is not None or self.clock is not None

    def to_datetime(self, *, base: date, default_clock: time) -> datetime:
        return datetime.combine(self.day or base, self.clock or default_clock)


def _cn_number(text: str) -> Optional[int]:
    text = text.strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)

    # Handles 十, 十一, 二十, 二十三 — enough for clock hours.
    if "十" in text:
        left, _, right = text.partition("十")
        tens = _CN_DIGITS.get(left, 1) if left else 1
        ones = _CN_DIGITS.get(right, 0) if right else 0
        return tens * 10 + ones

    total = 0
    for char in text:
        if char not in _CN_DIGITS:
            return None
        total = total * 10 + _CN_DIGITS[char]
    return total or None


def _parse_day(text: str, today: date) -> Optional[date]:
    iso = _ISO_DATE.search(text)
    if iso:
        try:
            return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        except ValueError:
            return None

    cn = _CN_DATE.search(text)
    if cn:
        try:
            candidate = date(today.year, int(cn.group(1)), int(cn.group(2)))
        except ValueError:
            return None
        # A date more than a month behind us almost certainly means next year.
        if (today - candidate).days > 31:
            try:
                candidate = candidate.replace(year=today.year + 1)
            except ValueError:
                return candidate
        return candidate

    for word, offset in _RELATIVE_DAYS.items():
        if word in text:
            return today + timedelta(days=offset)

    weekday = _WEEKDAY.search(text)
    if weekday:
        target = _WEEKDAY_CHARS.get(weekday.group(1))
        if target is None:
            return None
        delta = (target - today.weekday()) % 7
        # "周三" spoken on a Wednesday means today; "下周三" is out of scope here.
        return today + timedelta(days=delta)

    return None


def _parse_clock(text: str) -> Optional[time]:
    hour: Optional[int] = None
    minute = 0

    clock = _CLOCK.search(text)
    if clock:
        hour = int(clock.group(1))
        minute = int(clock.group(2))
    else:
        cn = _CN_HOUR.search(text)
        if cn:
            hour = _cn_number(cn.group(1))
            suffix = (cn.group(2) or "").strip()
            if suffix == "半":
                minute = 30
            elif suffix:
                parsed = _cn_number(suffix.replace("分", "").strip())
                minute = parsed if parsed is not None else 0

    if hour is None:
        if "中午" in text:
            return time(12, 0)
        return None

    if any(word in text for word in _MERIDIEM_SHIFT) and hour < 12:
        hour += 12
    elif "中午" in text and hour < 12:
        hour = 12
    elif any(word in text for word in _MERIDIEM_MORNING) and hour == 12:
        hour = 0

    if not (0 <= hour <= 23) or not (0 <= minute <= 59):
        return None

    return time(hour, minute)


def parse_when(text: str, *, now: Optional[datetime] = None) -> ParsedWhen:
    """Extract whatever date/clock the text states. Never guesses both."""
    reference = now or datetime.now()
    source = text or ""

    return ParsedWhen(day=_parse_day(source, reference.date()), clock=_parse_clock(source))


# Common non-ISO shapes chatlog hands back. Tried in order after ISO.
_SENT_AT_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y年%m月%d日 %H:%M:%S",
    "%Y年%m月%d日",
)

# Epoch values above this are milliseconds, not seconds (a seconds value that
# large would be year ~2286; chatlog sends 13-digit ms timestamps).
_EPOCH_MS_THRESHOLD = 10_000_000_000


def parse_sent_at(raw: str, *, fallback: Optional[datetime] = None) -> Optional[datetime]:
    """Parse a chatlog ``sent_at`` string into a naive local datetime.

    chatlog formats differ between versions, so we deliberately accept several
    shapes: ISO-8601 (with or without ``Z`` / offset), epoch seconds or
    milliseconds, and the common ``%Y-%m-%d %H:%M:%S`` / ``%Y/%m/%d`` forms.

    This is the *only* anchor for relative dates ("明天", "下午"): the moment a
    message was sent, not the moment we happened to sweep it. When the value
    cannot be parsed the caller's ``fallback`` (usually the wall clock) is
    returned — never a silently invented time — and the failure is logged at
    ``debug`` because it is expected for some chatlog builds.
    """
    text = (raw or "").strip()
    if text:
        stripped = text.lstrip("-")
        if stripped.isdigit():
            try:
                value = int(text)
                if abs(value) >= _EPOCH_MS_THRESHOLD:
                    value /= 1000.0
                return datetime.fromtimestamp(value)
            except (ValueError, OSError, OverflowError):
                pass

        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone().replace(tzinfo=None)
            return parsed
        except ValueError:
            pass

        for fmt in _SENT_AT_FORMATS:
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue

    logger.debug("timeparse: could not parse sent_at %r; using fallback", raw)
    return fallback
