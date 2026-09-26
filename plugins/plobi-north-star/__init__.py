"""Plobi North Star plugin — deep edge module (narrow public surface).

Public agent tool: ``plobi`` (area + action).
Public HTTP: ``/api/plugins/plobi-north-star/*`` (dashboard plugin_api).
Internals live under ``lib/`` and must not be imported by Electron/UI code.
"""

from __future__ import annotations

import logging
from typing import Any

from . import hard_route as HR
from . import master_tools as MT
from . import tools as T

logger = logging.getLogger(__name__)

TOOLSET = "plobi_north_star"

# L1 Master narrow surface (B1): dispatch / status / preview / approve are
# thin wrappers over Hermes kanban, registered under the same toolset so the
# secretary gets them WITHOUT the raw ``kanban_*`` lifecycle tools.
#
# WP-L1-EVERY-TURN (裁定 33.1 / 33.4): 4 个 Master 工具 + 深工具 ``plobi``
# 本周对 L1 关闭——check_fn 切到 ``check_plobi_l1_mouth_disabled``（恒
# False）。``plobi_secretary_ask`` + ``plobi_checkin_respond`` 仍用
# ``check_plobi_master_mode``——L1 本周只剩这两张嘴。
_MASTER_TOOLS = (
    # ── 4 个 Master 工具（M3 才开；handler 保留） ─────────────────────
    (
        "plobi_master_status",
        MT.MASTER_STATUS_SCHEMA,
        MT.handle_master_status,
        MT.MASTER_STATUS_SCHEMA["description"],
        "🗂",
    ),
    (
        "plobi_master_preview",
        MT.MASTER_PREVIEW_SCHEMA,
        MT.handle_master_preview,
        MT.MASTER_PREVIEW_SCHEMA["description"],
        "🔎",
    ),
    (
        "plobi_master_dispatch",
        MT.MASTER_DISPATCH_SCHEMA,
        MT.handle_master_dispatch,
        MT.MASTER_DISPATCH_SCHEMA["description"],
        "🚀",
    ),
    (
        "plobi_master_approve",
        MT.MASTER_APPROVE_SCHEMA,
        MT.handle_master_approve,
        MT.MASTER_APPROVE_SCHEMA["description"],
        "✅",
    ),
    # ── L1 这周仍开放的两张嘴（§8.2 硬路由路径） ────────────────────
    (
        "plobi_secretary_ask",
        MT.SECRETARY_ASK_SCHEMA,
        MT.handle_secretary_ask,
        MT.SECRETARY_ASK_SCHEMA["description"],
        "📅",
    ),
    (
        "plobi_checkin_respond",
        MT.CHECKIN_RESPOND_SCHEMA,
        MT.handle_checkin_respond,
        MT.CHECKIN_RESPOND_SCHEMA["description"],
        "🗓",
    ),
)

_PLOBI_SCHEMA = {
    "name": "plobi",
    "description": (
        "Plobi North Star deep tool. Pass area=task|compute|preview|ops and an action. "
        "See docs/plobi/north_star/API.md. Prefer summaries for Master; use HID/aigw "
        "via compute; reuse Hermes kanban/gateway/cron instead of reinventing them."
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
    # WP-L1-EVERY-TURN: deep tool ``plobi`` 本周也对 L1 关闭（与 4 个
    # Master 工具同口径）—— ``check_plobi_l1_mouth_disabled`` 恒 False。
    # M3 切回开放时把 check_fn 改回 None 即可。
    ctx.register_tool(
        name="plobi",
        toolset=TOOLSET,
        schema=_PLOBI_SCHEMA,
        handler=T.plobi,
        check_fn=MT.check_plobi_l1_mouth_disabled,
        description=_PLOBI_SCHEMA["description"],
        emoji="🧭",
    )
    # WP-L1-EVERY-TURN: 4 个 Master 工具 + 2 个 secretary 工具分组挂
    # check_fn。Master 组 (status / preview / dispatch / approve) 切到
    # 恒 False；secretary 组 (secretary_ask / checkin_respond) 保持
    # ``check_plobi_master_mode``——L1 这周只剩这两张嘴。
    _L1_MOUTH_CLOSED = {
        "plobi_master_status",
        "plobi_master_preview",
        "plobi_master_dispatch",
        "plobi_master_approve",
    }
    for name, schema, handler, description, emoji in _MASTER_TOOLS:
        if name in _L1_MOUTH_CLOSED:
            check_fn = MT.check_plobi_l1_mouth_disabled
        else:
            # plobi_secretary_ask / plobi_checkin_respond
            check_fn = MT.check_plobi_master_mode
        ctx.register_tool(
            name=name,
            toolset=TOOLSET,
            schema=schema,
            handler=handler,
            check_fn=check_fn,
            description=description,
            emoji=emoji,
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
        "plobi-north-star: registered deep tool 'plobi' + 5 master "
        "narrow tools (incl. plobi_secretary_ask) + gateway hook + §8.2 hard route"
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
