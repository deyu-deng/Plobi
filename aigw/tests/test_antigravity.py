"""Unit verification for the Antigravity adapter.

Proves the adapter builds the EXACT request the real Cloud Code API expects
(URL, envelope, headers) WITHOUT needing a live token. Run:

    cd backup/aigw && python -m tests.test_antigravity
"""

from __future__ import annotations

import sys

import pytest

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


@pytest.fixture
def p() -> AntigravityProvider:
    """Provider instance for the request-building unit tests."""
    return build_provider()


def check(cond: bool, msg: str) -> None:
    if not cond:
        print(f"  FAIL: {msg}")
        raise SystemExit(f"ASSERTION FAILED: {msg}")
    print(f"  ok:   {msg}")


def test_urls(p: AntigravityProvider) -> None:
    print("[URLs]")
    check(
        p._url(False) == "https://daily-cloudcode-pa.googleapis.com/v1internal:generateContent",
        "_url(False) -> generateContent (no model in path)",
    )
    check(
        "streamGenerateContent?alt=sse" in p._url(True),
        "_url(True) -> streamGenerateContent?alt=sse",
    )


def test_headers(p: AntigravityProvider) -> None:
    print("[Headers]")
    h = p._headers("ya29.TEST", session_id="abc123", model="gemini-3-pro")
    check(h["Authorization"] == "Bearer ya29.TEST", "Authorization Bearer present")
    check(
        h["x-goog-api-client"] == "gl-node/18.18.2 fire/0.8.6 grpc/1.10.x",
        "x-goog-api-client mirrors the real client",
    )
    check(h["X-Client-Name"] == "antigravity", "X-Client-Name present")
    check(h["X-Machine-Session-Id"] == "abc123", "X-Machine-Session-Id forwarded")
    h2 = p._headers("t", model="claude-sonnet-4-6", accept="text/event-stream")
    check(h2["Accept"] == "text/event-stream", "stream Accept header")
    h3 = p._headers("t", model="antigravity/claude-sonnet-4-6-thinking")
    check(
        h3.get("anthropic-beta") == "interleaved-thinking-2025-05-14",
        "claude thinking model gets anthropic-beta header",
    )


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
    check(
        si.get("role") == "user" and "You are a helpful assistant." in si["parts"][0]["text"],
        "systemInstruction.role == user + text",
    )
    roles = [(c["role"], [list(pp.keys())[0] for pp in c["parts"]]) for c in req["contents"]]
    check(("user", ["functionResponse"]) in roles, "tool result -> functionResponse")
    check(("model", ["functionCall"]) in roles, "assistant tool_call -> functionCall")
    check(
        "tools" in req and req["tools"][0]["functionDeclarations"][0]["name"] == "get_weather",
        "tools -> functionDeclarations",
    )
    check(req["generationConfig"]["temperature"] == 0.7, "temperature mapped")
    check(req["generationConfig"]["maxOutputTokens"] == 512, "max_tokens -> maxOutputTokens")
    check(req["sessionId"] == "sess-1", "sessionId present")


def test_models() -> None:
    print("[Models]")
    # served_models / MODEL_MAP are per-instance: seeded in __init__ from
    # _SEED_MODELS and replaced at runtime by refresh_models(). The class-level
    # base attribute (Provider.served_models) is just an empty tuple, so the
    # catalog must be asserted on a built instance.
    prov = build_provider()
    expected = {
        "antigravity/gemini-3-flash",
        "antigravity/gemini-3-pro-high",
        "antigravity/gemini-3-pro-low",
        "antigravity/gemini-3.1-pro-high",
        "antigravity/gemini-3.1-pro-low",
        "antigravity/claude-opus-4-6-thinking",
        "antigravity/claude-opus-4-5-thinking",
        "antigravity/claude-sonnet-4-6",
    }
    for m in prov.served_models:
        check(m in expected, f"served model {m}")
    check(
        set(prov.served_models) == expected,
        "served catalog matches the seed catalog exactly",
    )
    for m in prov.served_models:
        check(
            prov.MODEL_MAP[m] == m.split("/", 1)[1],
            f"MODEL_MAP strips antigravity/ prefix for {m}",
        )


def test_composite() -> None:
    print("[Composite refresh token]")
    rt, pid, mpid = ds.parse_composite_refresh("REF|PROJ|MP")
    check(rt == "REF" and pid == "PROJ" and mpid == "MP", "parse composite")
    rt2, pid2, mpid2 = ds.parse_composite_refresh("REFONLY")
    check(rt2 == "REFONLY" and pid2 == "" and mpid2 == "", "parse plain refresh")


