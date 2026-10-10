"""Format bridge: subscription-backed chat endpoints ⇄ Plobi's OpenAI surface.

Why this exists. Plobi's L1 loop is driven by ``tool_calls``. The three ways we
had of using someone else's quota — an MCP server, an ACP session, and a CLI
wrapped by ``aigw.providers.cli`` — all answer text only, so none of them can be
L1's mouth. A fourth shape can: take a subscription OAuth token and expose it as
an OpenAI-compatible endpoint that *does* return ``tool_calls``. This module is
the mapping layer for that shape.

It is deliberately inert: no route imports it yet, so nothing here changes how
the gateway behaves today. The mapping semantics are learned from
``Wei-Shaw/sub2api``'s ``backend/internal/pkg/apicompat`` (Go, LGPL-3.0); the
code is a Python reimplementation of the rules, written against the failure modes
that package documents in its own comments. Three of those rules are the reason
this file exists at all, and each has a test:

1. **Reasoning must be passable back.** Upstreams in thinking mode reject a
   history whose assistant ``reasoning_content`` is missing (sub2api records
   DeepSeek answering 400 "``reasoning_content`` in the thinking mode must be
   passed back to the API"). Codex-style histories carry reasoning items with an
   *opaque* encrypted body and no plaintext summary, so the client cannot resend
   it — the gateway has to remember what it streamed and restore it by id.
2. **Tool-call ids are namespaced, not opaque.** Anthropic ``toolu_…`` and
   OpenAI ``call_…`` are different protocols; an id with no counterpart in the
   protocol being emitted must be dropped rather than forwarded half-translated.
3. **A terminal stream event may carry an empty output array.** Accumulate first,
   and never let "nothing new in the last frame" erase what already arrived.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any, Optional

#: Anthropic stop_reason -> OpenAI finish_reason. Unmapped values become "stop"
#: only when nothing said "length"; a truncated turn must not read as complete.
_STOP_REASON = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "tool_use": "tool_calls",
    "refusal": "content_filter",
}
_FINISH_REASON = {value: key for key, value in _STOP_REASON.items() if key != "refusal"}


class ReasoningCache:
    """Remembers reasoning text by tool-call / item id so it can be resent.

    A client that received an opaque thinking block cannot give us the plaintext
    back on the next turn. Rather than forward a history the upstream will
    reject, the gateway serves the text it already streamed for that id.
    """

    def __init__(self) -> None:
        self._by_id: dict[str, str] = {}

    def remember(self, item_id: str, text: str) -> None:
        if item_id and text:
            self._by_id[item_id] = text

    def lookup(self, item_id: str) -> str:
        return self._by_id.get(item_id, "")

    def __len__(self) -> int:
        return len(self._by_id)


def remember_reasoning(cache: Optional[ReasoningCache], item_id: str, text: str) -> None:
    """Store reasoning without treating an *empty* cache as absent (it is falsy
    once ``__len__`` exists, which silently discards the caller's object)."""
    if cache is not None:
        cache.remember(item_id, text)


def lookup_reasoning(cache: Optional[ReasoningCache], item_id: str) -> str:
    if cache is None or not item_id:
        return ""
    return cache.lookup(item_id)


def tool_call_id_for(upstream_id: str, *, to_openai: bool = True) -> str:
    """Project an upstream tool-use id into the protocol being emitted.

    Ids are namespaced by prefix (rule 2). The mapping is *stable* — the same
    upstream id must produce the same emitted id across turns, or a follow-up
    ``tool`` message can no longer address the call it answers.
    """
    raw = (upstream_id or "").strip()
    if not raw:
        return ""
    if to_openai:
        if raw.startswith("call_"):
            return raw
        return "call_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    # Out of OpenAI: a call_… id has no Anthropic counterpart unless we minted it
    # above, in which case the caller keeps the original in `_upstream_id`.
    return raw


def upstream_tool_use_id(message: dict[str, Any], call_index: int = 0) -> str:
    """The id Anthropic issued for a call, preferring what we stashed on it."""
    for call in reversed(message.get("tool_calls") or []):
        stash = (call.get("_upstream_id") or "").strip()
        if stash:
            return stash
    blocks = message.get("content_blocks") or []
    uses = [b for b in blocks if isinstance(b, dict) and b.get("type") == "tool_use"]
    if uses and 0 <= call_index < len(uses):
        return str(uses[call_index].get("id") or "")
    return ""


def anthropic_response_to_chatcompletions(
    resp: dict[str, Any], *, model: str = "", reasoning: Optional[ReasoningCache] = None
) -> dict[str, Any]:
    """Anthropic Messages response -> OpenAI ChatCompletions response.

    The whole point of this direction: ``tool_use`` blocks become real
    ``tool_calls``, so a subscription-backed endpoint can drive Plobi's loop.
    """
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    reasoning_chunks: list[str] = []

    for block in resp.get("content") or []:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            text_parts.append(str(block.get("text") or ""))
        elif kind == "thinking":
            thinking = str(block.get("thinking") or "")
            block_id = str(block.get("id") or block.get("signature") or "")
            if thinking:
                reasoning_chunks.append(thinking)
                if block_id:
                    remember_reasoning(reasoning, block_id, thinking)
            elif block_id:
                # Opaque encrypted thinking: the client cannot resend it, so the
                # cache we filled when streaming is the only source left.
                held = lookup_reasoning(reasoning, block_id)
                if held:
                    reasoning_chunks.append(held)
        elif kind == "tool_use":
            raw_id = str(block.get("id") or "")
            tool_calls.append({
                "id": tool_call_id_for(raw_id),
                "type": "function",
                "function": {
                    "name": str(block.get("name") or ""),
                    "arguments": _as_json_string(block.get("input")),
                },
                "_upstream_id": raw_id,
            })

    message: dict[str, Any] = {"role": "assistant", "content": "".join(text_parts) or None}
    if tool_calls:
        message["tool_calls"] = tool_calls
    if reasoning_chunks:
        message["reasoning_content"] = "\n".join(reasoning_chunks)

    finish = _STOP_REASON.get(str(resp.get("stop_reason") or ""), "stop")
    usage = _usage_from_anthropic(resp.get("usage"))
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:20]}",
        "object": "chat.completion",
        "model": model or str(resp.get("model") or ""),
        "choices": [{
            "index": 0,
            "message": message,
            "finish_reason": finish,
        }],
        "usage": usage,
    }


