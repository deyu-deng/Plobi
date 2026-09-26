"""Local rule filter for schedule-bearing messages.

Runs entirely on-device and MUST NOT call a model: it is the privacy gate that
decides which single message snippet is allowed to leave the machine for model
confirmation (docs/adr/0010-collection-whitelist-privacy-boundary.md) and the
cost gate that keeps per-message LLM calls down (ADR-0005, ADR-0011).

The filter is intentionally lenient — recall matters more than precision here,
because a downstream model confirms every hit and the user confirms every
resulting change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# Keyword families. Order matters only for `category` resolution below.
CHANGE_WORDS = (
    "改期", "改到", "推迟", "延期", "提前", "顺延", "取消", "调课", "调到", "换到",
    "挪到", "改成", "变更", "改时间",
)
DEADLINE_WORDS = (
    "截止", "deadline", "ddl", "最晚", "before", "交表", "提交", "上交",
    "报名截止", "务必在",
)
MEETING_WORDS = (
    "开会", "会议", "例会", "答辩", "面试", "组会", "宣讲", "培训",
    "聚餐", "见面", "约", "meeting", "班会",
)
CLASS_WORDS = ("上课", "课程", "实验课", "补课", "停课", "课表")

# Time expressions: weekday, explicit clock, relative day, date.
# Hours appear in both Arabic and Chinese numerals ("3点" / "三点"); missing the
# Chinese form here would silently drop messages that timeparse can resolve.
TIME_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(周|礼拜|星期)[一二三四五六日天1-7]"),
    re.compile(r"\d{1,2}\s*[:：]\s*\d{2}"),
    re.compile(r"(?:\d{1,2}|[零一二两三四五六七八九十]{1,3})\s*点(半|\d{1,2}\s*分?|[零一二两三四五六七八九十]{1,3}\s*分)?"),
    re.compile(r"(今天|明天|后天|大后天|今晚|明早|明晚|今早)"),
    re.compile(r"\d{1,2}\s*月\s*\d{1,2}\s*[日号]"),
    re.compile(r"\d{4}-\d{1,2}-\d{1,2}"),
    re.compile(r"(上午|下午|中午|晚上|早上|凌晨)"),
)

# Messages this short are almost always chatter ("好的", "收到").
MIN_LENGTH = 4
# Snippet cap: only this much text may be handed to a model (privacy boundary).
MAX_SNIPPET_CHARS = 400


@dataclass
class RuleHit:
    """A message that earned the right to a model confirmation call."""

    matched_keywords: list[str] = field(default_factory=list)
    matched_times: list[str] = field(default_factory=list)
    category: str = "task"
    is_change: bool = False

    @property
    def score(self) -> int:
        return len(self.matched_keywords) + len(self.matched_times)


def _find_keywords(text_lower: str, words: tuple[str, ...]) -> list[str]:
    return [w for w in words if w in text_lower]


def snippet(text: str, limit: int = MAX_SNIPPET_CHARS) -> str:
    """Clip a message to the slice that may leave the machine."""
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"


def match(text: str) -> Optional[RuleHit]:
    """Return a hit when the message plausibly carries schedule information.

    Requires **a time expression plus a topical keyword**. A bare time
    ("三点了") or a bare keyword ("会议室好冷") is not enough — that pairing is
    what keeps the model-call volume and the privacy exposure low.
    """
    raw = (text or "").strip()
    if len(raw) < MIN_LENGTH:
        return None

    lowered = raw.lower()

    times: list[str] = []
    for pattern in TIME_PATTERNS:
        times.extend(m.group(0) for m in pattern.finditer(raw))
    if not times:
        return None

    change = _find_keywords(lowered, CHANGE_WORDS)
    deadline = _find_keywords(lowered, DEADLINE_WORDS)
    meeting = _find_keywords(lowered, MEETING_WORDS)
    klass = _find_keywords(lowered, CLASS_WORDS)

    keywords = [*change, *deadline, *meeting, *klass]
    if not keywords:
        return None

    if klass:
        category = "class"
    elif deadline:
        category = "ddl"
    elif meeting:
        category = "meeting"
    else:
        category = "task"

    return RuleHit(
        matched_keywords=keywords,
        matched_times=sorted(set(times)),
        category=category,
        is_change=bool(change),
    )
