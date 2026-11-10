"""按域召回与汇报纪律（裁定 215 #34／#35 的落点）。

DeepSeek Super-agent 的「Thread 订阅 + 链式召回」在 Plobi 的递归模型里要改一层：
**持全局记忆的不是 L1，是每个 L2**（``DECISIONS.md`` §4 #34 已答）。L1 是纯调度，
``l1_soul_template.md`` 本来就禁 ``session_search``；L3 无长期记忆。

所以这里不新建检索件：``plobi_state.SessionDB.search_messages`` 原生支持
``agent_id`` 限定域（``plobi_state.py:4849-4860``），FTS5 / trigram / short-CJK
回落都在那一处。本模块只做三件事：把「必须带 agent_id」钉成不可绕过的形状、
把一张 Ticket 的关联键串成召回链、把汇报纪律写成一处可引用的常量。

**不改 ``plobi_state.py``**：它此刻在 ``WP-SAY-ACTS`` 在派族里。
"""

from __future__ import annotations

from typing import Any, Optional

#: 裁定 215 第 3 条的人优先说话纪律。给灵魂模板与汇报生成处引用，不各写一份。
REPORTING_RULES = (
    "只在「人必须行动」或「人现在能用上这个结果」时开口；纯进度、纯状态、全绿无事一律不报。",
    "一条汇报 ≤100 字，只讲结论与要人做的那一个动作；一次只问一个具体问题。",
    "不解释调度细节、不列工具、不报内部编号；派工留痕照旧，不因这一节省掉。",
)


class DomainRecallError(ValueError):
    """没带身份就要查全局记忆 —— #34 明令禁止。"""


def recall_for_agent(
    db: Any,
    agent_id: str,
    query: str,
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """按域召回：一次查询只能落在**一个** L2 的那一摊。

    ``db`` 由调用方注入（``plobi_state.SessionDB`` 或测试替身），本模块不建连接、
    不碰单库 schema。``agent_id=""``（L1／无身份）直接抛错，不静默退化成全库搜。
    """
    agent = str(agent_id or "").strip()
    if not agent:
        raise DomainRecallError("L1 是纯调度、不持全局记忆；按域召回必须带名册身份")
    text = str(query or "").strip()
    if not text:
        return []
    rows = db.search_messages(
        text,
        agent_id=agent,
        limit=max(1, int(limit)),
        sort="newest",
    )
    return list(rows or [])


def recall_chain_for_ticket(
    db: Any,
    ticket: Any,
    query: str,
    *,
    limit: int = 5,
) -> tuple[list[dict[str, Any]], list[str]]:
    """链式召回：票的关联键 → 该票所属域 → 一组命中。

    返回 ``(hits, chain)``；``chain`` 是可给人看的召回路径（哪张票、哪几个键、
    限定在哪个域），不是第二份检索结果。票没有身份时不猜，交给
    :func:`recall_for_agent` 抛错。
    """
    refs = dict(getattr(ticket, "refs", {}) or {})
    keys = [
        f"{name}={refs[name]}"
        for name in ("session_ref", "job", "reply_to", "delivery_id", "external_session")
        if refs.get(name)
    ]
    enriched = " ".join([str(query or "").strip()] + keys).strip()
    hits = recall_for_agent(db, getattr(ticket, "parent_agent", ""), enriched, limit=limit)
    chain = [f"ticket={getattr(ticket, 'id', '')}", *keys,
             f"agent_id={getattr(ticket, 'parent_agent', '')}"]
    return hits, chain


def reporting_rules_text() -> str:
    """给灵魂模板／提示拼装用的一句话版本；真源只有上面那份常量。"""
    return "\n".join(f"- {rule}" for rule in REPORTING_RULES)


def should_speak(*, needs_human_action: bool, usable_now: bool) -> bool:
    """人优先：只有「人得动手」或「人现在用得上」才值得开口。"""
    return bool(needs_human_action or usable_now)


def within_report_budget(text: str, *, limit: int = 100) -> bool:
    return len(str(text or "").strip()) <= max(1, int(limit))


__all__ = [
    "DomainRecallError",
    "REPORTING_RULES",
    "recall_chain_for_ticket",
    "recall_for_agent",
    "reporting_rules_text",
    "should_speak",
    "within_report_budget",
]
