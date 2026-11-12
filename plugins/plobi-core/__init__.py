"""Plobi North Star plugin — deep edge module (narrow public surface).

Public agent tool: ``plobi`` (area + action).
Public HTTP: ``/api/plugins/plobi-core/*`` (dashboard plugin_api).
Internals live under ``lib/`` and must not be imported by Electron/UI code.
"""

from __future__ import annotations

import logging
from typing import Any

from . import channel as CH
from . import hard_route as HR
from . import tools as T

logger = logging.getLogger(__name__)

TOOLSET = "plobi_core"

# ── Agent 之间的两枚原子工具（裁定 183）────────────────────────────────
# 旧的那 7 枚带 intent/action 枚举的嘴（plobi_secretary_ask、plobi_checkin_respond、
# plobi_master_status/preview/dispatch/approve、plobi_agent_ask）按裁定 183 全删：
# 「问话和派工或者别的不都是一件事情吗？就是一个 Agent 给另一个 Agent 发了一段提示词吗？」
# 替它们的只有 say 与 ask，副作用归接话方自己的工具，消息直接进对方的聊天。
_CHANNEL_TOOLS = (
    (
        "say",
        CH.SAY_SCHEMA,
        CH.handle_say,
        CH.SAY_SCHEMA["description"],
        "🗣",
    ),
    (
        "ask",
        CH.ASK_SCHEMA,
        CH.handle_ask,
        CH.ASK_SCHEMA["description"],
        "🙋",
    ),
)

def _schema(name: str, description: str, props: dict | None = None, required: list | None = None) -> dict:
    params: dict = {"type": "object", "properties": props or {}}
    if required:
        params["required"] = required
    return {"name": name, "description": description, "parameters": params}


_STR = {"type": "string"}


def _domain_handler(area: str, action: str):
    """一枚工具 = 一个 area 的一个 action。

    area/action 由注册处**写死**，模型不再自己选分支——这正是裁定 185 要的形态
    （一枚工具只干一件事），也是 Tools 页能按门类开关的前提。
    """

    def run(args: dict, **kwargs) -> str:
        return T.plobi({"area": area, "action": action, **(args or {})})

    return run


