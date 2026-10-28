"""Verification for the ACP leg (`quota_hub/acp.py` + `quota_hub/providers/acp.py`).

The point of this knife: a spawn-based leg could not hand a tool call back to us, so it
was not pickable (`registry.usable` needs `tools`). ACP has a session and `session/new`
takes an `mcpServers` list, which is where Plobi's own tool surface goes in. The claim
`tools=True` is honest only while that handoff actually happened — both halves are
pinned here, against a stand-in agent (`tests/fake_acp_cli.py`): no network, no
credential, no vendor CLI.

Run:
    cd Code && scripts/run_tests.sh -j 1 quota_hub/tests/test_acp_provider.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
import pytest
from quota_hub import login_ledger
from quota_hub.acp import AcpError, AcpSession, mcp_servers
from quota_hub.providers.acp import AcpProvider

FAKE = str(Path(__file__).with_name("fake_acp_cli.py"))


def _cfg(tmp_path: Path, mode: str = "", **extra) -> dict:
    return {
        "binary": sys.executable,
        "acp_args": [FAKE] + ([mode] if mode else []),
        "leg": "fake_acp",
        "models": ["fake_acp/default"],
        "timeout": 20,
        "cwd": str(tmp_path),
        **extra,
    }


def _run(coro):
    return asyncio.run(asyncio.wait_for(coro, timeout=60))


# --------------------------------------------------------------- the handoff
def test_the_tool_handoff_carries_plobi_home_as_a_name_value_array(monkeypatch, tmp_path):
    # ACP's McpServerStdio.env is a *list* of {name,value}; the codex-shaped dict would
    # be dropped silently, and a MCP child without PLOBI_HOME lands on the C: drive on
    # this machine (裁定 79).
    monkeypatch.setenv("PLOBI_HOME", str(tmp_path / "home"))

    servers = mcp_servers()

    assert servers and servers[0]["name"] == "plobi"
    env = servers[0]["env"]
    assert isinstance(env, list) and all(isinstance(item, dict) and set(item) == {"name", "value"} for item in env)
    assert {"name": "PLOBI_HOME", "value": str(tmp_path / "home")} in env


def test_the_handoff_can_be_switched_off_and_then_nothing_is_promised(monkeypatch):
    monkeypatch.setenv("PLOBI_ACP_MCP_TOOLS", "0")

    assert mcp_servers() == []


def test_a_session_that_took_our_tools_is_earned_tools_true(tmp_path):
    async def _go():
        sess = AcpSession(argv=[sys.executable, FAKE], cwd=str(tmp_path), name="fake")
        await sess.start()
        assert sess.tools_handed_over is True
        text = await sess.prompt("hi")
        assert "hello from fake acp" in text
        # The CLI used our tool twice (call + update); that is what "tools came back"
        # means, and it is counted rather than assumed.
        assert sess.tool_calls == 2
        await sess.close()

    _run(_go())


def test_streaming_hands_back_deltas_and_closes_with_stop(tmp_path):
    async def _go():
        prov = AcpProvider(_cfg(tmp_path), None)
        acc = (await prov.discover_accounts())[0]
        chunks = [chunk async for chunk in prov.chat_stream(acc, {"messages": [{"content": "hi", "role": "user"}], "model": "fake_acp/default"})]
        text = "".join((c["choices"][0]["delta"].get("content") or "") for c in chunks)
        assert "hello from fake acp" in text
        assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
        await prov.aclose()

    _run(_go())


def test_only_our_own_tool_option_is_approved(tmp_path):
    async def _go():
        sess = AcpSession(argv=[sys.executable, FAKE, "--permission"], cwd=str(tmp_path), name="fake")
        await sess.start()
        text = await sess.prompt("hi")
        # The fake offered `allow_plobi_read` (ours) and `allow_shell` (foreign); only
        # our option is selected, so the foreign one is never authorized.
        assert "chosen=allow_plobi_read" in text
        await sess.close()

    _run(_go())


def test_a_silent_cli_times_out_instead_of_hanging_the_hub(tmp_path):
    async def _go():
        sess = AcpSession(argv=[sys.executable, FAKE, "--silent"], cwd=str(tmp_path), name="fake", prompt_timeout=0.5)
        await sess.start()
        with pytest.raises(AcpError):
            await sess.prompt("hi")
        await sess.close()

    _run(_go())


# ------------------------------------------------------------ the picker view
def test_the_leg_is_pickable_only_once_its_sign_in_was_watched(tmp_path):
    async def _go():
        cfg = {
            "providers": {"fake_acp": {**_cfg(tmp_path), "transport": "acp"}},
            "routing": {"rules": []},
            "server": {"api_key": "k"},
        }
        async with httpx.AsyncClient() as http:
            from quota_hub.registry import Registry

            reg = await Registry(cfg, http).build()
            # Same gate as every other leg: signed in AND tools, nothing else.
            assert reg.list_models(selectable_only=True) == []

            login_ledger.remember("fake_acp")
            rows = {row["id"]: row for row in reg.list_models(selectable_only=True)}

        assert "fake_acp/default" in rows
        assert rows["fake_acp/default"]["capabilities"]["tools"] is True

    _run(_go())