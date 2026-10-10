"""Contract tests for the subscription⇄OpenAI format bridge.

Everything here runs against hand-built fake upstream frames — no network, no
account, no spend. They exist to pin the three failure modes documented in
``aigw/apicompat.py``, because each one is invisible until a real multi-turn
tool loop hits it.
"""

from __future__ import annotations

import json

from aigw import apicompat


def _anthropic_tool_response(tool_id="toolu_01ABC", name="search_files", args=None):
    return {
        "role": "assistant",
        "model": "claude-sonnet-4-6",
        "stop_reason": "tool_use",
        "content": [
            {"type": "text", "text": "looking"},
            {"type": "tool_use", "id": tool_id, "name": name,
             "input": args if args is not None else {"pattern": "*.py"}},
        ],
        "usage": {"input_tokens": 120, "output_tokens": 18},
    }


# ── 1. 工具调用必须真的回吐出来（这是整件事成立的前提） ─────────────────────

def test_tool_use_block_becomes_a_real_tool_call():
    out = apicompat.anthropic_response_to_chatcompletions(_anthropic_tool_response())
    message = out["choices"][0]["message"]

    assert message["content"] == "looking"
    calls = message["tool_calls"]
    assert len(calls) == 1 and calls[0]["type"] == "function"
    assert calls[0]["function"]["name"] == "search_files"
    assert json.loads(calls[0]["function"]["arguments"]) == {"pattern": "*.py"}
    assert calls[0]["id"].startswith("call_")
    # the Anthropic id survives so the follow-up tool_result can address it
    assert calls[0]["_upstream_id"] == "toolu_01ABC"


def test_stop_reason_maps_to_tool_calls_not_stop():
    out = apicompat.anthropic_response_to_chatcompletions(_anthropic_tool_response())
    assert out["choices"][0]["finish_reason"] == "tool_calls"


def test_truncation_is_never_reported_as_a_finished_turn():
    resp = _anthropic_tool_response()
    resp["stop_reason"] = "max_tokens"
    out = apicompat.anthropic_response_to_chatcompletions(resp)
    assert out["choices"][0]["finish_reason"] == "length"


def test_ids_are_stable_across_turns_and_namespaced():
    first = apicompat.tool_call_id_for("toolu_01ABC")
    assert first == apicompat.tool_call_id_for("toolu_01ABC")
    assert first != apicompat.tool_call_id_for("toolu_02XYZ")
    # an id already in OpenAI namespace passes through untouched
    assert apicompat.tool_call_id_for("call_keepme") == "call_keepme"
    assert apicompat.tool_call_id_for("") == ""


# ── 2. 思考内容必须能回传，否则上游第二轮直接 400 ─────────────────────────

def test_client_supplied_reasoning_is_passed_back_through():
    req = {
        "model": "claude-sonnet-4-6",
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "thinking out loud",
             "reasoning_content": "I should check the repo first"},
        ],
    }
    out = apicompat.chatcompletions_request_to_anthropic(req)
    assistant = out["messages"][1]
    blocks = {b["type"]: b for b in assistant["content"]}

    assert blocks["thinking"]["thinking"] == "I should check the repo first"
    assert blocks["text"]["text"] == "thinking out loud"


def test_opaque_thinking_is_restored_from_the_gateway_cache():
    """The client received an encrypted thinking block with no plaintext, so it
    cannot resend it. The gateway must serve what it streamed earlier."""
    cache = apicompat.ReasoningCache()
    resp = {
        "role": "assistant", "stop_reason": "end_turn", "content": [],
        "usage": {"input_tokens": 5, "output_tokens": 5},
    }
    resp["content"] = [{"type": "thinking", "id": "rs_1", "thinking": "the real reasoning"}]
    converted = apicompat.anthropic_response_to_chatcompletions(resp, reasoning=cache)
    assert converted["choices"][0]["message"]["reasoning_content"] == "the real reasoning"
    assert len(cache) == 1

    # second turn: the client sends the assistant message with NO reasoning text
    stripped = {"role": "assistant", "content": "", "tool_calls": [
        {"id": "call_xyz", "_upstream_id": "rs_1", "type": "function",
         "function": {"name": "x", "arguments": "{}"}}]}
    out = apicompat.chatcompletions_request_to_anthropic(
        {"model": "m", "messages": [stripped]}, reasoning=cache
    )
    blocks = out["messages"][0]["content"]
    assert any(b.get("type") == "thinking" and b.get("thinking") == "the real reasoning"
               for b in blocks), blocks