# ── 10-31 工具面按门类重排（WP-TOOLSET-INBOUND-2）────────────────────────
# 原来一枚 `plobi` 用 area/action 选分支：模型要猜 area，Tools 页也开不了关，
# 用户看不见就等于没有。现在拆成具体工具，各自挂在 task / edge / ops / mind 门类下。
# `plobi()` 函数保留在 tools.py 里作 back-compat（skills 与测试还在调它）。
# (name, toolset, area, action, description, properties, required)
_DOMAIN_TOOLS: tuple = (
    # ── 任务队列 ──
    ("task_board", "task", "task", "board", "读任务板：排队中 / 分阶段 / 已完成的行，带风险与阶段。",
     {"limit": {"type": "integer"}, "status": _STR}, None),
    ("task_enqueue", "task", "task", "enqueue", "把一件要做的事放进任务队列。",
     {"goal": _STR, "risk": _STR, "domain": _STR, "summary": _STR, "mirror_kanban": {"type": "boolean"}},
     ["goal"]),
    ("task_get", "task", "task", "get", "按 id 读一条任务（含阶段与状态）。",
     {"task_id": _STR}, ["task_id"]),
    ("task_approve", "task", "task", "approve", "批准一条排队的任务，让它能跑。",
     {"task_id": _STR}, ["task_id"]),
    ("task_reject", "task", "task", "reject", "驳回一条任务并写明原因。",
     {"task_id": _STR, "reason": _STR}, ["task_id"]),
    ("task_complete", "task", "task", "complete", "把一条任务标成完成，附结果摘要。",
     {"task_id": _STR, "result": _STR}, ["task_id"]),
    ("task_update", "task", "task", "update", "更新一条任务的状态或摘要。",
     {"task_id": _STR, "status": _STR, "summary": _STR}, ["task_id"]),
    ("task_stage_advance", "task", "task", "stage_advance", "把一条任务推进到下一个阶段。",
     {"task_id": _STR, "stage": _STR}, ["task_id"]),
    ("task_stage_approve", "task", "task", "stage_approve", "批准一条任务当前阶段的产出。",
     {"task_id": _STR, "stage": _STR}, ["task_id"]),

    # ── 边缘能力 ──
    ("edge_compute", "edge", "compute", "route", "为一次计算选一条路（本地 / 云端 / 设备），并说明理由。",
     {"surface": _STR, "prefer": _STR, "prompt": _STR, "goal": _STR}, None),
    ("edge_hid_status", "edge", "compute", "hid_status", "看边缘设备（HID worker）现在的状态。",
     {}, None),
    ("edge_hid_run", "edge", "compute", "hid_run", "在一台边缘设备上跑一组动作。",
     {"device_id": _STR, "actions": {"type": "array", "items": {"type": "object"}}, "task_id": _STR,
      "risk": _STR, "stage": _STR}, None),
    ("edge_preview_push", "edge", "preview", "push", "把一份产物推到预览面板上给人看。",
     {"title": _STR, "priority": _STR, "kind": _STR, "url": _STR, "path": _STR, "text": _STR,
      "auto_open": {"type": "boolean"}}, None),
    ("edge_preview_list", "edge", "preview", "list", "列出最近的预览项。",
     {"limit": {"type": "integer"}}, None),
    ("edge_preview_latest", "edge", "preview", "latest", "取最新一条该自动打开的预览。",
     {}, None),

    # ── 运维（L1 的日常职责）──
    ("ops_plan", "ops", "ops", "master_plan", "给今天排一份计划。",
     {"goal": _STR, "risk": _STR, "max_chars": {"type": "integer"}}, None),
    ("ops_summarize", "ops", "ops", "master_summarize", "把当前进展收成一段摘要。",
     {"max_chars": {"type": "integer"}}, None),
    ("ops_morning", "ops", "ops", "morning_report", "出早报。",
     {"max_chars": {"type": "integer"}}, None),
    ("ops_night", "ops", "ops", "night_tick", "跑夜间那一轮（复盘 / 收尾）。",
     {}, None),
    ("ops_mobile_board", "ops", "ops", "mobile_board", "给手机那一面出看板。",
     {}, None),
    ("ops_mobile_instruct", "ops", "ops", "mobile_instruct", "把一条指令派给手机那一面。",
     {"instruction": _STR, "text": _STR}, ["instruction"]),
    ("ops_domain_list", "ops", "ops", "domain_list", "列出已登记的领域。",
     {}, None),
    ("ops_domain_register", "ops", "ops", "domain_register", "登记一个新领域。",
     {"domain": _STR, "label": _STR, "default_risk": _STR, "enabled": {"type": "boolean"}}, ["domain"]),
    ("ops_diagnose", "ops", "ops", "diagnose", "做一次自检并给出结论。",
     {"error": _STR, "status": _STR}, None),

    # ── 学习（挂在脑那一门）──
    ("learn_observe", "mind", "ops", "learn_observe", "记一条观察，供之后整理成知识。",
     {"text": _STR, "domain": _STR}, ["text"]),
    ("learn_drafts", "mind", "ops", "learn_drafts", "看还没定稿的学习草稿。",
     {"limit": {"type": "integer"}}, None),
    ("learn_resolve", "mind", "ops", "learn_resolve", "定一条学习草稿：采纳或丢掉。",
     {"draft_id": _STR, "approve": {"type": "boolean"}}, ["draft_id"]),
)

_DOMAIN_EMOJI = {"task": "📋", "edge": "🛰", "ops": "🧭", "mind": "🎓"}


