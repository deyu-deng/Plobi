"""Unit verification for the Antigravity adapter.

Proves the adapter builds the EXACT request the real Cloud Code API expects
(URL, envelope, headers) WITHOUT needing a live token. Run:

    cd backup/aigw && python -m tests.test_antigravity
"""
from __future__ import annotations

import sys

from aigw.providers.antigravity import AntigravityProvider
from aigw.tokens import desktop_stores as ds


def build_provider() -> AntigravityProvider:
    # dummy http client; we only exercise pure request-building methods
    class _Dummy:
        pass

    cfg = {
        "host": "https://daily-cloudcode-pa.googleapis.com",
        "accounts": [],
    }
    return AntigravityProvider(cfg, _Dummy())


def check(cond: bool, msg: str) -> None:
    if not cond:
        print(f"  FAIL: {msg}")
        raise SystemExit(f"ASSERTION FAILED: {msg}")
    print(f"  ok:   {msg}")


def test_urls(p: AntigravityProvider) -> None:
    print("[URLs]")
    check(p._url(False) == "https://daily-cloudcode-pa.googleapis.com/v1internal:generateContent",
          "_url(False) -> generateContent (no model in path)")
    check("streamGenerateContent?alt=sse" in p._url(True),
          "_url(True) -> streamGenerateContent?alt=sse")


def test_headers(p: AntigravityProvider) -> None:
    print("[Headers]")
    h = p._headers("ya29.TEST", session_id="abc123", model="gemini-3-pro")
    check(h["Authorization"] == "Bearer ya29.TEST", "Authorization Bearer present")
    check(h["x-goog-api-client"] == "gl-node/18.18.2 fire/0.8.6 grpc/1.10.x",
          "x-goog-api-client mirrors the real client")
    check(h["X-Client-Name"] == "antigravity", "X-Client-Name present")
    check(h["X-Machine-Session-Id"] == "abc123", "X-Machine-Session-Id forwarded")
    h2 = p._headers("t", model="claude-sonnet-4-6", accept="text/event-stream")
    check(h2["Accept"] == "text/event-stream", "stream Accept header")
    h3 = p._headers("t", model="antigravity/claude-sonnet-4-6-thinking")
    check(h3.get("anthropic-beta") == "interleaved-thinking-2025-05-14",
          "claude thinking model gets anthropic-beta header")


def test_envelope(p: AntigravityProvider) -> None:
    print("[Envelope]")
    oai_req = {
        "model": "antigravity/gemini-3-pro",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello"},
            {
                "role": "assistant",
                "content": "Hi! How can I help?",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": '{"city":"SF"}'},
                    }
                ],
            },
            {"role": "tool", "name": "get_weather", "content": "72F"},
            {"role": "user", "content": "thanks"},
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "weather",
                    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
                },
            }
        ],
        "temperature": 0.7,
        "max_tokens": 512,
    }
    wrapped = p._wrap(oai_req, project="proj-123", model="gemini-3-pro", session_id="sess-1")
    check(wrapped["project"] == "proj-123", "envelope.project")
    check(wrapped["model"] == "gemini-3-pro", "envelope.model (short id)")
    check(wrapped["userAgent"] == "antigravity", "envelope.userAgent")
    check(wrapped["requestType"] == "agent", "envelope.requestType")
    check(wrapped["requestId"].startswith("agent-"), "envelope.requestId")
    req = wrapped["request"]
    check("contents" in req and isinstance(req["contents"], list), "request.contents")
    si = req.get("systemInstruction", {})
    check(si.get("role") == "user" and "You are a helpful assistant." in si["parts"][0]["text"],
          "systemInstruction.role == user + text")
    roles = [(c["role"], [list(pp.keys())[0] for pp in c["parts"]]) for c in req["contents"]]
    check(("user", ["functionResponse"]) in roles, "tool result -> functionResponse")
    check(("model", ["functionCall"]) in roles, "assistant tool_call -> functionCall")
    check("tools" in req and req["tools"][0]["functionDeclarations"][0]["name"] == "get_weather",
          "tools -> functionDeclarations")
    check(req["generationConfig"]["temperature"] == 0.7, "temperature mapped")
    check(req["generationConfig"]["maxOutputTokens"] == 512, "max_tokens -> maxOutputTokens")
    check(req["sessionId"] == "sess-1", "sessionId present")


def test_models() -> None:
    print("[Models]")
    for m in AntigravityProvider.served_models:
        check(m in ("antigravity/gemini-3-pro", "antigravity/gemini-3-flash",
                   "antigravity/claude-sonnet-4-6"), f"served model {m}")
    check(AntigravityProvider.MODEL_MAP["antigravity/gemini-3-pro"] == "gemini-3-pro",
          "MODEL_MAP strips antigravity/ prefix")


def test_composite() -> None:
    print("[Composite refresh token]")
    rt, pid, mpid = ds.parse_composite_refresh("REF|PROJ|MP")
    check(rt == "REF" and pid == "PROJ" and mpid == "MP", "parse composite")
    rt2, pid2, mpid2 = ds.parse_composite_refresh("REFONLY")
    check(rt2 == "REFONLY" and pid2 == "" and mpid2 == "", "parse plain refresh")


def main() -> int:
    print("=== antigravity adapter verification ===")
    p = build_provider()
    test_urls(p)
    test_headers(p)
    test_envelope(p)
    test_models()
    test_composite()
    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