def chatcompletions_request_to_anthropic(
    req: dict[str, Any], *, reasoning: Optional[ReasoningCache] = None
) -> dict[str, Any]:
    """OpenAI request -> Anthropic Messages request.

    Carries three things a naive mapping drops: the ``system`` string out of the
    message list, ``tool`` results back into ``tool_result`` blocks, and the
    assistant ``reasoning_content`` the upstream demands in thinking mode
    (rule 1) — restored from the cache when the client can no longer supply it.
    """
    system_bits: list[str] = []
    messages: list[dict[str, Any]] = []
    # Rule 2: ids are namespaced per protocol, so a tool_result must answer the
    # *upstream* id. Build the map from the assistant turns we have already seen.
    emitted_to_upstream: dict[str, str] = {}
    for prior in req.get("messages") or []:
        if isinstance(prior, dict):
            for call in prior.get("tool_calls") or []:
                if isinstance(call, dict) and call.get("_upstream_id"):
                    emitted_to_upstream[str(call.get("id") or "")] = str(call["_upstream_id"])

    for msg in req.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "")
        if role == "system":
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                system_bits.append(content)
            continue
        if role == "tool":
            call_id = str(msg.get("tool_call_id") or "")
            messages.append({"role": "user", "content": [{
                "type": "tool_result",
                "tool_use_id": str(
                    msg.get("_upstream_id") or emitted_to_upstream.get(call_id) or call_id
                ),
                "content": _tool_result_text(msg.get("content")),
            }]})
            continue
        if role == "assistant":
            blocks: list[dict[str, Any]] = []
            reasoning_text = str(msg.get("reasoning_content") or "")
            if not reasoning_text:
                for call in msg.get("tool_calls") or []:
                    # The cache is keyed by the UPSTREAM id — that is what we were
                    # given when the text streamed past.
                    reasoning_text = (
                        lookup_reasoning(reasoning, str(call.get("_upstream_id") or ""))
                        or lookup_reasoning(reasoning, str(call.get("id") or ""))
                    )
                    if reasoning_text:
                        break
            if reasoning_text:
                blocks.append({"type": "thinking", "thinking": reasoning_text})
            text = msg.get("content")
            if isinstance(text, str) and text:
                blocks.append({"type": "text", "text": text})
            for call in msg.get("tool_calls") or []:
                fn = call.get("function") or {}
                blocks.append({
                    "type": "tool_use",
                    "id": str(call.get("_upstream_id") or call.get("id") or ""),
                    "name": str(fn.get("name") or ""),
                    "input": _as_object(fn.get("arguments")),
                })
            messages.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})
            continue
        messages.append({"role": "user", "content": str(msg.get("content") or "")})

    out: dict[str, Any] = {
        "model": str(req.get("model") or ""),
        "messages": messages,
        "max_tokens": int(req.get("max_tokens") or 4096),
    }
    if system_bits:
        out["system"] = "\n\n".join(system_bits)

    tools = []
    for tool in req.get("tools") or []:
        fn = tool.get("function") if isinstance(tool, dict) else None
        if isinstance(fn, dict) and fn.get("name"):
            tools.append({
                "name": str(fn["name"]),
                "description": str(fn.get("description") or ""),
                "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
            })
    if tools:
        # Chat-only upstreams need both forms (sub2api's own comment); Anthropic
        # itself only reads `tools`, but we keep the declaration symmetric so one
        # request object can serve either upstream kind.
        out["tools"] = tools
    return out


