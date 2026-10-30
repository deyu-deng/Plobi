"""`WP-TOOLS-MCP`：把 say / ask 暴露成 MCP 工具。

钉四件：
1. 默认暴露面只有 `say` 与 `ask`（裁 185：工具面只给原子工具）；
2. `PLOBI_MCP_TOOLS` 可以显式扩展；
3. `tools/call` 走的是**现成的** `tools.registry.dispatch`，不复制发送逻辑；
4. MCP 看到的参数形状与 Plobi 工具面一致（to / parts / reply_to；ask 多 timeout）。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import mcp_tools_serve as M  # noqa: E402


def test_default_surface_is_only_say_and_ask(monkeypatch):
    monkeypatch.delenv("PLOBI_MCP_TOOLS", raising=False)
    assert M.exposed_tool_names() == ("say", "ask")


def test_env_can_widen_the_surface(monkeypatch):
    monkeypatch.setenv("PLOBI_MCP_TOOLS", "say, ask ,agenda_read")
    assert M.exposed_tool_names() == ("say", "ask", "agenda_read")


def test_dispatch_goes_through_the_real_registry(monkeypatch):
    seen: list[tuple] = []

    class _Reg:
        def dispatch(self, name, args, **kwargs):
            seen.append((name, args, kwargs))
            return json.dumps({"ok": True})

    monkeypatch.setitem(sys.modules, "tools.registry", type("m", (), {"registry": _Reg()})())
    monkeypatch.setenv("PLOBI_MCP_SESSION_ID", "sess-x")

    out = M.dispatch_tool("say", {"to": "agenda", "parts": [{"text": "hi"}]})

    assert json.loads(out)["ok"] is True
    assert seen[0][0] == "say"
    assert seen[0][1] == {"to": "agenda", "parts": [{"text": "hi"}]}
    assert seen[0][2]["session_id"] == "sess-x"


def test_the_mcp_surface_exposes_two_tools_with_the_plobi_arguments():
    server = M.build_server()
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}

    assert set(tools) == {"say", "ask"}
    say_schema = tools["say"].inputSchema
    assert set(say_schema["properties"]) == {"to", "parts", "reply_to"}
    assert say_schema["required"] == ["to", "parts"]
    ask_schema = tools["ask"].inputSchema
    assert "timeout" in ask_schema["properties"]
