"""Hand the ACP CLI a real tool surface instead of asking it to print JSON.

Today the ACP legs "support tools" by putting this instruction in the prompt
(`agent/copilot_acp_client.py`): *"you MUST output tool calls using
<tool_call>{...}</tool_call> blocks"* — i.e. the model pretends to call a function
and we regex it back out of prose. That is the thing the owner keeps calling
degradation: it is neither the CLI's native protocol nor something a strict
endpoint guarantees.

ACP already has the right shape: `session/new` takes `mcpServers`, and Plobi
already runs an MCP server exposing its own tools
(`agent/transports/plobi_quota_mcp_server.py`, stdio). Wiring one to the other
means the CLI calls Plobi's tools over MCP and the results come back as real
tool results.

The wire detail that bites: ACP's `McpServerStdio.env` is an array of
`{name, value}` pairs, while `render_mcp_config()` returns a dict — passing the
dict through would silently drop `PLOBI_HOME`, and the child server would then
read the wrong home.
"""

from __future__ import annotations

import sys

from agent.copilot_acp_client import _acp_mcp_servers


def test_acp_mcp_servers_uses_the_name_value_array_shape():
    servers = _acp_mcp_servers()

    assert servers, "Plobi's own MCP server must be offered to the ACP agent"
    server = servers[0]
    assert server["name"]
    assert server["command"] == sys.executable
    assert server["args"], "the -m module argv must be passed"
    # ACP wants [{name, value}], not {"KEY": "value"} — a dict here is dropped.
    assert isinstance(server["env"], list)
    assert all(isinstance(entry, dict) and {"name", "value"} <= set(entry) for entry in server["env"])
    names = {entry["name"] for entry in server["env"]}
    assert "PLOBI_HOME" in names, "the child must read the same home Plobi is running against"


def test_acp_mcp_servers_never_carries_a_secret_it_does_not_need(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-be-forwarded")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_should-not-be-forwarded")

    names = {entry["name"] for entry in _acp_mcp_servers()[0]["env"]}

    assert "ANTHROPIC_API_KEY" not in names
    assert "GITHUB_TOKEN" not in names


def test_acp_mcp_servers_can_be_turned_off_for_one_provider(monkeypatch):
    monkeypatch.setenv("PLOBI_ACP_MCP_TOOLS", "0")

    assert _acp_mcp_servers() == []