def _usage_from_anthropic(usage: Any) -> dict[str, Any]:
    u = usage if isinstance(usage, dict) else {}
    prompt = int(u.get("input_tokens") or 0)
    completion = int(u.get("output_tokens") or 0)
    # No money field exists upstream; the ledger decides cost, never this file.
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }


def accumulate_stream(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Fold Anthropic SSE content-block events into one response.

    Rule 3: the terminal event may carry an empty output/content array, so text
    and tool blocks are accumulated as they arrive and the terminal frame only
    supplies stop_reason/usage — it must never overwrite what we collected.
    """
    blocks: dict[int, dict[str, Any]] = {}
    stop_reason = ""
    usage: dict[str, Any] = {}

    for event in events:
        if not isinstance(event, dict):
            continue
        kind = str(event.get("type") or "")
        if kind == "content_block_start":
            index = int(event.get("index") or 0)
            block = dict(event.get("content_block") or {})
            blocks.setdefault(index, block)
        elif kind == "content_block_delta":
            index = int(event.get("index") or 0)
            block = blocks.setdefault(index, {"type": "text", "text": ""})
            delta = event.get("delta") or {}
            dtype = str(delta.get("type") or "")
            if dtype == "text_delta":
                block["text"] = str(block.get("text") or "") + str(delta.get("text") or "")
            elif dtype == "thinking_delta":
                block["type"] = "thinking"
                block["thinking"] = str(block.get("thinking") or "") + str(delta.get("thinking") or "")
            elif dtype == "input_json_delta":
                block["input_partial"] = str(block.get("input_partial") or "") + str(
                    delta.get("partial_json") or ""
                )
        elif kind in ("message_delta", "message_end"):
            incoming_stop = (event.get("delta") or {}).get("stop_reason") or event.get("stop_reason")
            if incoming_stop:
                stop_reason = str(incoming_stop)
            incoming_usage = event.get("usage") or (event.get("delta") or {}).get("usage")
            if isinstance(incoming_usage, dict) and incoming_usage:
                usage = incoming_usage
        elif kind == "message_stop":
            continue

    ordered = [blocks[i] for i in sorted(blocks)]
    merged: list[dict[str, Any]] = []
    for block in ordered:
        if block.get("type") == "tool_use" and "input" not in block:
            block = {**block, "input": _as_object(block.pop("input_partial", "") or "{}")}
        merged.append(block)

    return {
        "role": "assistant",
        "content": merged,
        "stop_reason": stop_reason or "end_turn",
        "usage": usage,
    }


def _as_json_string(value: Any) -> str:
    import json

    if isinstance(value, str):
        return value
    try:
        return json.dumps(value if value is not None else {}, ensure_ascii=False)
    except (TypeError, ValueError):
        return "{}"


def _as_object(value: Any) -> dict[str, Any]:
    import json

    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _tool_result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    import json

    try:
        return json.dumps(content, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(content or "")
