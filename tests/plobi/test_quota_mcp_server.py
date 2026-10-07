"""Invariants for the quota-line MCP server (plobi_quota_mcp_server).

Zero network, zero model, zero live tools. These guard the two load-bearing
rules discovered on 2026-10-07:

  1. Agent-loop tools (todo/memory/session_search/delegate_task) must NEVER be
     advertised — an external client driving them statelessly gets only the
     "must be handled by the agent loop" error, so exposing them is a trap.
  2. A real-work tool that IS registered must advertise Plobi's ACTUAL parameter
     schema, not the bogus required top-level ``kwargs`` field the FastMCP codex
     server produced by signature-reflection. That bug is the entire reason this
     second server exists.
"""

from __future__ import annotations

import logging
import sys

import pytest

logging.disable(logging.CRITICAL)

from agent.transports import plobi_quota_mcp_server as S
from model_tools import _AGENT_LOOP_TOOLS


def test_agent_loop_tools_are_never_exposed():
    # The whole point: an external MCP client cannot drive these statelessly.
    assert not (set(S.EXPOSED_TOOLS) & set(_AGENT_LOOP_TOOLS))


def test_real_work_tools_are_in_the_allowlist():
    # 含写+终端 was an explicit operator decision — pin the surface so a later
    # refactor can't silently drop terminal/file access from the quota line.
    for name in ("terminal", "read_file", "write_file", "patch", "search_files"):
        assert name in S.EXPOSED_TOOLS, name


def test_base_codex_set_is_still_carried():
    # Single source: the curated read-only/web/skill set is imported, not copied,
    # so the two servers can't drift. At least one codex-only tool must survive.
    from agent.transports.plobi_tools_mcp_server import EXPOSED_TOOLS as codex

    assert set(codex) - set(_AGENT_LOOP_TOOLS) <= set(S.EXPOSED_TOOLS)


def test_advertised_schema_is_plobis_not_signature_derived():
    """If a real-work tool is registered in THIS process, its advertised schema
    must be Plobi's JSON (no fabricated top-level required ``kwargs``).

    ``get_tool_definitions`` is env/credential-gated, so which tools register
    varies; assert only over the ones that actually came back.
    """
    specs = S._tool_specs()
    if not specs:
        pytest.skip("no exposed tools registered in this bare process")
    for name, spec in specs.items():
        params = spec["parameters"]
        assert isinstance(params, dict), name
        # The bug we fixed: FastMCP turned ``**kwargs`` into a required field
        # literally named "kwargs". Plobi's real schemas never do that.
        assert "kwargs" not in (params.get("required") or []), name
        assert params.get("type") in (None, "object"), name


def test_render_mcp_config_targets_this_module_with_current_python():
    # The paste-ready entry an external MCP client (CodeBuddy) uses. It must run
    # the CURRENT interpreter (venv / worktree / install all work) and launch
    # THIS module — not the codex one.
    cfg = S.render_mcp_config()
    assert cfg["command"] == sys.executable
    assert cfg["args"] == ["-m", "agent.transports.plobi_quota_mcp_server"]


def test_render_mcp_config_passes_through_env_without_burning_default(monkeypatch):
    # Mirrors the codex builder: PLOBI_HOME is forwarded only if the ENVIRONMENT
    # sets it, so a launcher's profile choice wins over anything baked here.
    monkeypatch.delenv("PLOBI_HOME", raising=False)
    assert "PLOBI_HOME" not in S.render_mcp_config()["env"]
    monkeypatch.setenv("PLOBI_HOME", "/somewhere/real")
    assert S.render_mcp_config()["env"]["PLOBI_HOME"] == "/somewhere/real"