def register(ctx) -> None:
    # say / ask 不挂任何开关：名册里一条可发的人都没有时，check_channel_available
    # 让它们的 schema 根本不下发（Footprint Ladder rung 3，零足迹）。
    # 10-31：它们从 plobi_core 挪进自己的 channel 门类——「跟另一个 Agent
    # 说话」是一件独立的事，不该和边缘能力挤在一个筐里。
    for name, schema, handler, description, emoji in _CHANNEL_TOOLS:
        ctx.register_tool(
            name=name,
            toolset="channel",
            schema=schema,
            handler=handler,
            check_fn=CH.check_channel_available,
            description=description,
            emoji=emoji,
        )

    # 裁定 216：ticket_report 是**旁路状态通道**，不是第三枚嘴——它不向任何
    # Agent 传提示词，只更新自己那张授权票的六态。因此不挂
    # check_channel_available（名册里一条可发的人都没有时，票仍可能来自
    # webhook 或 L2→L3，状态照样要能报）。裁定 183 的两枚嘴一个字没动。
    ctx.register_tool(
        name=CH.TICKET_REPORT_SCHEMA["name"],
        toolset="channel",
        schema=CH.TICKET_REPORT_SCHEMA,
        handler=CH.handle_ticket_report,
        description=CH.TICKET_REPORT_SCHEMA["description"],
        emoji="\u2713",
    )

    for name, toolset, area, action, description, props, required in _DOMAIN_TOOLS:
        ctx.register_tool(
            name=name,
            toolset=toolset,
            schema=_schema(name, description, props, required),
            handler=_domain_handler(area, action),
            description=description,
            emoji=_DOMAIN_EMOJI.get(toolset, "🧭"),
        )

    try:
        from plobi.agents.registry import ensure_north_star_toolset

        ensure_north_star_toolset()
    except Exception:
        logger.debug("plobi-core: toolset ensure on active profile skipped", exc_info=True)
    ctx.register_hook("pre_gateway_dispatch", _on_pre_gateway_dispatch)
    # WP-BE-13 / 裁定 21.1: pin §8.2's two frozen L1 utterances onto the
    # ``ask`` atom at the GUI/dashboard user-turn boundary (裁定 183 换了嘴：
    # 旧那 7 枚 intent 枚举全删，硬路由跟着指 ask，标签留在 hard_route 的表里).
    # (裁定 21.1 有效：软路由 2026-09-08 已失败，窄硬路由就是当前产品形态；
    #  任何 QA 验证需要软路由对照时，用临时分支，不许关交付钩子。)
    ctx.register_hook("pre_prompt_submit", HR.on_pre_prompt_submit)
    # WP-L1-BUDGET: 在 brand-new session 第一回合开头收紧 L1 默认 profile 的
    # 回合后 curator + 压缩阈值。handler 内部做 L1/L2 判定，非 L1 直接 return。
    from . import l1_budget as _l1b
    ctx.register_hook("on_session_start", _l1b.on_session_start)
    logger.info(
        "plobi-core: registered deep tool 'plobi' + the say/ask channel "
        "tools (裁定 183 折掉了 plobi_secretary_ask / plobi_agent_ask / "
        "plobi_master_*，别再照旧名报数) + gateway hook "
        "+ §8.2 hard route"
        " + L1 budget hook"
    )


def _on_pre_gateway_dispatch(event: Any = None, gateway: Any = None, **kwargs) -> Any:
    """Mobile commands → rewrite into prompts that call the deep ``plobi`` tool."""
    raw = (getattr(event, "text", None) or "").strip()
    if not raw.lower().startswith("/plobi"):
        return None

    parts = raw.split(maxsplit=2)
    cmd = parts[1].lower() if len(parts) > 1 else "board"
    arg = parts[2] if len(parts) > 2 else ""

    prompts = {
        "board": (
            "Call plobi with area=ops action=mobile_board and reply with a concise "
            "status board (awaiting approvals first). No remote mouse."
        ),
        "status": "Call plobi area=ops action=mobile_board and summarize for mobile.",
        "report": "Call plobi area=ops action=morning_report and send the report.",
        "preview": (
            "Call plobi area=preview action=list and summarize. "
            "User may also open the desktop preview panel manually anytime."
        ),
        "help": (
            "Explain /plobi board|approve <id>|reject <id>|instruct <text>|report|preview. "
            "Use the plobi tool (deep module). No remote mouse."
        ),
    }
    if cmd in prompts:
        return {"action": "rewrite", "text": prompts[cmd]}
    if cmd == "approve" and arg:
        return {
            "action": "rewrite",
            "text": (
                f"Human approved {arg.strip()} from mobile. "
                f"Call plobi area=task action=approve task_id={arg.strip()} "
                f"(and stage_approve if needed). Confirm briefly."
            ),
        }
    if cmd == "reject" and arg:
        return {
            "action": "rewrite",
            "text": (
                f"Human rejected {arg.strip()} from mobile. "
                f"Call plobi area=task action=reject task_id={arg.strip()}."
            ),
        }
    if cmd in {"instruct", "do"} and arg:
        return {
            "action": "rewrite",
            "text": (
                f"Mobile instruction: {arg}\n"
                "Call plobi area=ops action=mobile_instruct with that instruction, "
                "then outline Master next steps. Do not take over the mouse."
            ),
        }
    return {"action": "rewrite", "text": prompts["help"]}
