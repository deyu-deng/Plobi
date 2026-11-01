"""Kiro 这条腿的两条新判据：ACP 事件流要解析对，`tools=True` 只有在我们的工具面真的交出去之后才许说。

活证据（这台 2026-10-28 一次真跑，0.2 量级 credit）：`kiro-cli mcp add` 之后，事件流里出现

    "sessionUpdate":"tool_call" ... "title":"Running: @plobi/process"
    "sessionUpdate":"tool_call" ... "title":"Running: @plobi/terminal"

也就是它调的是 **Plobi 的** 工具、结果从我们这边回去——这才是 `tools=True` 的凭据；
配置里写一行不算（`registry.py` 那道 `usable = live and bool(caps.tools)` 是 owner 的裁定）。

本文件的"是否已注册"一律走假 CLI 脚本，不碰真 kiro-cli。

Run:
    cd Code && scripts/run_tests.sh -j 1 quota_hub/tests/test_kiro_stream.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from quota_hub.providers.apps.kiro.cli import KiroCliProvider, parse_stream_json

RUN = "\n".join([
    json.dumps({"type": "runStarted", "data": {"payloadSchema": "acp", "acpProtocolVersion": 1, "engine": "v2"}}),
    json.dumps({"type": "metadata", "data": {"sessionId": "s1", "contextUsagePercentage": 15.0}}),
    json.dumps({"type": "sessionUpdate", "data": {"sessionId": "s1", "update": {
        "sessionUpdate": "tool_call", "toolCallId": "t1", "title": "Running: @plobi/process", "status": "pending"}}}),
    json.dumps({"type": "sessionUpdate", "data": {"sessionId": "s1", "update": {
        "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "输出是 plobi-tool-ok"}}}}),
    json.dumps({"type": "metadata", "data": {"sessionId": "s1", "contextUsagePercentage": 22.6,
                                             "meteringUsage": [{"value": 0.2264, "unit": "credit"}]}}),
    json.dumps({"type": "runFinished", "data": {"sessionId": "s1", "status": "success",
                                                "stopReason": "end_turn", "finalText": "输出是 plobi-tool-ok"}}),
])


def test_the_acp_stream_yields_text_tools_and_credits():
    parsed = parse_stream_json(RUN)

    assert parsed["shaped"] is True
    assert parsed["text"] == "输出是 plobi-tool-ok"
    assert [e["title"] for e in parsed["tool_events"]] == ["Running: @plobi/process"]
    assert round(parsed["credits"], 4) == 0.2264
    assert parsed["context_percent"] == 22.6


def test_the_streamed_text_wins_over_the_terminal_summary():
    """实测 `finalText` 会漏进模型自己的控制标记，流式文本才是给用户看的那份。"""
    noisy = RUN.replace('"finalText": "输出是 plobi-tool-ok"', '"finalText": "输出是 plobi-tool-ok<｜DSML｜function_calls"')

    parsed = parse_stream_json(noisy)

    assert parsed["text"] == "输出是 plobi-tool-ok"
    assert "DSML" not in parsed["text"]


def test_an_old_plain_text_answer_still_parses():
    parsed = parse_stream_json("Thinking...\n\nHere is your answer: 42\n")

    assert parsed["shaped"] is False
    assert "42" in parsed["text"]


def _provider(monkeypatch, mcp_output: str) -> KiroCliProvider:
    prov = KiroCliProvider({"binary": "kiro-cli"}, None)
    prov.binary_path = r"C:\fake\kiro-cli.exe"
    monkeypatch.setattr(KiroCliProvider, "_binary_present", lambda self: True)
    # 逐个实例打桩：打在类上会让后一个 provider 的替身盖掉前一个的。
    monkeypatch.setattr(prov, "_run_mcp", lambda argv: mcp_output)
    return prov


def test_tools_are_only_claimed_once_our_mcp_server_is_registered(monkeypatch):
    wired = _provider(monkeypatch, "\n🤖 default:\n\n  plobi\n    (stdio)\n")
    plain = _provider(monkeypatch, "\n🤖 default:\n\n  kiro_default\n    (empty)\n")

    assert wired.capabilities().as_dict()["tools"] is True
    assert plain.capabilities().as_dict()["tools"] is False


def test_the_registration_command_is_the_cli_own_mcp_add(monkeypatch):
    """`kiro-cli mcp add` 是它自己文档里的非交互入口；不是我们改它的配置文件。"""
    calls: list[list[str]] = []
    prov = _provider(monkeypatch, "")

    def _record(argv):
        calls.append(list(argv))
        return "" if len(calls) == 1 else "  plobi\n"

    monkeypatch.setattr(prov, "_run_mcp", _record)

    assert prov.mcp_registered() is True
    assert calls[0][:2] == ["mcp", "list"], "先看有没有注册过，才谈得上写"
    add = next(call for call in calls if call[:2] == ["mcp", "add"])
    assert add[2:5] == ["--name", "plobi", "--scope"]
    assert "--force" in add and "--command" in add
    assert any(a.startswith("PYTHONPATH=") for a in add), "MCP 子进程要能找到 agent 包"


def test_the_chat_command_asks_for_the_event_stream():
    prov = KiroCliProvider({"binary": "kiro-cli"}, None)

    assert "stream-json" in prov.args_template
    assert prov.args_template.count("--output-format") == 1


def test_a_run_records_its_credit_cost_on_the_account(monkeypatch, tmp_path: Path):
    prov = _provider(monkeypatch, "  plobi\n")
    from quota_hub.providers.base import Account, Credential

    acc = Account(id="kiro-default", provider="kiro_cli", label="Kiro", cred=Credential(access_token="x"))
    prov.accounts = [acc]

    out = prov.parse_response(RUN, {"model": "kiro_cli/default", "messages": [{"role": "user", "content": "hi"}]})

    assert out["choices"][0]["message"]["content"] == "输出是 plobi-tool-ok"
    assert acc.quota["credits_used_last_turn"] == 0.2264
    assert acc.quota["context_percent"] == 22.6


class _FakeStdout:
    def __init__(self, lines):
        self._lines = list(lines)
        self._index = 0

    async def readline(self):
        if self._index < len(self._lines):
            line = self._lines[self._index]
            self._index += 1
            return line
        return b""

    async def read(self):
        return b""


class _FakeStderr:
    async def read(self):
        return b""


class _FakeProc:
    """Just enough of a child process for the base streaming loop."""

    returncode = 0

    def __init__(self, lines):
        self.stdout = _FakeStdout(lines)
        self.stderr = _FakeStderr()

    async def wait(self):
        return 0

    def kill(self):
        self.returncode = -9


def test_streamed_turns_send_each_chunk_instead_of_one_blob(monkeypatch):
    """Measured 2026-10-29: with `stream_from_chat=True` the whole answer arrived as a
    single delta after the turn ended. The CLI was streaming all along."""
    from quota_hub.providers.base import Account

    prov = KiroCliProvider({"binary": "kiro-cli"}, None)
    # Two token groups with a metering line between them: the client must see two deltas,
    # and the credit line must still land.
    lines = [
        json.dumps({"type": "sessionUpdate", "data": {"update": {
            "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "第一段"}}}}),
        json.dumps({"type": "metadata", "data": {"meteringUsage": [{"value": 0.1, "unit": "credit"}],
                                                 "contextUsagePercentage": 12.0}}),
        json.dumps({"type": "sessionUpdate", "data": {"update": {
            "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "第二段"}}}}),
        json.dumps({"type": "metadata", "data": {"meteringUsage": [{"value": 0.1264, "unit": "credit"}]}}),
        json.dumps({"type": "runFinished", "data": {"finalText": "第一段第二段"}}),
    ]
    encoded = [line.encode("utf-8") + b"\n" for line in lines]

    async def _spawn(acc, oai_req):
        return _FakeProc(encoded)

    monkeypatch.setattr(prov, "_spawn", _spawn)
    acc = Account(id="a", provider="kiro_cli")

    async def _go():
        return [
            chunk
            async for chunk in prov.chat_stream(
                acc, {"model": "kiro_cli/x", "messages": [{"content": "hi", "role": "user"}]}
            )
        ]

    chunks = asyncio.run(_go())
    deltas = [c["choices"][0]["delta"].get("content") for c in chunks if c["choices"][0]["delta"].get("content")]
    assert deltas == ["第一段", "第二段"], deltas
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
    assert acc.quota["credits_used_last_turn"] == 0.2264
    assert acc.quota["context_percent"] == 12.0


def test_a_run_that_only_ends_with_final_text_still_answers():
    prov = KiroCliProvider({"binary": "kiro-cli"}, None)
    prov._streamed_text = False
    line = json.dumps({"type": "runFinished", "data": {"finalText": "整段答案"}})
    assert prov.stream_text(line.encode("utf-8")) == "整段答案"


def test_a_failed_model_list_is_retried_once_before_keeping_the_seed():
    """Measured 2026-10-29: the same `--list-models` that answers in 1.8 s by hand came
    back unusable at cold boot, and the leg then served its one-row seed — which the
    picker offers as a model literally named `default`."""
    prov = KiroCliProvider({"binary": "kiro-cli"}, None)
    prov.binary_path = "kiro-cli"
    calls = {"n": 0}

    async def _once():
        calls["n"] += 1
        if calls["n"] == 1:
            return [], "exit 1: busy"
        return ["m1", "m2"], ""

    prov._fetch_models_once = _once
    shorts = asyncio.run(prov.refresh_models(None))

    assert calls["n"] == 2
    assert shorts == ["m1", "m2"]
    assert prov.served_models == ("kiro_cli/m1", "kiro_cli/m2")


def test_both_attempts_failing_keeps_the_seed_and_says_why(caplog):
    import logging

    prov = KiroCliProvider({"binary": "kiro-cli"}, None)
    prov.binary_path = "kiro-cli"
    prov.served_models = ("kiro_cli/default",)

    async def _once():
        return [], "exit 1: locked"

    prov._fetch_models_once = _once
    with caplog.at_level(logging.WARNING, logger="quota_hub"):
        assert asyncio.run(prov.refresh_models(None)) is None

    assert prov.served_models == ("kiro_cli/default",)
    assert "model refresh failed twice" in caplog.text
    assert "locked" in caplog.text