def test_cache_miss_does_not_invent_reasoning():
    out = apicompat.chatcompletions_request_to_anthropic(
        {"model": "m", "messages": [{"role": "assistant", "content": "x"}]},
        reasoning=apicompat.ReasoningCache(),
    )
    assert all(b.get("type") != "thinking" for b in out["messages"][0]["content"])


# ── 3. 终态事件可能带空 output，不能把已收的东西冲掉 ───────────────────────

def test_empty_terminal_event_does_not_erase_accumulated_text():
    events = [
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "half"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": " done"}},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
         "usage": {"input_tokens": 9, "output_tokens": 3}},
        {"type": "message_stop", "output": []},
    ]
    merged = apicompat.accumulate_stream(events)

    assert merged["content"][0]["text"] == "half done"
    assert merged["stop_reason"] == "end_turn"
    assert merged["usage"]["output_tokens"] == 3


def test_streamed_tool_input_json_is_reassembled_into_an_object():
    events = [
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "tool_use", "id": "toolu_9", "name": "write_file"}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "input_json_delta", "partial_json": '{"path":'}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "input_json_delta", "partial_json": ' "a.md"}'}},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
    ]
    merged = apicompat.accumulate_stream(events)
    out = apicompat.anthropic_response_to_chatcompletions(merged)
    call = out["choices"][0]["message"]["tool_calls"][0]

    assert json.loads(call["function"]["arguments"]) == {"path": "a.md"}
    assert out["choices"][0]["finish_reason"] == "tool_calls"


# ── 请求侧的其它必带项 ─────────────────────────────────────────────────────

def test_system_messages_are_hoisted_out_of_the_history():
    out = apicompat.chatcompletions_request_to_anthropic({
        "model": "m",
        "messages": [
            {"role": "system", "content": "be terse"},
            {"role": "user", "content": "hi"},
        ],
    })
    assert out["system"] == "be terse"
    assert [m["role"] for m in out["messages"]] == ["user"]


def test_tool_result_carries_the_upstream_id_not_the_openai_one():
    out = apicompat.chatcompletions_request_to_anthropic({
        "model": "m",
        "messages": [
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "call_hashed", "_upstream_id": "toolu_01ABC", "type": "function",
                 "function": {"name": "search_files", "arguments": '{"pattern":"*.py"}'}}]},
            {"role": "tool", "tool_call_id": "call_hashed", "content": "3 files"},
        ],
    })
    result = out["messages"][1]["content"][0]

    assert result["type"] == "tool_result"
    assert result["tool_use_id"] == "toolu_01ABC"
    assert result["content"] == "3 files"


def test_tools_are_declared_with_input_schema():
    out = apicompat.chatcompletions_request_to_anthropic({
        "model": "m", "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"type": "function", "function": {
            "name": "terminal", "description": "run it",
            "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}}}}],
    })
    tool = out["tools"][0]

    assert tool["name"] == "terminal"
    assert tool["input_schema"]["properties"]["cmd"]["type"] == "string"


def test_usage_carries_tokens_and_never_invents_money():
    out = apicompat.anthropic_response_to_chatcompletions(_anthropic_tool_response())

    assert out["usage"] == {"prompt_tokens": 120, "completion_tokens": 18, "total_tokens": 138}
    assert "cost" not in json.dumps(out)


# ── 边界：这一格现在必须是惰性的，不许偷偷改网关服务本体（裁定 45） ─────────

def test_module_is_inert_nothing_in_the_gateway_imports_it_yet():
    import sys
    from pathlib import Path

    pkg = Path(apicompat.__file__).parent
    importers = []
    for path in pkg.rglob("*.py"):
        # the module itself, and its own tests, are not "the gateway importing it"
        if path.name == "apicompat.py" or path.name.startswith("test_"):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "apicompat" in text:
            importers.append(path.name)

    assert importers == [], f"apicompat 已被这些模块引用：{importers}"
    assert "aigw.main" not in sys.modules or "apicompat" not in str(
        getattr(sys.modules.get("aigw.main"), "__file__", "")
    )
