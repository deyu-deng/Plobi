"""§8.2 narrow hard route: two frozen L1 utterances → ``plobi_secretary_ask``.

WP-BE-13 / ARCH-RULINGS 2026-09-08 裁定 21.1.

The 2026-09-08 live-window acceptance proved SOUL soft routing is not enough:
sentence ① (「明天的日常安排是什么」) produced **zero** tool calls and the L1
secretary invented a schedule from memory (and falsely announced chatlog was
down).  Sentence ② did reach the tool, so the tool itself is fine — only the
routing decision is unreliable.  This module pins the tool *entry point* for
exactly those two utterances.

Scope discipline (裁定 21.1, and the task's 禁止 list):

* The match table holds the two frozen utterances **verbatim**.  The only
  accepted variants are leading/trailing whitespace and a single trailing
  full-width ``？`` or half-width ``?`` question mark.
* There is **no keyword table**.  「明天几点开会？」 must NOT match — anything
  else that merely contains 明天 falls through to the model untouched, and no
  other user utterance is ever skipped/answered here.
* Which L2 actually runs stays inside the tool (name-list routing / S2 spawn).
  No agent id and no business name other than ``agenda``'s intent enum is
  written down here.
* Anti-fabrication: the rewrite instructs the model to answer only from the
  tool JSON, and to say merely "采集不通" when ``ok=false`` / ``dead=true``
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

# Frozen utterances (§8.2) → ``plobi_secretary_ask`` intent.
# Keys are normalized forms (no surrounding whitespace, no question mark).
FROZEN_UTTERANCES: Dict[str, str] = {
    "明天的日常安排是什么": "refresh_agenda",
    "根据明天的日程写一段早报": "write_briefing",
}

# ``plobi_secretary_ask`` owns the source of truth for its intent enum; this
# tuple only documents what this module is allowed to emit.
SUPPORTED_INTENTS = ("refresh_agenda", "write_briefing")

_REWRITE_TEMPLATE = (
    "{original}\n"
    "\n"
    "[§8.2 硬路由 · 本回合强制] 上面这句话命中总秘书冻结话术，必须严格按序执行：\n"
    "1. 第一动作只能是调用工具 plobi_secretary_ask，参数 intent=\"{intent}\"、"
    "user_text=\"{original}\"。不要先写 memory，不要用 session_search / terminal / "
    "clarify / kanban dispatch 顶替，不要凭已有印象直接回答。\n"
    "2. 拿到工具返回的 JSON 后再作答，终答只能基于该 JSON 的事实。\n"
    "3. 若 JSON 中 ok=false 或 dead=true，只如实说明「采集不通，暂时拿不到明天日程」，"
    "禁止编造任何日程条目，也禁止断言具体组件故障（例如不得说 chatlog 挂了）。\n"
    "4. 交给哪个 L2 由工具内部名单决定，不要自行拼 agent id。"
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
    """Return the ``plobi_secretary_ask`` intent for ``text``, else ``None``.

    ``None`` means "not one of the two frozen utterances" — the caller must let
    the turn reach the model untouched.
    """
    if not isinstance(text, str):
        return None
    normalized = normalize_utterance(text)
    if not normalized:
        return None
    return FROZEN_UTTERANCES.get(normalized)


def build_rewrite(original: str, intent: str) -> str:
    """Build the rewritten user turn that forces the tool call.

    The user's original words are kept verbatim at the top of the rewrite so
    the transcript and the tool's ``user_text`` argument both stay faithful.
    """
    if intent not in SUPPORTED_INTENTS:
        raise ValueError(f"unsupported intent: {intent!r}")
    return _REWRITE_TEMPLATE.format(original=original.strip(), intent=intent)


def _master_mode_enabled() -> bool:
    """True when the L1 secretary tools are actually available.

    Reuses the very gate the ``plobi_secretary_ask`` tool itself is registered
    with (``check_plobi_master_mode``): the active profile must enable the
    ``plobi_north_star`` toolset and we must not be inside a dispatcher-spawned
    worker.  Rewriting a turn whose agent cannot see the tool would be worse
    than not rewriting at all, so the hard route is a strict no-op otherwise.
    """
    try:
        from . import master_tools as MT
    except Exception:  # pragma: no cover - standalone import (docs/tests)
        logger.debug("hard route: master_tools unavailable; gate off")
        return False
    try:
        return bool(MT.check_plobi_master_mode())
    except Exception:
        logger.debug("hard route: master-mode gate failed closed", exc_info=True)
        return False


def on_pre_prompt_submit(
    text: Any = None,
    session_id: Any = None,
    **kwargs: Any,
) -> Optional[dict]:
    """``pre_prompt_submit`` hook: pin §8.2's two utterances to the tool.

    Fires once per user turn at the prompt-dispatch boundary (desktop and
    dashboard both reach the agent through ``tui_gateway``'s ``prompt.submit``
    RPC).  Returns ``{"action": "rewrite", "text": ...}`` on a hit and
    ``None`` on a miss so every other utterance is untouched.
    """
    del kwargs  # hook contract ships extra fields; this route needs none.
    if not isinstance(text, str) or not text.strip():
        return None
    intent = match_frozen_utterance(text)
    if intent is None:
        return None
    if not _master_mode_enabled():
        logger.debug(
            "§8.2 hard route: matched %r but secretary tool is not served here; "
            "falling through to the model (session=%s)",
            intent,
            session_id,
        )
        return None
    logger.info(
        "§8.2 hard route: pinning utterance to plobi_secretary_ask "
        "(intent=%s session=%s)",
        intent,
        session_id,
    )
    return {"action": "rewrite", "text": build_rewrite(text, intent)}
