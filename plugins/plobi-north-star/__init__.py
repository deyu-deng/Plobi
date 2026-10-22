"""Plobi North Star plugin — deep edge module (narrow public surface).

Public agent tool: ``plobi`` (area + action).
Public HTTP: ``/api/plugins/plobi-north-star/*`` (dashboard plugin_api).
Internals live under ``lib/`` and must not be imported by Electron/UI code.
"""

from __future__ import annotations

import logging
from typing import Any

from . import channel as CH
from . import cli_say
from . import hard_route as HR
from . import master_tools as MT
from . import tools as T

logger = logging.getLogger(__name__)

TOOLSET = "plobi_north_star"

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

_PLOBI_SCHEMA = {
    "name": "plobi",
    "description": (
        "Plobi North Star deep tool. Pass area=task|compute|preview|ops and an action. "
        "See docs/plobi/north_star/API.md. Prefer summaries for Master; use HID/aigw "
        "via compute; reuse Plobi kanban/gateway/cron instead of reinventing them."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "area": {
                "type": "string",
                "enum": ["task", "compute", "preview", "ops"],
                "description": "Subsystem façade",
            },
            "action": {
                "type": "string",
                "description": (
                    "task: enqueue|board|get|approve|reject|complete|update|"
                    "stage_status|stage_advance|stage_approve; "
                    "compute: route|hid_status|hid_run; "
                    "preview: push|list|latest; "
                    "ops: master_plan|master_summarize|morning_report|night_tick|"
                    "mobile_board|mobile_instruct|domain_list|domain_register|"
                    "diagnose|learn_observe|learn_drafts|learn_resolve"
                ),
            },
            "goal": {"type": "string"},
            "task_id": {"type": "string"},
            "risk": {"type": "string"},
            "domain": {"type": "string"},
            "summary": {"type": "string"},
            "result": {"type": "string"},
            "reason": {"type": "string"},
            "status": {"type": "string"},
            "stage": {"type": "string"},
            "surface": {"type": "string"},
            "route": {"type": "string"},
            "prefer": {"type": "string"},
            "prompt": {"type": "string"},
            "actions": {"type": "array", "items": {"type": "object"}},
            "device_id": {"type": "string"},
            "mock": {"type": "boolean"},
            "include_raw": {"type": "boolean"},
            "title": {"type": "string"},
            "priority": {"type": "string"},
            "kind": {"type": "string"},
            "url": {"type": "string"},
            "path": {"type": "string"},
            "text": {"type": "string"},
            "auto_open": {"type": "boolean"},
            "limit": {"type": "integer"},
            "instruction": {"type": "string"},
            "id": {"type": "string"},
            "label": {"type": "string"},
            "default_risk": {"type": "string"},
            "enabled": {"type": "boolean"},
            "steps": {"type": "array", "items": {"type": "string"}},
            "draft_id": {"type": "string"},
            "approve": {"type": "boolean"},
            "error": {"type": "string"},
            "max_chars": {"type": "integer"},
            "mirror_kanban": {"type": "boolean"},
        },
        "required": ["area", "action"],
    },
}


def register(ctx) -> None:
    # WP-L1-MOUTH-SWITCH: the deep tool ``plobi`` belongs to the same switched
    # family as the 4 Master tools — it is visible exactly when the user's
    # switch says so (``check_plobi_l1_master_tools``), never by editing here.
    ctx.register_tool(
        name="plobi",
        toolset=TOOLSET,
        schema=_PLOBI_SCHEMA,
        handler=T.plobi,
        check_fn=MT.check_plobi_l1_master_tools,
        description=_PLOBI_SCHEMA["description"],
        emoji="🧭",
    )
    # say / ask 不挂任何开关：名册里一条可发的人都没有时，check_channel_available
    # 让它们的 schema 根本不下发（Footprint Ladder rung 3，零足迹）。
    for name, schema, handler, description, emoji in _CHANNEL_TOOLS:
        ctx.register_tool(
            name=name,
            toolset=TOOLSET,
            schema=schema,
            handler=handler,
            check_fn=CH.check_channel_available,
            description=description,
            emoji=emoji,
        )
    # ``plobi say`` = 同一枚嘴的第二个把手，给**不在工具循环里**的说话者（外包出去的
    # L2、终端上的人）。它不新造投递：直接调上面那只 ``CH.handle_say``，所以落点、
    # 留痕、唤醒与内部 Agent 用的 ``say`` 逐字相同（裁 94）。身份只认
    # ``PLOBI_AGENT_ID``——这一档故意没有 ``--as``。
    ctx.register_cli_command(
        name="say",
        help="Say something to another Agent (same delivery path as the `say` tool)",
        setup_fn=cli_say.setup,
        handler_fn=cli_say.run,
        description=(
            "Hand a message to an Agent in the roster. Same mouth as the `say` "
            "tool — one delivery path, one trace. The speaker is whoever "
            "PLOBI_AGENT_ID says; there is no --as flag on purpose."
        ),
    )
    try:
        from plobi.agents.registry import ensure_north_star_toolset

        ensure_north_star_toolset()
    except Exception:
        logger.debug("plobi-north-star: toolset ensure on active profile skipped", exc_info=True)
    ctx.register_hook("pre_gateway_dispatch", _on_pre_gateway_dispatch)
    # WP-BE-13 / 裁定 21.1: pin §8.2's two frozen L1 utterances onto
    # plobi_secretary_ask at the GUI/dashboard user-turn boundary.
    # (裁定 21.1 有效：软路由 2026-09-08 已失败，窄硬路由就是当前产品形态；
    #  任何 QA 验证需要软路由对照时，用临时分支，不许关交付钩子。)
    ctx.register_hook("pre_prompt_submit", HR.on_pre_prompt_submit)
    # WP-L1-BUDGET: 在 brand-new session 第一回合开头收紧 L1 默认 profile 的
    # 回合后 curator + 压缩阈值。handler 内部做 L1/L2 判定，非 L1 直接 return。
    from . import l1_budget as _l1b
    ctx.register_hook("on_session_start", _l1b.on_session_start)
    logger.info(
        "plobi-north-star: registered deep tool 'plobi' + 6 narrow tools "
        "(incl. plobi_secretary_ask, plobi_agent_ask) + gateway hook "
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