class _FakeResponse:
    """Minimal stand-in for the httpx.Response chat() consumes."""

    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.headers = {}

    def json(self):
        return self._payload


class _FakeHttp:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "json": json})
        return _FakeResponse(self.payload)


class _FakeCred:
    access_token = "ya29.test-token-not-a-secret"
    extra: dict = {}

    def is_expired(self, skew=60.0):
        return False


class _FakeAccount:
    cred = _FakeCred()
    id = "test-account"


# Gemini's success shape, exactly as the API wraps it: {"response": {...}}.
_SUCCESS = {"response": {"candidates": [{"content": {"parts": [
    {"functionCall": {"name": "get_weather", "args": {"city": "Shanghai"}}}]},
    "finishReason": "STOP"}],
    "usageMetadata": {"promptTokenCount": 60, "candidatesTokenCount": 16,
                      "thoughtsTokenCount": 53, "totalTokenCount": 129}}}


@pytest.mark.asyncio
async def test_chat_returns_a_completion_on_a_successful_upstream():
    """Regression: the unwrap + return used to sit INSIDE the `if "error" in data`
    block, after its ``raise`` — so a *successful* answer fell off the end of
    chat() and the caller got ``None``. The adapter was otherwise believed to
    work because no test ever called chat(); this is that test.

    Measured for real on 2026-10-11 against the subscription (finish_reason
    ``tool_calls``), then pinned here offline with the same wire shape.
    """
    prov = AntigravityProvider({"host": "https://daily-cloudcode-pa.googleapis.com",
                                "project_id": "test-project"}, _FakeHttp(_SUCCESS))
    resp = await prov.chat(_FakeAccount(), {"model": "gemini-3.8-flash-low",
                                            "messages": [{"role": "user", "content": "weather?"}],
                                            "tools": [{"type": "function", "function": {"name": "get_weather"}}]})
    assert resp is not None, "chat() returned None — the success path fell off the end"
    assert resp.get("choices"), "a successful upstream answer must produce choices"
    msg = resp["choices"][0]["message"]
    calls = msg.get("tool_calls") or []
    assert calls and calls[0]["function"]["name"] == "get_weather", calls
    assert resp["choices"][0]["finish_reason"] == "tool_calls"
    assert resp.get("usage", {}).get("prompt_tokens") == 60


@pytest.mark.asyncio
async def test_chat_still_raises_on_an_error_payload():
    """The other half: the branch that DID run must keep running."""
    payload = {"error": {"code": 400, "message": "User location is not supported for the API use"}}
    prov = AntigravityProvider({"host": "https://daily-cloudcode-pa.googleapis.com",
                                "project_id": "test-project"}, _FakeHttp(payload))
    from aigw.providers.base import UpstreamError
    with pytest.raises(UpstreamError):
        await prov.chat(_FakeAccount(), {"model": "gemini-3.8-flash-low",
                                         "messages": [{"role": "user", "content": "hi"}]})


@pytest.mark.asyncio
async def test_chat_names_a_non_object_payload_instead_of_crashing():
    """Moving the unwrap out of the error branch made it reachable for *any*
    payload shape. A JSON list would then hit ``.get`` and raise AttributeError —
    which is why the unwrap carries an isinstance guard. This pins that guard.

    The same fake also covers the first POST chat() provokes (loadCodeAssist),
    which used to crash on ``list.get`` for the same reason.
    """
    prov = AntigravityProvider({"host": "https://daily-cloudcode-pa.googleapis.com",
                                "project_id": "test-project"}, _FakeHttp([1, 2, 3]))
    from aigw.providers.base import UpstreamError
    with pytest.raises(UpstreamError):
        await prov.chat(_FakeAccount(), {"model": "gemini-3.8-flash-low",
                                         "messages": [{"role": "user", "content": "hi"}]})


@pytest.mark.asyncio
async def test_load_code_assist_returns_empty_on_the_wrong_shape_instead_of_raising():
    """Its four documented failure modes all return "" (transport, non-200, bad
    JSON, missing project). A list payload was the fifth shape and it raised."""
    prov = AntigravityProvider({"host": "https://daily-cloudcode-pa.googleapis.com",
                                "project_id": "test-project"}, _FakeHttp([1, 2, 3]))
    assert await prov._load_code_assist(_FakeAccount()) == ""


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
