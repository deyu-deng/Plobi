"""Rule filter: it is the privacy + cost gate, so both directions matter."""

from __future__ import annotations

import pytest

from plobi.agenda import rules


@pytest.mark.parametrize(
    "text,category",
    [
        ("明天下午三点组会改到四点", "meeting"),
        ("明天下午3点开会", "meeting"),
        ("周三 14:00 答辩，别迟到", "meeting"),
        ("报名截止到 9月10日 晚上12点", "ddl"),
        ("明天的实验课停课", "class"),
        ("这个表格务必在今天下午提交", "ddl"),
    ],
)
def test_schedule_messages_hit(text, category):
    hit = rules.match(text)
    assert hit is not None
    assert hit.category == category
    assert hit.matched_times


@pytest.mark.parametrize(
    "text",
    [
        "好的",
        "收到收到",
        "这个会议室好冷",          # keyword, no time
        "已经三点了啊",            # Chinese-numeral time, no topical keyword
        "已经3点了啊",             # Arabic-numeral time, no topical keyword
        "哈哈哈哈",
        "",
    ],
)
def test_chatter_is_filtered_out(text):
    assert rules.match(text) is None


def test_change_messages_are_flagged():
    hit = rules.match("明天的组会改到下午三点")
    assert hit is not None
    assert hit.is_change is True

    plain = rules.match("周五下午两点开会")
    assert plain is not None
    assert plain.is_change is False


def test_english_deadline_is_detected():
    hit = rules.match("Please submit before 2026-09-01, hard deadline")
    assert hit is not None
    assert hit.category == "ddl"


def test_snippet_is_clipped_and_whitespace_collapsed():
    long_text = "明天下午三点开会 " + ("详情 " * 300)
    clipped = rules.snippet(long_text)
    assert len(clipped) <= rules.MAX_SNIPPET_CHARS
    assert "  " not in clipped

    short = rules.snippet("  明天  开会  ")
    assert short == "明天 开会"


def test_score_reflects_signal_strength():
    strong = rules.match("周三下午 15:00 组会改期到 16:00，截止今天回复")
    weak = rules.match("明天开会")
    assert strong is not None and weak is not None
    assert strong.score > weak.score


def test_rules_module_never_imports_a_model_client():
    source = rules.__file__ or ""
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    for forbidden in ("openai", "anthropic", "requests.post", "httpx", "completion"):
        assert forbidden not in text, f"rules.py must stay local-only, found {forbidden!r}"
