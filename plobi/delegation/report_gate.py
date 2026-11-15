"""「到点主动汇报」的判据（裁定 225）。

裁定 199 立的是**会移动的门槛**，当时只接在「记忆写入要不要先问」上
（``plobi/mind/writer.py::_gate_refusals``）。本模块把**同一套形状**接到
「指挥该不该向用户开口」上：

* **形状复用**：``plobi.mind.proposal.TrustLedger`` 的可核事件计数、
  ``AUTO_THRESHOLD=8``、驳回归零、永不自动、不许模型自评，一处真源，不抄第二份。
* **账本分开**：本层用 ``PLOBI_HOME/report-trust.json``，与 ``l1-trust.json``
  物理隔离。写入类别由文件路径推（``category_of``），汇报类别是「哪一跳＋哪一域」；
  共用一本会让「批过一次笔记写入」顺带把汇报也放行，那是串账。
* **出厂一律先问**：``report-policy.json`` 的 ``auto_approve`` 缺省 ``False``。
  开它是**用户的动作**，不许一次代码改动或模型判断替他开（裁定 199 总开关原话）。

护栏（裁定 215 #34）：指挥是纯调度、**不持全局记忆**。本层只读票的结构化六态回执
与运行时信任账，不查会话、不读 Mind、不做跨域召回；要问某一摊的实情仍用 ``ask``
找那位首席。所以「记忆越来越完善」在指挥这一侧只落成信任账的计数，不是给它建一份
全局记忆。

消费者只有一个：``plugins/plobi-core/channel.py::handle_tickets``。判据不另开工具、
不在 ``ticket_report`` 里传提示词（旁路状态通道仍不是第三枚嘴，裁定 216/222）。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from plobi.mind.proposal import (
    AUTO_THRESHOLD,
    NEVER_AUTO_CATEGORIES,
    TrustLedger,
)

#: 除 ``progress`` 之外的五种六态都算「这一刻可能需要人」。纯进度一律不报
#: （``recall.py::REPORTING_RULES`` 第 1 条：纯进度、纯状态、全绿无事一律不报）。
NEEDS_HUMAN_STATES = frozenset({
    "waiting", "ready", "blocked", "failed", "dropped",
})

#: 一条汇报 ≤100 字（``REPORTING_RULES`` 第 2 条）。
MAX_ONE_LINE = 100

#: 信任账里的 source 固定为指挥——账本按 (category, source) 分行，别让别的
#: 调用方混进同一行，把「别人攒的信任」算到指挥头上。
TRUST_SOURCE = "conductor"

DEFAULT_AUTO_APPROVE = False
POLICY_FILENAME = "report-policy.json"
LEDGER_FILENAME = "report-trust.json"

_STATE_WORD = {
    "waiting": "在等你",
    "ready": "做完了",
    "blocked": "卡住了",
    "failed": "没做成",
    "dropped": "停下了",
}


@dataclass(frozen=True)
class ReportPolicy:
    """只有一颗开关；缺省关，且读不出来也当关（宁可多喊，不可漏报）。"""

    auto_approve: bool = DEFAULT_AUTO_APPROVE


def _home() -> Path:
    override = os.environ.get("PLOBI_HOME", "").strip()
    if override:
        return Path(override)
    try:
        from plobi_constants import get_plobi_home

        return Path(get_plobi_home())
    except Exception:  # noqa: BLE001 - 常量件缺失时退回用户家，不把判据卡死
        return Path.home() / ".plobi"


def policy_path(path: Optional[Path | str] = None) -> Path:
    return Path(path) if path is not None else _home() / POLICY_FILENAME


def ledger_path(path: Optional[Path | str] = None) -> Path:
    return Path(path) if path is not None else _home() / LEDGER_FILENAME


def load_policy(path: Optional[Path | str] = None) -> ReportPolicy:
    """读不出来 / 写坏 / 类型不对，一律回出厂关。字符串 ``"true"`` 不算开。"""
    try:
        raw = json.loads(policy_path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ReportPolicy()
    if not isinstance(raw, dict):
        return ReportPolicy()
    return ReportPolicy(auto_approve=raw.get("auto_approve") is True)


def decision_category(view: dict[str, Any]) -> str:
    """类别由**票的结构**推，不由模型猜、不从自由文本里读（裁定 199 同款纪律）。

    ``leg`` 区分这一跳是 Agent 之间／专家／外包／外部事件，``domain`` 是派工时
    按名册行固化进票的域。两者都来自 ``ticket.refs``，不是 prompt 的一部分。
    """
    leg = str(view.get("leg") or "unknown").strip().lower()
    domain = str(view.get("domain") or "general").strip().lower()
    return f"{leg}:{domain}"


def is_never_auto(view: dict[str, Any]) -> bool:
    """两类永不因信任而免问。

    ① 外部事件票：裁定 215 #35 定死「默认 observe、由人放行」，攒多少次都不许
       系统替他放行；② 日记／画像那一族：沿用裁定 199 的 ``NEVER_AUTO_CATEGORIES``。
    """
    if str(view.get("leg") or "").strip().lower() == "trigger":
        return True
    return str(view.get("domain") or "").strip().lower() in NEVER_AUTO_CATEGORIES


def should_escalate(
    view: dict[str, Any],
    *,
    policy: Optional[ReportPolicy] = None,
    ledger: Optional[TrustLedger] = None,
) -> bool:
    """这一刻该不该向用户开口。任何不确定都回 ``True``（宁可多喊）。"""
    last = view.get("last_report") or {}
    state = str(last.get("state") or "").strip().lower()
    if state not in NEEDS_HUMAN_STATES:
        return False
    if is_never_auto(view):
        return True
    gate = policy if policy is not None else load_policy()
    if not gate.auto_approve:
        return True
    book = ledger if ledger is not None else TrustLedger(path=ledger_path())
    return book.should_ask(decision_category(view), TRUST_SOURCE)


def render_one(view: dict[str, Any]) -> str:
    """按 ``REPORTING_RULES`` 出一句人话：只讲结论＋要人做的那一个动作。

    不列工具、不报内部编号（票号只在 ``escalations`` 的结构字段里给程序用），
    一次只问一个问题，纯进度由 ``should_escalate`` 挡在门外。
    """
    last = view.get("last_report") or {}
    state = str(last.get("state") or "").strip().lower()
    word = _STATE_WORD.get(state, "有情况")
    goal = " ".join(str(view.get("goal") or "那件事").split())[:28]
    note = " ".join(str(last.get("note") or "").split())[:40]
    text = f"「{goal}」{word}"
    if note:
        text = f"{text}：{note}"
    return f"{text}。要我继续，还是你来定？"[:MAX_ONE_LINE]


def due_escalations(
    views: Iterable[dict[str, Any]],
    *,
    policy: Optional[ReportPolicy] = None,
    ledger: Optional[TrustLedger] = None,
) -> list[dict[str, Any]]:
    """把一批票视图筛成「待喊人」清单。"""
    out: list[dict[str, Any]] = []
    for view in views:
        if not should_escalate(view, policy=policy, ledger=ledger):
            continue
        category = decision_category(view)
        state = str((view.get("last_report") or {}).get("state") or "").strip().lower()
        out.append({
            "ticket_id": str(view.get("ticket_id") or ""),
            "state": state,
            "category": category,
            "one_line": render_one(view),
        })
    return out


__all__ = [
    "AUTO_THRESHOLD",
    "DEFAULT_AUTO_APPROVE",
    "MAX_ONE_LINE",
    "NEEDS_HUMAN_STATES",
    "NEVER_AUTO_CATEGORIES",
    "ReportPolicy",
    "decision_category",
    "due_escalations",
    "is_never_auto",
    "ledger_path",
    "load_policy",
    "policy_path",
    "render_one",
    "should_escalate",
    "TRUST_SOURCE",
]
