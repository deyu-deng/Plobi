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

    excluded_ids: set[str] = set()
    for key in ("tabModelIds", "imageGenerationModelIds", "audioTranscriptionModelIds", "tieredModelIds"):
        vals = data.get(key)
        if isinstance(vals, list):
            excluded_ids.update(str(x).split("/")[-1] for x in vals if x)

    raw_ids: list[str] = []
    if isinstance(raw, dict):
        has_35_lite = "gemini-3.5-flash-lite" in raw
        for k, info in raw.items():
            mid = str(k).strip().split("/")[-1]
            if isinstance(info, dict):
                # A missing displayName is NOT a reason to drop the id: the captured
                # payload (measured 2026-10-29) has entries whose displayName is absent
                # and which are real models — the internal buckets are recognisable by
                # their own shape (`-tiered`, `tab_`…), which the common filter below
                # already drops. Dropping every nameless entry hid a real model
                # (gemini-2.5-flash) on the Gemini leg.
                if (
                    has_35_lite
                    and mid in ("gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-flash-thinking")
                    and info.get("displayName") == "Gemini 3.5 Flash Lite"
                ):
                    continue
            raw_ids.append(mid)
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
        if mid in excluded_ids:
            continue
        low = mid.lower()
        # Drop internal / non-chat models that real clients filter out.
        if low.startswith(("chat_", "rev19", "tab_")):
            continue
        # A `-tiered` row is NOT an internal bucket. Measured 2026-11-01 on this machine:
        # the newest Gemini row arrives as `gemini-3.8-flash-tiered` — `recommended: true`,
        # a 1M window, `supportsThinking`, and no `displayName` at all — and dropping every
        # `-tiered` id hid a model his Antigravity app shows him. The app resolves a tiered
        # row into `<family>-low|-medium|-high` (those strings are in its own binary), so the
        # family name is what we publish and what we send.
        if low.endswith("-tiered"):
            mid = mid[: -len("-tiered")]
        if any(s in mid.lower() for s in ("image", "embedding", "tts")):
            continue
        if mid and mid not in ids:
            ids.append(mid)

    # Respect upstream's recommended agentModelSorts ordering when present.
    preferred: list[str] = []
    for sort_block in data.get("agentModelSorts") or []:
        if not isinstance(sort_block, dict):
            continue
        for grp in sort_block.get("groups") or []:
            if not isinstance(grp, dict):
                continue
            for mid in grp.get("modelIds") or []:
                short = str(mid).split("/")[-1]
                if short in ids and short not in preferred:
                    preferred.append(short)
    if preferred:
        ids = preferred + [m for m in ids if m not in preferred]
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


_GEMINI_SCHEMA_SCALAR_KEYS = ("format", "description", "nullable", "minItems", "maxItems")


def _sanitize_gemini_schema(schema: Any, *, is_root: bool = False) -> dict[str, Any]:
    """Sanitize an OpenAI/JSON-Schema dict into Google Cloud Code Assist's Protobuf Schema.

    Cloud Code Assist (`v1internal`) parses `functionDeclarations[].parameters` directly
    into the Gemini Protobuf `Schema` message and rejects standard JSON Schema extensions
    with `400 INVALID_ARGUMENT`:
      - `type: ["string", "null"]` -> Proto field `Schema.type` is scalar, not repeating;
        must be `type: "string", nullable: True`.
      - `anyOf` / `oneOf` -> collapsed to first non-null branch (+ `nullable: True`).
      - Unknown JSON Schema keys (`additionalProperties`, `$schema`, `default`, `title`,
        `$defs`, `$ref`, `strict`, `examples`, `const`, etc.) -> stripped.
    """
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}} if is_root else {"type": "string"}

    src = dict(schema)
    out: dict[str, Any] = {}

    # Unwrap anyOf / oneOf if present.
    for union_key in ("anyOf", "oneOf"):
        branches = src.get(union_key)
        if isinstance(branches, list) and branches:
            non_null: list[dict[str, Any]] = []
            saw_null = False
            for b in branches:
                if isinstance(b, dict):
                    if b.get("type") == "null":
                        saw_null = True
                    else:
                        non_null.append(b)
            if saw_null:
                out["nullable"] = True
            if non_null and "type" not in src:
                merged = dict(non_null[0])
                for k, v in src.items():
                    if k not in (union_key, "anyOf", "oneOf"):
                        merged[k] = v
                src = merged
            break

    raw_type = src.get("type")
    if isinstance(raw_type, list):
        non_null_types = [str(t) for t in raw_type if t and str(t) != "null"]
        if "null" in raw_type:
            out["nullable"] = True
        out["type"] = non_null_types[0] if non_null_types else "string"
    elif isinstance(raw_type, str) and raw_type:
        if raw_type == "null":
            out["type"] = "string"
            out["nullable"] = True
        else:
            out["type"] = raw_type

    for k in _GEMINI_SCHEMA_SCALAR_KEYS:
        if k in src and src[k] is not None:
            out[k] = src[k]

    if isinstance(src.get("enum"), list) and src["enum"]:
        vals = [str(v) for v in src["enum"] if v is not None]
        if vals:
            out["enum"] = vals
            out.setdefault("type", "string")

    if isinstance(src.get("properties"), dict):
        props: dict[str, Any] = {}
        for prop_name, prop_schema in src["properties"].items():
            props[str(prop_name)] = _sanitize_gemini_schema(prop_schema, is_root=False)
        out["properties"] = props
        out["type"] = "object"
        if isinstance(src.get("required"), list):
            req = [str(r) for r in src["required"] if str(r) in props]
            if req:
                out["required"] = req

    if "items" in src and src["items"] is not None:
        raw_items = src["items"]
        if isinstance(raw_items, dict):
            out["items"] = _sanitize_gemini_schema(raw_items, is_root=False)
        elif isinstance(raw_items, list) and raw_items:
            out["items"] = _sanitize_gemini_schema(raw_items[0], is_root=False)
        else:
            out["items"] = {"type": "string"}
        out.setdefault("type", "array")

    if is_root:
        out.setdefault("type", "object")
        if out["type"] == "object":
            out.setdefault("properties", {})
    else:
        out.setdefault("type", "object" if "properties" in out else "string")
        if out["type"] == "array" and "items" not in out:
            out["items"] = {"type": "string"}

    return out


def _to_gemini_tools(tools):
    if not tools:
        return None
    decls = []
    for t in tools:
        if not isinstance(t, dict) or t.get("type") != "function":
            continue
        fn = t.get("function", {})
        raw_params = fn.get("parameters")
        params = _sanitize_gemini_schema(raw_params, is_root=True) if raw_params is not None else {"type": "object", "properties": {}}
        decls.append(
            {
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "parameters": params,
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

