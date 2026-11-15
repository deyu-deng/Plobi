"""一个 Agent 现在能自主调什么（**Capabilities**）。

**定名**（用户 2026-11-15 12:5x）：这条线叫 **Capabilities**——此前说的
"Capacity" 是拼错的，代码与文档一律按前者落。

一个 Agent 的能力有三样：**技能**、**工具门类**、**MCP 服务器**。三样各自已经
有一处判据；本模块**只把这三处的结果并排报出来，不重判一次**（裁 94 同族：
判据只有一份，清单与工具共用它）：

============  =================================================================
能力          判据（唯一一份）
============  =================================================================
技能          ``agent.skill_utils.get_disabled_skill_names()``（全局 ∪ 平台）
              ＋ 名册行的 ``disabled_skills``；装着的清单来自
              ``tools.skills_tool._find_all_skills()``
工具          ``plobi_cli.tools_config._get_platform_tools()``（平台开的门类）
              − 名册行的 ``disabled_toolsets``；实际进表的工具名来自
              ``model_tools.get_tool_definitions()``
MCP           ``tools.mcp_tool._load_mcp_config()``（全局表，一人一份全员可见）
              − 名册行的 ``disabled_mcp_servers``
============  =================================================================

**为什么要有这一处**：能力分散在三个模块的三个过滤链里，**没有一处能一次回答
「这个 Agent 手里有什么」**——派活之前想知道某个 Agent 能不能跑命令、能不能看
某个 MCP，只能去翻三处代码。这一层只**汇**，不**判**。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

__all__ = ["capabilities_for", "current_agent_name", "current_platform"]


def current_platform(platform: Optional[str] = None) -> str:
    """当前会话跑在哪个表面上（显式传 > 环境变量 > 会话 ContextVar > ``cli``）。"""
    if platform:
        return str(platform).strip()
    env = os.getenv("PLOBI_PLATFORM") or ""
    if not env:
        try:
            from gateway.session_context import get_session_env

            env = get_session_env("PLOBI_SESSION_PLATFORM") or ""
        except Exception:  # noqa: BLE001 - 读不到就按没有平台处理
            env = ""
    return str(env).strip() or "cli"


def current_agent_name(agent_name: str = "") -> str:
    """问的是哪个 Agent（留空＝当前会话那个）。"""
    name = str(agent_name or "").strip()
    if name:
        return name
    try:
        from plobi.agents.registry import active_agent_name

        return str(active_agent_name() or "").strip()
    except Exception:  # noqa: BLE001 - 名册读不到就当没有分身
        logger.debug("capabilities: cannot resolve the active agent name")
        return ""


def _disabled_sets(agent_name: str) -> Dict[str, set]:
    """这个 Agent 的三份黑名单（名册行那一份 ＋ 全局/平台那几份）。"""
    from plobi.agents.registry import agent_disabled_sets

    row = agent_disabled_sets(agent_name) or {}
    return {
        "skills": {str(s).casefold() for s in (row.get("skills") or set())},
        "toolsets": {str(t).casefold() for t in (row.get("toolsets") or set())},
        "mcp_servers": {str(m).casefold() for m in (row.get("mcp_servers") or set())},
    }


def _visible_skills(platform: str, disabled: set) -> List[str]:
    """看得见的技能（装着的 − 禁用的）。"""
    from agent.skill_utils import get_disabled_skill_names

    blocked = {str(s).casefold() for s in get_disabled_skill_names(platform)} | disabled
    try:
        from tools.skills_tool import _find_all_skills

        installed = _find_all_skills(skip_disabled=True)
    except Exception as exc:  # noqa: BLE001 - 技能目录读不动就说读不动
        logger.warning("capabilities: cannot list skills: %s", exc)
        return []
    names = []
    for entry in installed:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        if not name or name.casefold() in blocked:
            continue
        names.append(name)
    return sorted(set(names))


def _visible_toolsets(platform: str, disabled: set) -> List[str]:
    """开着的工具门类（平台开的 − 名册行禁的）。"""
    try:
        from plobi_cli.config import load_config
        from plobi_cli.tools_config import _get_platform_tools

        enabled = _get_platform_tools(load_config(), platform)
    except Exception as exc:  # noqa: BLE001 - 配置读不到就说读不到
        logger.warning("capabilities: cannot resolve platform toolsets: %s", exc)
        return []
    return sorted({str(t) for t in enabled if str(t).casefold() not in disabled})


def _visible_mcp_servers(disabled: set) -> List[str]:
    """看得见的 MCP 服务器（全局表 − 名册行禁的）。"""
    try:
        from tools.mcp_tool import _load_mcp_config

        servers = _load_mcp_config()
    except Exception as exc:  # noqa: BLE001 - MCP 读不到就说读不到
        logger.warning("capabilities: cannot read the MCP table: %s", exc)
        return []
    return sorted({str(name) for name in servers if str(name).casefold() not in disabled})


def capabilities_for(agent_name: str = "", *, platform: Optional[str] = None) -> Dict[str, Any]:
    """这个 Agent 现在能自主调什么：一处把三样能力并排报出来。

    留空 ``agent_name`` ⇒ 问当前会话那个。**只读**：不启动任何东西、不改任何配置。
    """
    name = current_agent_name(agent_name)
    surface = current_platform(platform)
    disabled = _disabled_sets(name)

    toolsets = _visible_toolsets(surface, disabled["toolsets"])
    skills = _visible_skills(surface, disabled["skills"])
    mcp_servers = _visible_mcp_servers(disabled["mcp_servers"])

    tools: List[str] = []
    try:
        from model_tools import get_tool_definitions

        for schema in get_tool_definitions(
            enabled_toolsets=toolsets or None,
            disabled_toolsets=sorted(disabled["toolsets"]),
            quiet_mode=True,
            skip_tool_search_assembly=True,
        ):
            fn = schema.get("function") if isinstance(schema, dict) else None
            tool_name = str((fn or {}).get("name") or "") if isinstance(fn, dict) else ""
            if tool_name:
                tools.append(tool_name)
    except Exception as exc:  # noqa: BLE001 - 工具表算不出来就说算不出来
        logger.warning("capabilities: cannot compute the tool table: %s", exc)

    return {
        "agent": name,
        "platform": surface,
        "skills": skills,
        "toolsets": toolsets,
        "tools": sorted(set(tools)),
        "mcp_servers": mcp_servers,
        "blocked": {
            "skills": sorted(disabled["skills"]),
            "toolsets": sorted(disabled["toolsets"]),
            "mcp_servers": sorted(disabled["mcp_servers"]),
        },
        "note": (
            "这是**此刻**的能力：技能＝装着的减掉禁用的；工具＝平台开的门类减掉"
            "这个 Agent 禁用的；MCP＝全局那张表减掉这个 Agent 禁用的。"
            "没被列出来的那几样就是**现在调不到**的。"
        ),
    }
