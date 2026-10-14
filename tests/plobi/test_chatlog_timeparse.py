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


# --------------------------------------------------------------------------- #
# 明写的区间 ⇒ 两端都要落；没说的结束时间 ⇒ 空着，不许编（WP-EXTRACT-QUALITY 修的那一类）
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "text,start,end",
    [
        ("明天下午三点到五点开组会", time(15, 0), time(17, 0)),
        ("明天下午三点到17:00开组会", time(15, 0), time(17, 0)),
        ("3:00到5:00值班", time(3, 0), time(5, 0)),
        ("明天下午三点开组会", time(15, 0), None),
        ("上午十点到十二点面谈", time(10, 0), None),
    ],
)
def test_a_stated_range_is_read_both_ways_an_unstated_one_is_not_invented(text, start, end):
    parsed = parse_when(text, now=NOW)

    assert parsed.clock == start
    assert parsed.end_clock == end


def test_the_end_of_a_range_must_not_become_its_start():
    """旧行为：数字钟点那一路先赢，"下午三点到17:00" 整条事件就晚两个小时落库。

    钟点按**在句中出现的位置**排，不按写法排 —— 这是那条 bug 的回归钉。
    """
    parsed = parse_when("明天下午三点到17:00开组会", now=NOW)

    assert parsed.clock == time(15, 0)
    assert parsed.end_clock == time(17, 0)
