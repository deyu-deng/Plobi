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

#: 注入文本的最长长度——指挥那段硬指令 + 派活提醒一段，不把整张清单塞进上下文。
_MAX_CONTEXT = 700

#: 派活提醒单独一段的上限：它是事后结算，只说一件事，不铺开讲四类名手的分工。
_MAX_REMINDER = 320

#: 手上真有派活口的两种角色：指挥（L1）与首席（L2）。名手与普通子智能体没有
#: 派活口，给它们注这句话只是噪声，所以按名册角色筛，不按模型自报。
_DISPATCH_ROLES = frozenset({"conductor", "principal"})


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


def _may_dispatch(name: str) -> bool:
    """这人手上有没有派活口：名册里是指挥或首席。读不出一律当「没有」。"""
    if not name:
        return False
    try:
        from plobi.agents.registry import load_registry

        entry = load_registry().get(name)
        role = str(getattr(entry, "role", "") or "") if entry else ""
        return role in _DISPATCH_ROLES
    except Exception as exc:  # noqa: BLE001
        logger.debug("forced report: dispatch identity lookup failed: %s", exc)
        return False


def _call_name(call: Any) -> str:
    """一枚工具调用的名字；两种消息格式都认（OpenAI 的 function / Anthropic 的 tool_use）。"""
    if not isinstance(call, dict):
        return ""
    fn = call.get("function")
    if isinstance(fn, dict) and fn.get("name"):
        return str(fn["name"])
    if call.get("type") == "tool_use" and call.get("name"):
        return str(call["name"])
    return ""


def _last_round_tool_names(history: Any) -> list[str]:
    """上一轮碰过哪几件工具；判不出来返回空表。

    两处边界都得算准：**末尾那条用户消息属于本轮**（hook 在本轮 user 消息入列
    之后才触发），先跳过；再往前收助手消息，走到上一条用户消息为止——一个长回合
    里工具会调好几遍，分散在多条 assistant 消息上，所以不能只看最后一条。
    """
    names: list[str] = []
    at_entry = True  # 还没收到任何助手消息：这一段只是本轮的入口消息
    for msg in reversed(list(history or [])):
        if not isinstance(msg, dict):
            break
        role = str(msg.get("role") or "")
        if role == "user":
            if at_entry:
                continue
            break
        if role != "assistant":
            continue
        at_entry = False
        calls = msg.get("tool_calls")
        if isinstance(calls, list):
            for call in calls:
                nm = _call_name(call)
                if nm:
                    names.append(nm)
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                nm = _call_name(block)
                if nm:
                    names.append(nm)
    return names


#: 结算提醒的正文。这是**事后**的一句，不是事前拦一道：动手不算错，只是那活本来
#: 该派出去；语气对齐派活四要素里「派完回一句就算交付」那条。
_DISPATCH_ADVICE = (
    "[上一轮结算 · 系统按工具名算好]"
    "上一轮你自己动手了（{doing}），这一轮没派过活。"
    "同类活下一次先派名手（delegate_task 的 virtuoso 填 file / browser / software / "
    "computer）；名手够不着才开客席。派完回一句「已经派下去了，结果回来我接着看」"
    "就算是交付。"
)


def dispatch_reminder(session_id: str = "", history: Any = None) -> str:
    """该派却没派：上一轮伸了手、又没有一枚派活工具，就回一句人话；否则空串。

    两条边界：**只对能派活的人**（指挥/首席，按名册角色判，不看模型自报）；
    **只读不改**——它不动历史、不改票、不落盘，判不出来就返回空串。
    """
    try:
        me = _current_name(session_id)
        if not _may_dispatch(me):
            return ""
        from plobi.delegation.brief import OTHER_HANDS, missed_dispatch

        labels = missed_dispatch(_last_round_tool_names(history))
        if not labels:
            return ""
        human = {label: text for label, _, text in OTHER_HANDS}
        doing = "、".join(
            text for text in (human.get(label) for label in labels) if text
        )
        if not doing:
            return ""
        return _DISPATCH_ADVICE.format(doing=doing)
    except Exception as exc:  # noqa: BLE001
        logger.debug("forced report: dispatch reminder failed: %s", exc)
        return ""


def build_forced_context(
    session_id: str = "",
    *,
    lines: Optional[list[str]] = None,
    reminder: Optional[str] = None,
) -> str:
    """给 ``pre_llm_call`` 的注入串；两段都没东西就是空串（不占上下文）。

    ``lines`` 是判据结果的注入口（默认 None ⇒ 自己读账算）。它让「关掉注入」
    成为可常驻测试的对偶：喂空列表就必须不注入，喂若干条就逐字进上下文。
    ``reminder`` 是派活结算那一段（见 :func:`dispatch_reminder`）：与票务那段
    各算各的——没有票要喊的时候，派活该提醒照样提醒。
    """
    parts: list[str] = []
    if lines is None:
        lines = pending_report_lines(session_id)
    if lines:
        one = lines[0]
        rest = len(lines) - 1
        text = (
            "[本轮待汇报 · 系统已按回执算好]"
            f"有一件事需要你这轮向用户开口：{one}"
        )
        if rest:
            text += f" 另有 {rest} 件，这一轮先只说这一件，其余留到下一轮。"
        text += " 用 say to=用户 念出上面那句，一次一件；不必再读 tickets 找它。"
        parts.append(text)
    if reminder:
        parts.append(str(reminder)[:_MAX_REMINDER])
    if not parts:
        return ""
    return "\n".join(parts)[:_MAX_CONTEXT]


def on_pre_llm_call(**kwargs: Any) -> dict[str, str]:
    """``pre_llm_call`` 回调：只对指挥注入，返回 ``context`` 给注入通道。

    回调签名兼容 turn_context 传来的全部关键字：``session_id`` 定人，
    ``conversation_history`` 只用来数上一轮伸过哪几只手（不改它）。
    """
    session_id = str(kwargs.get("session_id") or "")
    history = kwargs.get("conversation_history") or []
    try:
        text = build_forced_context(
            session_id, reminder=dispatch_reminder(session_id, history)
        )
    except Exception as exc:  # noqa: BLE001 - 双保险：hook 永不打断回合
        logger.debug("forced report hook failed: %s", exc)
        return {}
    return {"context": text} if text else {}
