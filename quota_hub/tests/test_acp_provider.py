"""Verification for the ACP leg (`quota_hub/acp.py` + `quota_hub/providers/acp.py`).

The point of this knife: a spawn-based leg could not hand a tool call back to us, so it
was not pickable (`registry.usable` needs `tools`). ACP has a session and `session/new`
takes an `mcpServers` list, which is where Plobi's own tool surface goes in. The claim
`tools=True` is honest only while that handoff actually happened — both halves are
pinned here, against a stand-in agent (`tests/fake_acp_cli.py`): no network, no
credential, no vendor CLI.

Run:
    cd Code && scripts/run_tests.sh -j 1 quota_hub/tests/test_acp_provider.py

The second half this knife pins (measured on cline 3.0.74, 2026-10-29): the CLI's own
`session/new` is the credential question, and the same answer carries the models that
session can run. A leg therefore claims a sign-in and a model list only from those bytes —
never from our login ledger, and never from a name typed into config.yaml.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import httpx
import pytest
from quota_hub.acp import AcpError, AcpSession, mcp_servers
from quota_hub.providers.acp import AcpProvider

FAKE = str(Path(__file__).with_name("fake_acp_cli.py"))


def _cfg(tmp_path: Path, mode: str = "", **extra) -> dict:
    return {
        "binary": sys.executable,
        "acp_args": [FAKE] + ([mode] if mode else []),
        "leg": "fake_acp",
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
        await prov.refresh_models(acc)
        chunks = [chunk async for chunk in prov.chat_stream(acc, {"messages": [{"content": "hi", "role": "user"}], "model": "fake_acp/m-b"})]
        text = "".join((c["choices"][0]["delta"].get("content") or "") for c in chunks)
        assert "hello from fake acp on m-b" in text
        assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
        await prov.aclose()

    _run(_go())


def test_only_our_own_tool_option_is_approved(tmp_path):
    async def _go():
        sess = AcpSession(argv=[sys.executable, FAKE, "--permission"], cwd=str(tmp_path), name="fake")
        await sess.start()
        text = await sess.prompt("hi")
        # The fake asks with the generic option ids and names *our* tool in toolCall;
        # the narrowest allow must be selected. A foreign tool is refused by the unit
        # test next to this one, where the request shape is spelled out verbatim.
        assert "chosen=allow_once" in text, text
        await sess.close()

    _run(_go())


def test_a_cli_that_drops_the_first_handshake_is_not_written_off(tmp_path):
    """Measured 2026-10-29: `qwen --acp` does not read stdin for ~20 s, so the first
    `initialize` is swallowed. Silence is not a refusal — the client resends."""

    async def _go():
        sess = AcpSession(
            argv=[sys.executable, FAKE, "--slow-init"],
            cwd=str(tmp_path),
            name="slow",
            start_timeout=10.0,
            init_attempt_timeout=1.0,
        )
        await sess.start()
        assert sess.session_id == "fake-session"
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
def _registry(http, tmp_path: Path, mode: str = ""):
    from quota_hub.registry import Registry

    cfg = {
        "providers": {"fake_acp": {**_cfg(tmp_path, mode), "transport": "acp"}},
        "routing": {"rules": []},
        "server": {"api_key": "k"},
    }
    return Registry(cfg, http)


def test_the_cli_opens_a_session_so_the_leg_is_pickable_with_its_own_models(tmp_path):
    async def _go():
        async with httpx.AsyncClient() as http:
            reg = await _registry(http, tmp_path).build()
            # The handshake is the model-refresh task's job, not build()'s — see below.
            await reg.ensure_models_refreshed()
            rows = {row["id"]: row for row in reg.list_models(selectable_only=True)}
        # The names are the CLI's (`session/new` answered m-a/m-b), not a config list.
        assert sorted(rows) == ["fake_acp/m-a", "fake_acp/m-b"]
        assert rows["fake_acp/m-a"]["capabilities"]["tools"] is True
        # Same gate as every other leg: usable = the CLI's session answer AND tools.
        assert rows["fake_acp/m-a"]["live"] is True

    _run(_go())


def test_a_slow_cli_does_not_hold_the_hub_off_the_port(tmp_path):
    """build() must not wait for the handshake: measured 9.8s for cline 3.0.74 alone."""

    async def _go():
        prov = AcpProvider(_cfg(tmp_path), None)
        started = time.monotonic()
        accounts = await prov.discover_accounts()
        assert len(accounts) == 1
        assert time.monotonic() - started < 1.0, "discover_accounts spawned the CLI"
        assert prov.credential_health() == (False, "not_probed")
        await prov.refresh_models(accounts[0])
        assert prov.credential_health() == (True, "ok")

    _run(_go())


def test_an_unsigned_cli_offers_no_models_and_no_switch(tmp_path):
    async def _go():
        prov = AcpProvider(_cfg(tmp_path, "--needs-auth"), None)
        acc = (await prov.discover_accounts())[0]
        assert await prov.refresh_models(acc) is None
        assert prov.credential_health() == (False, "not_signed_in")
        assert prov.served_models == ()

        async with httpx.AsyncClient() as http:
            reg = await _registry(http, tmp_path, "--needs-auth").build()
            await reg.ensure_models_refreshed()
        assert reg.list_models(selectable_only=True) == []

    _run(_go())


def test_a_sign_in_over_there_flips_the_card_without_a_restart(tmp_path):
    """The one moment he actually watches: he signs in, the `+` must become a switch.

    The hub re-reads a leg right after a login (`_recheck_after_login`); for an ACP leg
    that means a fresh handshake, not the cached "not signed in". The stand-in stays
    unsigned until a marker file appears, so writing it here *is* the sign-in.
    """

    marker = tmp_path / "signed-in"

    async def _go():
        prov = AcpProvider(_cfg(tmp_path, f"--auth-file={marker}"), None)
        acc = (await prov.discover_accounts())[0]
        assert await prov.refresh_models(acc) is None
        assert prov.credential_health() == (False, "not_signed_in")
        assert prov.served_models == ()

        marker.write_text("yes", encoding="utf-8")
        shorts = await prov.refresh_models(acc)

        assert shorts == ["m-a", "m-b"]
        assert prov.credential_health() == (True, "ok")
        assert prov.served_models == ("fake_acp/m-a", "fake_acp/m-b")

    _run(_go())


def test_the_post_login_recheck_puts_the_models_into_the_catalog(tmp_path):
    """The whole point of `_recheck_after_login`: one sign-in, no restart.

    This drives the real function the route calls after a login — discover → refresh →
    rebuild the routing index — against the stand-in agent, so the promise "点一下登录，
    模型就进框" is checked end to end instead of only at the provider level.
    """

    marker = tmp_path / "signed-in"

    async def _go():
        from quota_hub import main as hubmain

        async with httpx.AsyncClient() as http:
            reg = await _registry(http, tmp_path, f"--auth-file={marker}").build()
            hubmain.STATE["reg"] = reg
            await reg.ensure_models_refreshed()
            assert reg.list_models(selectable_only=True) == []

            marker.write_text("yes", encoding="utf-8")  # he finished the CLI's sign-in
            await hubmain._recheck_after_login("fake_acp")

            ids = [row["id"] for row in reg.list_models(selectable_only=True)]
            routed = dict(reg.model_to_provider)

        assert ids == ["fake_acp/m-a", "fake_acp/m-b"]
        assert routed == {"fake_acp/m-a": "fake_acp", "fake_acp/m-b": "fake_acp"}

    _run(_go())


def test_a_turn_runs_on_the_model_the_picker_chose(tmp_path):
    async def _go():
        prov = AcpProvider(_cfg(tmp_path), None)
        acc = (await prov.discover_accounts())[0]
        await prov.refresh_models(acc)
        out = await prov.chat(acc, {"messages": [{"role": "user", "content": "hi"}], "model": "fake_acp/m-b"})
        assert "on m-b" in out["choices"][0]["message"]["content"]
        await prov.aclose()

    _run(_go())


def test_a_cli_that_will_not_switch_models_serves_only_the_one_it_is_on(tmp_path):
    async def _go():
        prov = AcpProvider(_cfg(tmp_path, "--no-set-model"), None)
        acc = (await prov.discover_accounts())[0]
        await prov.refresh_models(acc)
        # m-b was reported, but a leg that cannot switch must not offer it: picking it
        # would answer from a different model than the one chosen.
        assert prov.served_models == ("fake_acp/m-a",)
        with pytest.raises(Exception) as exc:
            await prov.chat(acc, {"messages": [{"role": "user", "content": "hi"}], "model": "fake_acp/m-b"})
        assert getattr(exc.value, "status", 0) in (400, 404)
        await prov.aclose()

    _run(_go())


def test_a_cli_that_greets_with_a_banner_is_not_a_broken_session(tmp_path):
    """Measured 2026-10-28: `opencode --acp` (wrong subcommand) printed its help text on
    stdout and this client died on the first line. A banner is normal; dying on it is not."""

    async def _go():
        sess = AcpSession(argv=[sys.executable, FAKE, "--banner"], cwd=str(tmp_path), name="fake")
        await sess.start()
        text = await sess.prompt("hi")
        assert "hello from fake acp" in text
        await sess.close()

    _run(_go())


def test_a_second_turn_sends_only_what_the_session_has_not_seen(tmp_path):
    """A stateful session used to be handed the whole conversation on every turn: turn N
    re-sent turns 1..N-1, so the model saw its own earlier answer quoted back at it (and
    the context grew every turn). Measured 2026-10-29 from the code, before this pin."""

    async def _go():
        prov = AcpProvider(_cfg(tmp_path), None)
        acc = (await prov.discover_accounts())[0]
        await prov.refresh_models(acc)

        first = [{"role": "user", "content": "hello"}]
        _sess, text1, _msgs = await prov._prepare(acc, {"model": "fake_acp/m-a", "messages": first})
        assert text1 == "user: hello"
        prov._sent[acc.id] = first

        second = first + [
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "again"},
        ]
        _sess, text2, _msgs = await prov._prepare(acc, {"model": "fake_acp/m-a", "messages": second})
        assert text2 == "assistant: hi\n\nuser: again"

        await prov.aclose()

    _run(_go())


def test_a_rewound_history_restarts_the_session(tmp_path):
    """The client compacts or starts over: the prefix no longer matches, so the old
    session (which still holds turns we are not sending) is replaced, not reused."""

    async def _go():
        prov = AcpProvider(_cfg(tmp_path), None)
        acc = (await prov.discover_accounts())[0]
        await prov.refresh_models(acc)

        first = [{"role": "user", "content": "one"}]
        sess, _text, _msgs = await prov._prepare(acc, {"model": "fake_acp/m-a", "messages": first})
        await sess.start()
        pid_before = sess.proc.pid
        prov._sent[acc.id] = first

        other = [{"role": "user", "content": "totally different"}]
        sess2, text2, _msgs = await prov._prepare(acc, {"model": "fake_acp/m-a", "messages": other})

        assert sess2 is sess                       # same seat, new child
        assert sess2.proc.pid != pid_before        # it was really replaced
        assert text2 == "user: totally different"  # and the whole history went in

        await prov.aclose()

    _run(_go())


def test_a_retry_of_the_same_history_asks_the_last_question_again(tmp_path):
    """A client that times out and re-sends the identical history must not produce an
    empty prompt — the session has nothing new, but a retry is still a question."""

    async def _go():
        prov = AcpProvider(_cfg(tmp_path), None)
        acc = (await prov.discover_accounts())[0]
        await prov.refresh_models(acc)

        msgs = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "answer"},
            {"role": "user", "content": "second"},
        ]
        # The session must be alive before "already sent" means anything: a dead one is
        # respawned empty (see the respawn test).
        await prov._prepare(acc, {"model": "fake_acp/m-a", "messages": msgs[:1]})
        prov._sent[acc.id] = msgs

        _sess, text, _msgs = await prov._prepare(acc, {"model": "fake_acp/m-a", "messages": msgs})
        assert text == "user: second"

        await prov.aclose()

    _run(_go())


def test_a_rebuilt_system_prompt_does_not_restart_the_session(tmp_path):
    """Plobi rebuilds its system prompt every turn (a date, the current state). Counting
    that as a rewind would restart the CLI — and pay its boot — on every turn."""
    async def _go():
        prov = AcpProvider(_cfg(tmp_path), None)
        acc = (await prov.discover_accounts())[0]
        await prov.refresh_models(acc)

        first = [
            {"role": "system", "content": "today is Monday"},
            {"role": "user", "content": "hello"},
        ]
        sess, text1, _ = await prov._prepare(acc, {"model": "fake_acp/m-a", "messages": first})
        await sess.start()
        pid_before = sess.proc.pid
        prov._sent[acc.id] = first

        second = [
            {"role": "system", "content": "today is Tuesday"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "and again"},
        ]
        sess2, text2, _ = await prov._prepare(acc, {"model": "fake_acp/m-a", "messages": second})

        assert sess2.proc.pid == pid_before        # same child: no restart
        assert text2 == "assistant: hi\n\nuser: and again"

        await prov.aclose()

    _run(_go())


def test_a_respawned_session_gets_the_whole_conversation_again(tmp_path):
    """A dead child has no memory. Keeping the "already sent" bookkeeping would hand the
    fresh session only the tail, and the model would answer without the conversation."""

    async def _go():
        prov = AcpProvider(_cfg(tmp_path), None)
        acc = (await prov.discover_accounts())[0]
        await prov.refresh_models(acc)

        msgs = [
            {"role": "user", "content": "one"},
            {"role": "assistant", "content": "two"},
        ]
        sess, _text, _m = await prov._prepare(acc, {"model": "fake_acp/m-a", "messages": msgs})
        await sess.start()
        prov._sent[acc.id] = msgs

        await sess.close()  # the child dies between turns

        _sess, text, _m = await prov._prepare(
            acc,
            {"model": "fake_acp/m-a", "messages": msgs + [{"role": "user", "content": "three"}]},
        )
        expected = "\n\n".join(["user: one", "assistant: two", "user: three"])
        assert text == expected

        await prov.aclose()

    _run(_go())


def test_opaque_internal_model_ids_expose_human_vendor_slugs_and_map_both(tmp_path):
    """Measured 2026-10-29 on `qodercli --acp`: `session/new` reports opaque internal
    ids (`smodel`, `qmodel_38max`, `kmodel_latest`) alongside human vendor names
    (`Sonus`, `Qwen3.8-Max`, `Kimi-K3.1-Pro`). The picker gets the vendor slugs while
    `model_map` resolves both `<prefix>/<slug>` and `<prefix>/<raw_id>` to `<raw_id>`."""
    prov = AcpProvider({**_cfg(tmp_path), "model_prefix": "qoder"}, None)
    prov._probe = {
        "probed": True,
        "signed_in": True,
        "switch": True,
        "current": "auto",
        "models": [
            {"id": "auto", "name": "Auto (default)"},
            {"id": "smodel", "name": "Sonus"},
            {"id": "qmodel_38max", "name": "Qwen3.8-Max"},
        ],
    }
    shorts = prov._apply_probed_models()
    assert shorts == ["auto", "sonus", "qwen3.8-max"]
    assert prov.served_models == ("qoder/auto", "qoder/sonus", "qoder/qwen3.8-max")
    assert prov.model_map["qoder/sonus"] == "smodel"
    assert prov.model_map["qoder/smodel"] == "smodel"
    assert prov.model_map["qoder/qwen3.8-max"] == "qmodel_38max"



def test_is_our_tool_reads_the_call_not_the_option_id():
    """The names our MCP server exposes carry plobi; the CLI's own tools do not."""
    from quota_hub.acp import _is_our_tool

    assert _is_our_tool({"toolCallId": "plobi_terminal_0", "title": "plobi_terminal: ls"}) is True
    # Verbatim shape captured from Cline on 2026-10-30: it went looking for our tool in
    # its own Composio catalogue — that is not ours, and must stay refused.
    assert _is_our_tool({
        "toolCallId": "composio_search_tools_0_615a3dcc",
        "title": 'composio_search_tools: {"queries":[{"use_case":"use the get_time tool',
    }) is False
    assert _is_our_tool({}) is False
    assert _is_our_tool(None) is False


