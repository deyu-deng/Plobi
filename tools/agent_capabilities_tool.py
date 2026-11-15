"""``agent_capabilities``：报这个 Agent 现在能自主调什么。

**定名**（用户 2026-11-15 12:5x）：这条线叫 **Capabilities**。

为什么要有这把：能力分散在技能／工具／MCP 三条过滤链里，**没有一处能一次回答
「这个 Agent 手里有什么」**——派活之前想知道某个 Agent 能不能跑命令、能不能看
某个 MCP，只能去翻三处代码。这把只**汇**不**判**（真源在
:mod:`plobi.agents.capabilities`）。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

from tools.registry import registry

logger = logging.getLogger(__name__)

TOOLSET = "capabilities"

AGENT_CAPABILITIES_SCHEMA = {
    "name": "agent_capabilities",
    "description": (
        "报这个 Agent 现在能自主调什么：**技能**、**工具门类**、**MCP 服务器**，"
        "三份黑名单（这个 Agent 关掉了哪些）也一并报出来。"
        "派活之前拿不准某个能力有没有，就问它——**没被列出来的那几样现在调不到**。"
        "留空问的是当前会话这个 Agent。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "agent": {
                "type": "string",
                "description": "问哪个 Agent；填名册里的名字，留空＝当前会话这个。",
            },
        },
        "required": [],
    },
}


def _handle(args: Dict[str, Any], **_kw: Any) -> str:
    from plobi.agents.capabilities import capabilities_for

    try:
        payload = capabilities_for(str(args.get("agent") or ""))
    except Exception as exc:  # noqa: BLE001 - 失败也要是一句人话
        logger.warning("agent_capabilities: cannot compute capabilities: %s", exc)
        return json.dumps(
            {"ok": False, "error": f"算不出来：{type(exc).__name__}: {exc}"},
            ensure_ascii=False,
        )
    payload["ok"] = True
    return json.dumps(payload, ensure_ascii=False)


registry.register(
    name="agent_capabilities",
    toolset=TOOLSET,
    schema=AGENT_CAPABILITIES_SCHEMA,
    handler=_handle,
    emoji="🧭",
)
