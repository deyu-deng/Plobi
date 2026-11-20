"""回合入口的强制汇报上下文（裁定 232）。

问题：裁定 225 把「该不该开口」算成 ``tickets`` 回包里的 ``next_escalation``，
但要指挥先自己去读 ``tickets`` 才看得见——那是 SOUL 里的提示词纪律，模型
可以选择不读。本模块把判据结果**在回合入口由代码算好**，经 ``pre_llm_call``
注入当轮 user message：指挥不必自己取数就知道这一轮该喊哪一件。

边界（三条，都不越）：

* **只对指挥**：身份取 ``current_agent_name(session_id)`` + 名册 ``role=conductor``，
  读不出一律当「不是」。非指挥返回空串，一个字都不注入。
* **只读、不造嘴**：这里只调 ``due_escalations`` 算结论，不改票、不投递、
  不调用 ``say``；最后由指挥自己用 ``say to=用户`` 说出，出口仍是唯一那一枚。
* **不落盘、失败静默**：返回的字符串是 API-call-time 注入（见
  ``agent/turn_context.py``），不进会话历史；任何读账失败都返回空串，
  绝不因为判据层坏了就打断用户这一轮对话。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: 注入文本的最长长度——一句硬指令 + 一两个待办，不把整张清单塞进上下文。
_MAX_CONTEXT = 400


def _is_conductor_name(name: str) -> bool:
    """复用 channel 的唯一身份闸，不另造判据。"""
    if not name:
        return False
    try:
        from . import channel as CH

        return CH._is_conductor(name)
    except Exception as exc:  # noqa: BLE001
        logger.debug("forced report: conductor lookup failed: %s", exc)
        return False


def _current_name(session_id: str) -> str:
    """复用 channel 的会话级身份解析（名册行级归属，非模型自报）。"""
    try:
        from . import channel as CH

        return CH.current_agent_name(session_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("forced report: identity lookup failed: %s", exc)
        return ""


def pending_report_lines(session_id: str = "", *, limit: int = 3) -> list[str]:
    """这一轮该由指挥喊出的话，已算好的结论列表（候选，一次只念第一件）。

    只认指挥。读账失败 / 身份读不出 ⇒ 空列表（宁可少注一段，也不打断回合、
    不把别人的票漏给不相干的人）。
    """
    try:
        me = _current_name(session_id)
        if not _is_conductor_name(me):
            return []
        from plobi.delegation.report_gate import due_escalations, render_one
        from plobi.delegation.tickets import TicketLedger
        from . import channel as CH

        ledger = TicketLedger()
        # 与 tickets 读口同一可见性：指挥只看自己发起的票（裁 222/225），不扩。
        views = [
            CH._ticket_view(t, ledger)
            for t in ledger.list_tickets(limit=max(limit, 10) * 5)
            if CH._may_read_ticket(t, me)
        ]
        found = due_escalations(views)
        return [render_one(v) for v in found[:limit]]
    except Exception as exc:  # noqa: BLE001
        logger.debug("forced report: pending read failed: %s", exc)
        return []


def build_forced_context(session_id: str = "", *, lines: Optional[list[str]] = None) -> str:
    """给 ``pre_llm_call`` 的注入串；没有待汇报件就是空串（不占上下文）。

    ``lines`` 是判据结果的注入口（默认 None ⇒ 自己读账算）。它让「关掉注入」
    成为可常驻测试的对偶：喂空列表就必须不注入，喂若干条就逐字进上下文。
    """
    if lines is None:
        lines = pending_report_lines(session_id)
    if not lines:
        return ""
    one = lines[0]
    rest = len(lines) - 1
    text = (
        "[本轮待汇报 · 系统已按回执算好]"
        f"有一件事需要你这轮向用户开口：{one}"
    )
    if rest:
        text += f" 另有 {rest} 件，这一轮先只说这一件，其余留到下一轮。"
    text += " 用 say to=用户 念出上面那句，一次一件；不必再读 tickets 找它。"
    return text[:_MAX_CONTEXT]


def on_pre_llm_call(**kwargs: Any) -> dict[str, str]:
    """``pre_llm_call`` 回调：只对指挥注入，返回 ``context`` 给注入通道。

    回调签名兼容 turn_context 传来的全部关键字，只用 ``session_id``。
    """
    session_id = str(kwargs.get("session_id") or "")
    try:
        text = build_forced_context(session_id)
    except Exception as exc:  # noqa: BLE001 - 双保险：hook 永不打断回合
        logger.debug("forced report hook failed: %s", exc)
        return {}
    return {"context": text} if text else {}
