r"""Plobi 工具面 MCP：把 `say` / `ask` 暴露给外部 MCP 客户端（`WP-TOOLS-MCP`）。

和 `mcp_serve.py` 的分工：那个暴露**会话**（搜会话 / 读消息 / 事件 / 平台发信）；
这个暴露**工具**（Agent 之间说话与问话）。两个都用现成的 FastMCP，不另写协议循环。

用法::

    plobi mcp serve-tools

客户端配置（Qoder / Claude Code / Cursor 通用）::

    {
      "mcpServers": {
        "plobi-tools": {
          "command": "<Code>/.venv/Scripts/plobi.exe",
          "args": ["mcp", "serve-tools"],
          "env": {"PLOBI_HOME": "D:\Data\AppData\plobi", "PLOBI_AGENT_ID": "qoder"}
        }
      }
    }

身份：`PLOBI_AGENT_ID` 决定 `say`/`ask` 落到对方屏上时署的是谁（空 = `plobi`）。
暴露面：默认只有 `say` 与 `ask` 两枚（裁定 185：工具面只给原子工具）；
`PLOBI_MCP_TOOLS=say,ask,...` 可以显式扩展，但**不许**把带 `action`/`intent` 的大工具塞进来。
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any, Optional

logger = logging.getLogger("plobi.mcp_tools")

#: 默认暴露面：只有这两枚原子（裁 185）。
DEFAULT_TOOLS: tuple[str, ...] = ("say", "ask")


def exposed_tool_names() -> tuple[str, ...]:
    """要暴露哪些工具：默认两枚，``PLOBI_MCP_TOOLS`` 逗号分隔可覆盖。"""
    raw = (os.environ.get("PLOBI_MCP_TOOLS") or "").strip()
    if not raw:
        return DEFAULT_TOOLS
    names = tuple(part.strip() for part in raw.split(",") if part.strip())
    return names or DEFAULT_TOOLS


def dispatch_tool(name: str, args: dict[str, Any]) -> str:
    """走**现成的**工具注册表——这里不复制一份发送逻辑。"""
    from tools.registry import registry

    session_id = (os.environ.get("PLOBI_MCP_SESSION_ID") or "").strip()
    result = registry.dispatch(name, args, session_id=session_id)
    return result if isinstance(result, str) else str(result)


def build_server():
    """组装 MCP server（FastMCP）。测试只碰这个函数，不真起进程。"""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("plobi-tools")
    names = exposed_tool_names()

    if "say" in names:

        @mcp.tool(name="say")
        def say(to: str, parts: list[dict[str, Any]], reply_to: Optional[str] = None) -> str:
            """给另一个 Agent 说一段话（投递即返回，不等它回）。to 填名册里那一行的名字；
            parts 每段要么 {"text": ...}，要么 {"path": ...} 文件指针（不要把文件内容贴进来）。"""
            payload: dict[str, Any] = {"to": to, "parts": parts}
            if reply_to:
                payload["reply_to"] = reply_to
            return dispatch_tool("say", payload)

    if "ask" in names:

        @mcp.tool(name="ask")
        def ask(to: str, parts: list[dict[str, Any]], timeout: Optional[int] = None) -> str:
            """问另一个 Agent 并等它答（timeout 只影响你等多久，不影响它那边做了什么）。"""
            payload: dict[str, Any] = {"to": to, "parts": parts}
            if timeout:
                payload["timeout"] = timeout
            return dispatch_tool("ask", payload)

    return mcp


def run_tool_server(*, verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        stream=sys.stderr,
    )
    server = build_server()
    server.run(transport="stdio")


if __name__ == "__main__":
    run_tool_server(verbose="-v" in sys.argv or "--verbose" in sys.argv)
