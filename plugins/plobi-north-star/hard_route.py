"""§8.2 narrow hard route: two frozen L1 utterances → the one mouth that exists.

WP-BE-13 / ARCH-RULINGS 2026-09-08 裁定 21.1 — 裁定 183 换嘴.

The 2026-09-08 live-window acceptance proved SOUL soft routing is not enough:
sentence ① (「明天的日常安排是什么」) produced **zero** tool calls and the L1
secretary invented a schedule from memory (and falsely announced chatlog was
down).  Sentence ② did reach the tool, so the tool itself is fine — only the
routing decision is unreliable.  This module pins the *entry point* for
exactly those two utterances.

**嘴换了，窄硬路由没换**（裁定 183）：那 7 枚带 intent/action 枚举的嘴
（``plobi_secretary_ask`` 等）全删了，Agent 之间只剩两枚原子 ``say`` / ``ask``
（用户 10-19 原话：「问话和派工或者别的不都是一件事情吗？就是一个 Agent 给另一个
Agent 发了一段提示词吗？」）。所以这里钉的不再是某把刀的名字，而是那个**动作**：
把这一句原话用 ``ask`` 交给日程秘书，等它把话说完再作答。下表两个值因此只是
**路由标签**（供 hook 与测试引用），不再是 intent——7 个 intent 随裁定 183 消亡。

Scope discipline (裁定 21.1, and the task's 禁止 list):

* The match table holds the two frozen utterances **verbatim**.  The only
  accepted variants are leading/trailing whitespace and a single trailing
  full-width ``？`` or half-width ``?`` question mark.
* There is **no keyword table**.  「明天几点开会？」 must NOT match — anything
  else that merely contains 明天 falls through to the model untouched, and no
  other user utterance is ever skipped/answered here.
* Which L2 actually runs stays inside the mouth (name-list routing).  No agent
  id and no business name is written down here.
* Anti-fabrication: the rewrite instructs the model to answer only from the
  answer it gets back, and to say merely "采集不通" when the turn fails
  instead of announcing a concrete component failure from memory.

The hook returns ``{"action": "rewrite", "text": ...}`` — the same shape the
existing ``pre_gateway_dispatch`` rewrite precedent uses (see
``plugins/plobi-north-star/__init__.py::_on_pre_gateway_dispatch``).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Trailing question marks accepted as a variant of the frozen utterance.
# U+FF1F FULLWIDTH QUESTION MARK and the ASCII one only — deliberately narrow.
_TRAILING_QUESTION_MARKS = ("?", "\uff1f")

# Frozen utterances (§8.2) → route label. Keys are normalized forms (no
# surrounding whitespace, no question mark). The values are labels for the hook
# and its tests — NOT tool intents (those seven died with 裁定 183).
FROZEN_UTTERANCES: Dict[str, str] = {
    "明天的日常安排是什么": "refresh_agenda",
    "根据明天的日程写一段早报": "write_briefing",
}

# Labels this module is allowed to emit.
SUPPORTED_LABELS = ("refresh_agenda", "write_briefing")

_REWRITE_TEMPLATE = (
    "{original}\n"
    "\n"
    "[§8.2 硬路由 · 本回合强制] 上面这句话命中总秘书冻结话术，必须严格按序执行：\n"
    "1. 第一动作只能是调用工具 ask，把这句话原话交给日程秘书："
    "to 填日程秘书那一行、parts 就一段原话（{original}）。"
    "不要先写 memory，不要用 session_search / terminal / clarify 顶替，"
    "不要凭已有印象直接回答。\n"
    "2. 拿到回答之后再作答，终答只能基于那个回答的事实。\n"
    "3. 若那一问没通（问不到、超时、它那边起不来），只如实说明"
    "「采集不通，暂时拿不到明天日程」，禁止编造任何日程条目，"
    "也禁止断言具体组件故障（例如不得说 chatlog 挂了）。\n"
    "4. 交给哪个 L2 由 ask 内部的名册解析决定，不要自行拼 agent id。"
)


def normalize_utterance(text: str) -> str:
    """Return the lookup form of ``text``.

    Accepted normalization (and nothing else):

    * leading/trailing whitespace removed (incl. the ideographic space U+3000);
    * a single trailing ``?`` / ``？`` removed, then re-stripped.

    Everything else is preserved byte for byte, so 「明天几点开会？」 normalizes
    to 「明天几点开会」 and misses the table, while 「明天的日常安排是什么？」 hits it.
    """
    candidate = (text or "").strip()
    for mark in _TRAILING_QUESTION_MARKS:
        if candidate.endswith(mark):
            candidate = candidate[: -len(mark)].strip()
            break
    return candidate


def match_frozen_utterance(text: Any) -> Optional[str]:
    """Return the route label for ``text``, else ``None``.

    ``None`` means "not one of the two frozen utterances" — the caller must let
    the turn reach the model untouched.
    """
    if not isinstance(text, str):
        return None
    normalized = normalize_utterance(text)
    if not normalized:
        return None
    return FROZEN_UTTERANCES.get(normalized)


def build_rewrite(original: str, label: str) -> str:
    """Build the rewritten user turn that forces the ask.

    The user's original words are kept verbatim at the top of the rewrite so
    the transcript and the mouth's ``parts`` argument both stay faithful.
    """
    if label not in SUPPORTED_LABELS:
        raise ValueError(f"unsupported route label: {label!r}")
    return _REWRITE_TEMPLATE.format(original=original.strip())


def _channel_available() -> bool:
    """True when the two mouths could actually be served this turn.

    Reuses the very gate ``say`` / ``ask`` are registered with
    (``channel.check_channel_available``): the roster must hold at least one
    agent that is not archived.  Rewriting a turn whose agent cannot see the
    mouths would be worse than not rewriting at all, so the hard route is a
    strict no-op otherwise.
    """
    try:
        from . import channel as CH
    except Exception:  # pragma: no cover - standalone import (docs/tests)
        logger.debug("hard route: channel unavailable; gate off")
        return False
    try:
        return bool(CH.check_channel_available())
    except Exception:
        logger.debug("hard route: channel gate failed closed", exc_info=True)
        return False


def on_pre_prompt_submit(
    text: Any = None,
    session_id: Any = None,
    **kwargs: Any,
) -> Optional[dict]:
    """``pre_prompt_submit`` hook: pin §8.2's two utterances to the ask.

    Fires once per user turn at the prompt-dispatch boundary (desktop and
    dashboard both reach the agent through ``tui_gateway``'s ``prompt.submit``
    RPC).  Returns ``{"action": "rewrite", "text": ...}`` on a hit and
    ``None`` on a miss so every other utterance is untouched.
    """
    del kwargs  # hook contract ships extra fields; this route needs none.
    if not isinstance(text, str) or not text.strip():
        return None
    label = match_frozen_utterance(text)
    if label is None:
        return None
    if not _channel_available():
        logger.debug(
            "§8.2 hard route: matched %r but the mouths are not served here; "
            "falling through to the model (session=%s)",
            label,
            session_id,
        )
        return None
    logger.info(
        "§8.2 hard route: pinning utterance to ask the agenda secretary "
        "(label=%s session=%s)",
        label,
        session_id,
    )
    return {"action": "rewrite", "text": build_rewrite(text, label)}