@pytest.mark.asyncio
async def test_a_permission_request_for_our_tool_is_allowed_once():
    from quota_hub.acp import AcpSession

    class _Stub:
        name = "stub"

        def __init__(self):
            self.sent = []

        async def _send(self, msg):
            self.sent.append(msg)

    stub = _Stub()
    await AcpSession._answer_permission(stub, {
        "id": 7,
        "params": {
            "options": [
                {"optionId": "allow_once", "name": "Allow once", "kind": "allow_once"},
                {"optionId": "allow_always", "name": "Allow always", "kind": "allow_always"},
                {"optionId": "reject_once", "name": "Reject", "kind": "reject_once"},
            ],
            "toolCall": {"toolCallId": "plobi_process_0", "title": "plobi_process: run ls", "kind": "other"},
        },
    })
    outcome = stub.sent[0]["result"]["outcome"]
    assert outcome["outcome"] == "selected"
    assert outcome["optionId"] == "allow_once", "取最窄的那一枚，不用 allow_always"


@pytest.mark.asyncio
async def test_a_permission_request_for_someone_elses_tool_is_cancelled():
    """The refusal is deliberate: the CLI's own executor is not authorized by us."""
    from quota_hub.acp import AcpSession

    class _Stub:
        name = "stub"

        def __init__(self):
            self.sent = []

        async def _send(self, msg):
            self.sent.append(msg)

    stub = _Stub()
    await AcpSession._answer_permission(stub, {
        "id": 8,
        "params": {
            "options": [{"optionId": "allow_once", "kind": "allow_once"}],
            "toolCall": {"toolCallId": "composio_search_tools_0_x", "title": "composio_search_tools: {}", "kind": "other"},
        },
    })
    assert stub.sent[0]["result"]["outcome"]["outcome"] == "cancelled"
