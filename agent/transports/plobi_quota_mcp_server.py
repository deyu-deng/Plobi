"""Quota-line MCP server: expose Plobi's own tools to an EXTERNAL agent CLI
(e.g. WorkBuddy / CodeBuddy) that runs on its own subscription quota but wants
to call Plobi's capabilities — the OpenDesign "drive the external agent, hand
it our tools" pattern, the compliant way.

Why a second server instead of reusing ``plobi_tools_mcp_server`` (the codex one):
that module registers tools through FastMCP, which derives each tool's JSON
schema from the Python function signature. Our handler is ``**kwargs``, so the
advertised schema collapses to a bogus required ``kwargs`` field — an external
MCP client passing real arguments is rejected (measured 2026-10-07:
``skills_list`` -> "kwargs Field required"). Codex tolerates it; a generic MCP
client (CodeBuddy) does not. This module uses the low-level ``Server`` whose
``Tool`` accepts a raw ``inputSchema``, so each tool advertises Plobi's ACTUAL
OpenAI function schema — the same parameters Plobi gives its own model.

Single source: the allowlist is imported from the codex module, never copied —
so the two servers can't drift on WHICH tools are exposed. Only the registration
mechanism differs.

Run (stdio, for any MCP client):
    python -m agent.transports.plobi_quota_mcp_server
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Optional

# Make the server cwd-independent: an external MCP client (CodeBuddy) spawns
# `python -m agent.transports.plobi_quota_mcp_server` without guaranteeing the
# repo root is the working directory or on sys.path. Put it there ourselves so
# `model_tools` / `agent.*` always import.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Base curated set is shared with the codex server (import, don't copy — so the
# read-only / web / browser / skill / kanban capabilities can't drift between the
# two). On top of it the quota line ALSO exposes the real-work file/shell tools,
# because an external agent driving Plobi's tools is the whole point here.
#
# Deliberately EXCLUDED even though the user asked for them: `_AGENT_LOOP_TOOLS`
# {todo, memory, session_search, delegate_task} — model_tools.handle_function_call
# refuses them statelessly ("must be handled by the agent loop", model_tools.py:606
# / :1167). Exposing them would only hand the external agent a guaranteed error.
# A live-agent-runtime-backed server is a separate, larger slice if we ever want
# those.
from model_tools import _AGENT_LOOP_TOOLS

from agent.transports.plobi_tools_mcp_server import EXPOSED_TOOLS as _CODEX_EXPOSED

#: Tools an external quota-backed agent may drive to do real work on this box.
#: Shell/file writes here are gated by Plobi's pre_tool_call approval hooks
#: (fail-closed) — granting a cloud-inferred agent terminal access is intentional
#: and the operator accepts that blast radius.
REAL_WORK_TOOLS: tuple[str, ...] = (
    "terminal",
    "read_file",
    "write_file",
    "patch",
    "search_files",
    "process",
    "execute_code",
)

EXPOSED_TOOLS: tuple[str, ...] = tuple(
    sorted((set(_CODEX_EXPOSED) | set(REAL_WORK_TOOLS)) - set(_AGENT_LOOP_TOOLS))
)

logger = logging.getLogger("plobi.quota.mcp")


def render_mcp_config(module: str = "agent.transports.plobi_quota_mcp_server") -> dict:
    """The stdio entry an EXTERNAL MCP client (e.g. CodeBuddy `mcp add-json` /
    `--mcp-config`) should use to launch Plobi's tool surface.

    Mirrors ``plobi_cli.codex_runtime_plugin_migration._build_plobi_tools_mcp_entry``
    on purpose — same ``sys.executable`` + pass-through of ``PLOBI_HOME`` /
    ``PYTHONPATH`` read from ``os.environ`` (never the resolved default, so a
    launcher's profile choice wins over anything baked here). Kept independent of
    the ``mcp`` package so ``--print-config`` works in a bare checkout.
    """
    env: dict[str, str] = {
        "PLOBI_QUIET": "1",
        "PLOBI_REDACT_SECRETS": "true",
    }
    plobi_home = os.environ.get("PLOBI_HOME", "").strip()
    if plobi_home:
        env["PLOBI_HOME"] = plobi_home
    pythonpath = os.environ.get("PYTHONPATH", "").strip()
    if pythonpath:
        env["PYTHONPATH"] = pythonpath
    return {
        "command": sys.executable,
        "args": ["-m", module],
        "env": env,
    }


def print_config() -> int:
    """Emit the client-ready ``--mcp-config`` JSON to stdout and exit 0.

    ``plobi_quota_mcp_server --print-config`` -> paste straight into
    ``codebuddy mcp add-json plobi '<that JSON>'``.
    """
    entry = render_mcp_config()
    print(json.dumps({"mcpServers": {"plobi": entry}}, ensure_ascii=False, indent=2))
    return 0


def _tool_specs() -> dict[str, dict[str, Any]]:
    """Authoritative Plobi tool schemas (name -> {description, parameters}).

    Read live from the registry so an external client sees the SAME parameter
    docs Plobi's own model sees — the single-source rule for the tool surface.
    Tools whose ``check_fn`` fails in this process are omitted (they can't be
    dispatched anyway); ``get_tool_definitions`` is quiet + never raises.
    """
    from model_tools import get_tool_definitions

    out: dict[str, dict[str, Any]] = {}
    for td in get_tool_definitions(quiet_mode=True) or []:
        if not isinstance(td, dict) or td.get("type") != "function":
            continue
        fn = td["function"]
        name = fn.get("name")
        if name in EXPOSED_TOOLS and isinstance(name, str):
            out[name] = {
                "description": fn.get("description") or f"Plobi {name} tool",
                "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
            }
    return out


def build_server():
    """Low-level MCP ``Server`` wired to Plobi's tool engine.

    Uses ``mcp.server.lowlevel.Server`` precisely because its ``Tool`` model
    takes a raw ``inputSchema`` — we pass Plobi's real JSON schema straight
    through instead of letting FastMCP re-derive it from a function signature.
    """
    import mcp.types as mcp_types
    from mcp.server.lowlevel import Server

    specs = _tool_specs()
    server = Server("plobi-tools")

    @server.list_tools()
    async def _list_tools() -> list[mcp_types.Tool]:
        return [
            mcp_types.Tool(
                name=name,
                description=spec["description"],
                inputSchema=spec["parameters"],
            )
            for name, spec in specs.items()
        ]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any]) -> list[mcp_types.TextContent]:
        from model_tools import handle_function_call

        if name not in specs:
            return [
                mcp_types.TextContent(
                    type="text", text=f'{{"error": "tool {name!r} not exposed"}}'
                )
            ]
        try:
            # The codex server has no live AIAgent either; dispatch the same
            # stateless way it does. Agent-loop tools (delegate_task/memory/…)
            # are NOT in EXPOSED_TOOLS precisely because they need loop state.
            result = await asyncio.to_thread(handle_function_call, name, arguments or {})
        except Exception as exc:  # noqa: BLE001 — surface as tool error, not crash
            logger.exception("tool %s raised", name)
            import json as _json

            result = _json.dumps({"error": str(exc), "tool": name})
        return [mcp_types.TextContent(type="text", text=result)]

    init_options = server.create_initialization_options()
    return server, init_options


def main(argv: Optional[list[str]] = None) -> int:
    argv = argv or sys.argv[1:]
    # Print the client-ready config and exit BEFORE importing mcp / building the
    # server, so `--print-config` works in a bare checkout and never touches the
    # stdio wire.
    if "--print-config" in argv:
        return print_config()

    verbose = "--verbose" in argv or "-v" in argv
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        stream=sys.stderr,  # stdout is the MCP wire; logs MUST go to stderr
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    os.environ.setdefault("PLOBI_QUIET", "1")
    os.environ.setdefault("PLOBI_REDACT_SECRETS", "true")

    server, init_options = build_server()

    async def _run() -> None:
        from mcp.server.stdio import stdio_server

        async with stdio_server() as (read, write):
            await server.run(read, write, init_options)

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
