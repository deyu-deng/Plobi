"""The Code Assist wire: what we send, and how an answer becomes an OpenAI choice.

Pure functions over dicts — no accounts, no HTTP, no provider. They live here because
every leg holding a Google-account (Code Assist) token must translate through ONE
implementation; the transport that calls them is `providers/cloudcode.py`.
"""

import json
import time
import uuid
from typing import Any


def _parse_antigravity_models(data: object) -> list[str]:
    """Extract short model ids from a ``fetchAvailableModels`` response.

    The real Google Cloud Code Assist API returns a **record** mapping model
    id -> quota info::

        {"models": {"gemini-3-pro-high": {"displayName": "...", "quotaInfo": {...}}, ...}}

    We also tolerate the list shapes seen in some reverse-engineered clients::

        {"models": [{"name": "models/gemini-3-pro"}, ...]}
        {"models": ["gemini-3-pro", ...]}
        {"availableModels": [...]} / {"available_models": [...]}

    Any ``models/`` or ``publishers/`` catalog prefix is stripped, and internal
    / placeholder models (``chat_*``, ``rev19*``, ``tab_*``, ``*image*``,
    embeddings, tts) are dropped so the user only sees real, chattable models.
    """
    if not isinstance(data, dict):
        return []
    raw = None
    for key in ("models", "availableModels", "available_models"):
        v = data.get(key)
        if v:
            raw = v
            break
    if raw is None:
        return []

    raw_ids: list[str] = []
    if isinstance(raw, dict):
        # Record shape: keys are the model ids.
        raw_ids.extend(str(k) for k in raw.keys())
    elif isinstance(raw, list):
        for item in raw:
            mid = ""
            if isinstance(item, str):
                mid = item
            elif isinstance(item, dict):
                mid = (
                    item.get("name")
                    or item.get("id")
                    or item.get("model")
                    or item.get("modelId")
                    or ""
                )
            if mid:
                raw_ids.append(str(mid))

    ids: list[str] = []
    for mid in raw_ids:
        mid = (mid or "").strip()
        if not mid:
            continue
        # Strip a leading catalog prefix like "models/" / "publishers/".
        mid = mid.split("/")[-1]
        low = mid.lower()
        # Drop internal / non-chat models that real clients filter out.
        if low.startswith(("chat_", "rev19", "tab_")):
            continue
        if any(s in low for s in ("image", "embedding", "tts")):
            continue
        if mid and mid not in ids:
            ids.append(mid)
    return ids


def _describe_google_error(e: dict) -> str:
    """Human-readable message for a Google ``{"error": {...}}`` payload.

    Google Code Assist rejects calls from unsupported regions with a bare
    ``FAILED_PRECONDITION`` / ``"User location is not supported for the API use"``
    — which is the expected failure for mainland-China networks. Surface that
    clearly instead of a cryptic status string.
    """
    msg = (e.get("message") or "unknown error") if isinstance(e, dict) else str(e)
    if "User location is not supported" in msg or "location is not supported" in msg:
        return (
            f"{msg}. Google Code Assist (Antigravity) is not available in this "
            "region — connect through a compliant overseas network to use it."
        )
    return msg


def _as_text(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return str(content)


def _to_gemini_tools(tools):
    if not tools:
        return None
    decls = []
    for t in tools:
        if not isinstance(t, dict) or t.get("type") != "function":
            continue
        fn = t.get("function", {})
        decls.append(
            {
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "parameters": fn.get("parameters", {}),
            }
        )
    return [{"functionDeclarations": decls}] if decls else None


def _extract_gemini_text(obj: dict) -> str:
    try:
        return "".join(
            p.get("text", "") for p in obj["candidates"][0]["content"]["parts"] if "text" in p
        )
    except (KeyError, IndexError, TypeError):
        return ""


def _extract_gemini_function_call(obj: dict):
    try:
        for p in obj["candidates"][0]["content"]["parts"]:
            if "functionCall" in p:
                return p["functionCall"]
    except (KeyError, IndexError, TypeError):
        return None
    return None


def _gemini_to_oai(obj: dict, model: str, project: str) -> dict:
    text_parts = []
    tool_calls = []
    try:
        for p in obj["candidates"][0]["content"]["parts"]:
            if "text" in p:
                text_parts.append(p["text"])
            elif "functionCall" in p:
                fc = p["functionCall"]
                tool_calls.append(
                    {
                        "id": "call_" + uuid.uuid4().hex[:8],
                        "type": "function",
                        "function": {
                            "name": fc.get("name", ""),
                            "arguments": json.dumps(fc.get("args", {}), ensure_ascii=False),
                        },
                    }
                )
    except (KeyError, IndexError, TypeError):
        pass
    usage = obj.get("usageMetadata", {})
    msg = {"role": "assistant", "content": "".join(text_parts) if text_parts else None}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls" if tool_calls else "stop",
                "message": msg,
            }
        ],
        "usage": {
            "prompt_tokens": usage.get("promptTokenCount", 0),
            "completion_tokens": usage.get("candidatesTokenCount", 0),
            "total_tokens": usage.get("totalTokenCount", 0),
        },
    }


def _chunk(cid: str, model: str, text, finish=None) -> dict:
    delta = {} if text is None else {"content": text}
    return {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


def _tool_chunk(cid: str, model: str, fn: dict) -> dict:
    return {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_" + uuid.uuid4().hex[:8],
                            "type": "function",
                            "function": {
                                "name": fn.get("name", ""),
                                "arguments": json.dumps(fn.get("args", {}), ensure_ascii=False),
                            },
                        }
                    ]
                },
                "finish_reason": None,
            }
        ],
    }

