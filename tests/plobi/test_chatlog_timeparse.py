"""Deterministic time extraction — the reason common cases need no model."""

from __future__ import annotations

from datetime import date, datetime, time

import pytest

from plobi.collectors.chatlog.timeparse import parse_sent_at, parse_when

# A Tuesday, so weekday maths is observable.
NOW = datetime(2026, 8, 25, 10, 0)


@pytest.mark.parametrize(
    "text,expected_day",
    [
        ("今天下午三点开会", date(2026, 8, 25)),
        ("明天下午三点开会", date(2026, 8, 26)),
        ("后天交材料", date(2026, 8, 27)),
        ("2026-09-01 报到", date(2026, 9, 1)),
        ("9月1日开学", date(2026, 9, 1)),
    ],
)
def test_day_extraction(text, expected_day):
    assert parse_when(text, now=NOW).day == expected_day


def test_weekday_resolves_forward_and_includes_today():
    # Tuesday -> 周三 is tomorrow
    assert parse_when("周三下午答辩", now=NOW).day == date(2026, 8, 26)
    # 周二 spoken on a Tuesday means today, not next week
    assert parse_when("周二开会", now=NOW).day == date(2026, 8, 25)
    # 周一 wraps to next week
    assert parse_when("周一交表", now=NOW).day == date(2026, 8, 31)


def test_past_month_day_rolls_to_next_year():
    # Speaking of "1月5日" in late August means next January.
    assert parse_when("1月5日考试", now=NOW).day == date(2027, 1, 5)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("下午三点", time(15, 0)),
        ("上午九点", time(9, 0)),
        ("晚上八点半", time(20, 30)),
        ("14:30 开会", time(14, 30)),
        ("三点半", time(3, 30)),
        ("中午集合", time(12, 0)),
        ("十点十五分", time(10, 15)),
        ("两点", time(2, 0)),
    ],
)
def test_clock_extraction(text, expected):
    assert parse_when(text, now=NOW).clock == expected


def test_no_time_information_yields_nothing():
    parsed = parse_when("这个方案我觉得可以", now=NOW)
    assert parsed.day is None
    assert parsed.clock is None
    assert parsed.has_any is False


def test_invalid_clock_is_rejected():
    assert parse_when("99点开会", now=NOW).clock is None


def test_to_datetime_uses_default_clock_when_missing():
    parsed = parse_when("明天交材料", now=NOW)
    assert parsed.clock is None

    resolved = parsed.to_datetime(base=parsed.day, default_clock=time(23, 59))
    assert resolved == datetime(2026, 8, 26, 23, 59)


def test_iso_date_with_clock():
    parsed = parse_when("2026-09-01 14:00 报到", now=NOW)
    assert parsed.day == date(2026, 9, 1)
    assert parsed.clock == time(14, 0)


# --- sent_at parsing: the anchor for every relative date ---------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2026-09-10 23:00:00", datetime(2026, 9, 10, 23, 0)),
        ("2026-09-10 23:00", datetime(2026, 9, 10, 23, 0)),
        ("2026-09-10T23:00:00", datetime(2026, 9, 10, 23, 0)),
        ("2026-09-10T23:00", datetime(2026, 9, 10, 23, 0)),
        ("2026/09/10 23:00", datetime(2026, 9, 10, 23, 0)),
        ("2026-09-10", datetime(2026, 9, 10, 0, 0)),
    ],
)
def test_parse_sent_at_accepts_the_common_chatlog_formats(raw, expected):
    assert parse_sent_at(raw) == expected


def test_parse_sent_at_reads_epoch_seconds_and_milliseconds():
    moment = datetime(2026, 9, 10, 23, 0)
    assert parse_sent_at(str(int(moment.timestamp()))) == moment
    assert parse_sent_at(str(int(moment.timestamp() * 1000))) == moment


def test_parse_sent_at_falls_back_only_when_unparseable():
    fallback = datetime(2026, 9, 10, 23, 0)
    assert parse_sent_at("", fallback=fallback) == fallback
    assert parse_sent_at("not a timestamp", fallback=fallback) == fallback
    # No fallback -> honestly nothing, never a guess.
    assert parse_sent_at("", fallback=None) is None
    assert parse_sent_at("??", fallback=None) is None
