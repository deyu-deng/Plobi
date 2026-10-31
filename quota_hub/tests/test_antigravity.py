"""Unit verification for the Antigravity adapter.

Proves the adapter builds the EXACT request the real Cloud Code API expects
(URL, envelope, headers) WITHOUT needing a live token. Run:

    cd backup/quota_hub && python -m tests.test_antigravity
"""

from __future__ import annotations

import sys

import pytest

from quota_hub.providers.apps.antigravity import AntigravityProvider
from quota_hub.tokens import desktop_stores as ds


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
    # No hand-copied catalog on purpose (measured 2026-10-29 against the real Antigravity
    # app: it now offers Gemini 4 Argon / Gemini 3.8 Flash / Claude Opus 5.5 / Claude
    # Sonnet 5.5 / GPT-OSS 120B, and the old hand-copied ids are gone upstream). Until
    # upstream answers, this leg offers nothing — and the card says why.
    check(tuple(prov.served_models) == (), "no hand-copied catalog before upstream answers")
    check(dict(prov.MODEL_MAP) == {}, "no hand-copied mapping before upstream answers")

    # What upstream says is what we serve, and the prefix is stripped for the wire.
    prov.served_models = ("antigravity/gemini-4-argon-medium",)
    prov.MODEL_MAP = {"antigravity/gemini-4-argon-medium": "gemini-4-argon-medium"}
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
    from quota_hub.providers.base import UpstreamError
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
    from quota_hub.providers.base import UpstreamError
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


@pytest.mark.asyncio
async def test_a_self_owned_account_renews_without_any_external_binary(monkeypatch):
    """Measured 2026-10-31: we CAN refresh this account ourselves.

    The stored refresh token plus the public client pair - adjacency-extracted from the
    product installed on this machine, byte-identical to what three independent
    implementations cite - renews the lease directly (`access_len=260 expires_in=3599`),
    and a turn served afterwards still comes back `finish_reason=tool_calls`. The old claim
    in this file's docstrings ("the leg cannot refresh itself, no client pair") was a
    misreading of a pair WE had assembled wrongly: one product's id with another's secret.
    """
    import time

    from quota_hub.providers.apps.antigravity import tool_bridge

    async def _renew(_http, refresh_token, client_id, client_secret):
        seen["renewed_with"] = (client_id[:12], bool(client_secret))
        # The shape `desktop_stores.refresh_antigravity` really returns: an absolute stamp.
        return {"access_token": "SELF-RENEWED", "expires_at": time.time() + 3599,
                "refresh_token": refresh_token}

    def _no_binary(*_a, **_k):
        raise AssertionError("the external tool must not be needed once we hold our own token")

    seen: dict = {}
    monkeypatch.setattr(ds, "refresh_antigravity", _renew)
    monkeypatch.setattr(tool_bridge, "refresh_via_tool", _no_binary)

    prov = AntigravityProvider({"project_id": "p"}, _FakeHttp(_SUCCESS))
    # The node is a separate question from the renewal, and this test is about the renewal.
    # Left un-pinned, the leg's own "no node, don't dial" guard fired on a machine whose VPN
    # was down and this test failed for a reason it does not cover (measured 2026-10-31 22:4x).
    async def _no_rebind() -> None:
        return None

    monkeypatch.setattr(prov, "_rebind_node", _no_rebind)
    prov.proxy_reason = "ok"
    acc = prov._make_account(id="antigravity-self", label="self", refresh_token="RT",
                             access_token="STALE", client_id="cid.apps.googleusercontent.com",
                             client_secret="GOCSPX-x")
    acc.cred.expires_at = time.time() - 10  # the lease an idle hour would have killed

    await prov.ensure_fresh(acc)
    assert acc.cred.access_token == "SELF-RENEWED"
    assert seen["renewed_with"][0] == "cid.apps.goo" and seen["renewed_with"][1] is True
    assert acc.cred.expires_at > time.time()


@pytest.mark.asyncio
async def test_ensure_fresh_picks_up_the_token_the_desktop_app_renewed(monkeypatch):
    """The shared slot's lease is the desktop app's to renew - we re-read it instead of
    spending a credential that is not ours. (A sign-in of our own, with a refresh token in
    our own file, renews directly: see the test above.) Reading the local store before each
    dispatch is what keeps the leg alive past ~60 minutes."""
    import time
    from quota_hub.providers.base import AccountState
    prov = AntigravityProvider({"project_id": "p"}, _FakeHttp(_SUCCESS))
    acc = prov._access_only_account("antigravity-keychain", "OLD", "cid", "csec",
                                    desktop_store=True)
    monkeypatch.setattr(ds, "antigravity_desktop_token_status", lambda: {
        "source": "windows-credential-manager", "present": True,
        "expires_at": time.time() + 1800, "expires_in_seconds": 1800})
    monkeypatch.setattr(ds, "read_antigravity_access_token", lambda: "NEW")
    await prov.ensure_fresh(acc)
    assert acc.cred.access_token == "NEW"
    assert acc.cred.expires_at > time.time(), "the app's own stamp should be trusted"
    assert acc.state is AccountState.ACTIVE


@pytest.mark.asyncio
async def test_ensure_fresh_refuses_locally_when_the_store_says_expired(monkeypatch):
    """A known-dead token must not cost a network round-trip, and the verdict has
    to name the only fix a human can do."""
    import time
    from quota_hub.providers.base import AccountState, UpstreamError
    prov = AntigravityProvider({"project_id": "p"}, _FakeHttp(_SUCCESS))
    acc = prov._access_only_account("antigravity-keychain", "OLD", "cid", "csec",
                                    desktop_store=True)
    monkeypatch.setattr(ds, "antigravity_desktop_token_status", lambda: {
        "source": "windows-credential-manager", "present": True,
        "expires_at": time.time() - 600, "expires_in_seconds": -600})
    monkeypatch.setattr(ds, "read_antigravity_access_token", lambda: None)
    with pytest.raises(UpstreamError):
        await prov.ensure_fresh(acc)
    assert acc.state is AccountState.REAUTH
    assert prov.http.calls == [], "an expired desktop token must not be dialed"


@pytest.mark.asyncio
async def test_ensure_fresh_does_not_invent_a_death_when_the_store_is_unreadable(monkeypatch):
    """Absent / locked / unreadable is not evidence the token is dead — the old
    copy may still work, so let the upstream answer instead of pre-judging."""
    from quota_hub.providers.base import AccountState
    prov = AntigravityProvider({"project_id": "p"}, _FakeHttp(_SUCCESS))
    acc = prov._access_only_account("antigravity-keychain", "OLD", "cid", "csec",
                                    desktop_store=True)
    monkeypatch.setattr(ds, "antigravity_desktop_token_status", lambda: {
        "source": "windows-credential-manager", "present": False,
        "expires_at": None, "expires_in_seconds": None})
    monkeypatch.setattr(ds, "read_antigravity_access_token", lambda: None)
    await prov.ensure_fresh(acc)
    assert acc.cred.access_token == "OLD"
    assert acc.state is not AccountState.REAUTH


@pytest.mark.asyncio
async def test_a_token_the_operator_pasted_is_never_overwritten_by_the_store(monkeypatch):
    """ANTIGRAVITY_ACCESS_TOKEN accounts are the operator's own business: swapping
    in a desktop app's token behind their back is a second source of truth."""
    prov = AntigravityProvider({"project_id": "p"}, _FakeHttp(_SUCCESS))
    acc = prov._access_only_account("antigravity-env-access", "MINE", "cid", "csec")
    assert acc.cred.extra["desktop_store"] is False

    def _boom():
        raise AssertionError("the desktop store must not be consulted")

    monkeypatch.setattr(ds, "antigravity_desktop_token_status", _boom)
    await prov.ensure_fresh(acc)
    assert acc.cred.access_token == "MINE"


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


@pytest.mark.asyncio
async def test_a_stale_slot_is_refreshed_through_antigravity_tools(monkeypatch):
    """Measured 2026-10-29: `antigravity-tools switch --target agy` put a fresh token
    into the shared Keychain slot, and this leg's eight models became selectable — no
    browser, no consent page. So when the slot has gone stale we ask the tool that owns
    the account instead of telling the user to open an app."""
    import time

    from quota_hub.providers.apps.antigravity import tool_bridge

    calls = {"n": 0}

    def _status():
        calls["n"] += 1
        if calls["n"] <= 1:
            return {"source": "windows-credential-manager", "present": True,
                    "expires_at": time.time() - 600, "expires_in_seconds": -600}
        return {"source": "windows-credential-manager", "present": True,
                "expires_at": time.time() + 3000, "expires_in_seconds": 3000}

    prov = AntigravityProvider({"project_id": "p"}, _FakeHttp(_SUCCESS))
    acc = prov._access_only_account("antigravity-keychain", "OLD", "cid", "csec",
                                    desktop_store=True)
    monkeypatch.setattr(ds, "antigravity_desktop_token_status", _status)
    monkeypatch.setattr(ds, "read_antigravity_access_token", lambda: "FRESH")
    monkeypatch.setattr(tool_bridge, "refresh_via_tool", lambda **_kw: (True, "switched"))

    await prov.ensure_fresh(acc)

    assert acc.cred.access_token == "FRESH"
    assert getattr(acc.state, "value", acc.state) == "active"


@pytest.mark.asyncio
async def test_a_quota_refusal_rotates_to_the_next_account_and_retries(monkeypatch):
    """Measured from their source (Antigravity-Manager): an upstream 429/401 triggers a
    silent rotation to the next account and a retry. We ask the tool that owns the
    accounts instead of re-implementing the rotation."""
    import time

    from quota_hub.providers.apps.antigravity import tool_bridge
    from quota_hub.providers.base import UpstreamError

    calls = {"n": 0}

    class _Cloudcode:
        async def chat(self, **_kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise UpstreamError(429, "quota exhausted", retryable=True)
            return {"choices": [{"message": {"content": "ok"}}]}

    prov = AntigravityProvider({"project_id": "p"}, _FakeHttp(_SUCCESS))
    prov.cloudcode = _Cloudcode()
    acc = prov._access_only_account("antigravity-keychain", "OLD", "cid", "csec",
                                    desktop_store=True)
    monkeypatch.setattr(tool_bridge, "refresh_via_tool", lambda **_kw: (True, "switched"))
    monkeypatch.setattr(ds, "antigravity_desktop_token_status", lambda: {
        "source": "windows-credential-manager", "present": True,
        "expires_at": time.time() + 3000, "expires_in_seconds": 3000})
    monkeypatch.setattr(ds, "read_antigravity_access_token", lambda: "FRESH")

    out = await prov.chat(acc, {"model": "antigravity/gemini-3-flash",
                                "messages": [{"role": "user", "content": "hi"}]})

    assert calls["n"] == 2, "the turn was not retried after the rotation"
    assert out["choices"][0]["message"]["content"] == "ok"
    assert acc.cred.access_token == "FRESH"


@pytest.mark.asyncio
async def test_an_account_without_quota_is_replaced_before_the_turn(monkeypatch):
    """Their CLI lists accounts without their remaining quota (the dashboard has it, in an
    encrypted store we do not read), so the reachable form of "pick the healthiest" is:
    we read the quota of the account we are on, and switch before the request fails."""
    import time

    from quota_hub.providers.apps.antigravity import tool_bridge

    seen = {}

    class _Cloudcode:
        async def chat(self, **kwargs):
            seen["token"] = kwargs.get("token")
            return {"choices": [{"message": {"content": "ok"}}]}

    prov = AntigravityProvider({"project_id": "p"}, _FakeHttp(_SUCCESS))
    prov.cloudcode = _Cloudcode()
    acc = prov._access_only_account("antigravity-keychain", "OLD", "cid", "csec",
                                    desktop_store=True)
    acc.quota["remaining"] = 0
    monkeypatch.setattr(tool_bridge, "refresh_via_tool", lambda **_kw: (True, "switched"))
    monkeypatch.setattr(ds, "antigravity_desktop_token_status", lambda: {
        "source": "windows-credential-manager", "present": True,
        "expires_at": time.time() + 3000, "expires_in_seconds": 3000})
    monkeypatch.setattr(ds, "read_antigravity_access_token", lambda: "FRESH")

    await prov.chat(acc, {"model": "antigravity/gemini-3-flash",
                          "messages": [{"role": "user", "content": "hi"}]})

    assert seen["token"] == "FRESH", "the turn went out on the account with no quota left"


def test_to_gemini_tools_sanitizes_openai_schema_for_cloudcode_protobuf() -> None:
    """Measured live 2026-10-29 against Cloud Code Assist v1internal:
    `type: ["string", "null"]` and unknown JSON Schema keys (`additionalProperties`,
    `$schema`, `default`, `title`) cause HTTP 400 INVALID_ARGUMENT (`Proto field is
    not repeating, cannot start list` / `Unknown name`)."""
    from quota_hub.providers.cloudcode_wire import _parse_antigravity_models, _to_gemini_tools

    tools = [
        {
            "type": "function",
            "function": {
                "name": "agenda_query",
                "description": "Query agenda",
                "parameters": {
                    "$schema": "http://json-schema.org/draft-07/schema#",
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "date": {
                            "type": ["string", "null"],
                            "description": "ISO date",
                            "default": None,
                            "title": "Date",
                        },
                        "tags": {
                            "anyOf": [{"type": "array", "items": {"type": "string"}}, {"type": "null"}],
                        },
                    },
                    "required": ["date", "nonexistent"],
                },
            },
        }
    ]
    gem = _to_gemini_tools(tools)
    assert gem is not None
    params = gem[0]["functionDeclarations"][0]["parameters"]
    assert "$schema" not in params
    assert "additionalProperties" not in params
    assert params["required"] == ["date"]
    date_prop = params["properties"]["date"]
    assert date_prop["type"] == "string"
    assert date_prop["nullable"] is True
    assert "default" not in date_prop and "title" not in date_prop
    tags_prop = params["properties"]["tags"]
    assert tags_prop["type"] == "array"
    assert tags_prop["nullable"] is True

    # Also verify _parse_antigravity_models drops internal buckets with no displayName
    # and legacy 2.5 aliases that point to Gemini 3.5 Flash Lite.
    catalog = {
        "models": {
            "gemini-2.5-flash": {"displayName": "Gemini 3.5 Flash Lite"},
            "gemini-3.5-flash-lite": {"displayName": "Gemini 3.5 Flash Lite"},
            "gemini-3-flash-agent": {"displayName": "Gemini 3.5 Flash (High)"},
            "gemini-3.8-flash-tiered": {"displayName": None},
            "claude-sonnet-5-5-high": {"displayName": "Claude Sonnet 5.5 (High)"},
        },
        "agentModelSorts": [
            {"groups": [{"modelIds": ["claude-sonnet-5-5-high", "gemini-3-flash-agent"]}]}
        ],
    }
    parsed = _parse_antigravity_models(catalog)
    assert parsed == ["claude-sonnet-5-5-high", "gemini-3-flash-agent", "gemini-3.5-flash-lite"]



def _empty_store(monkeypatch, tmp_path) -> None:
    """No account of ours: the shared slot is then the only source of truth."""
    monkeypatch.setenv("PLOBI_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ANTIGRAVITY_REFRESH_TOKEN", raising=False)


def test_a_stale_slot_renews_the_lease_instead_of_saying_signed_out(monkeypatch, tmp_path):
    """The slot holds an access token (an hour-long lease); the account behind it is
    long-lived, and the tool that owns it can re-push a fresh token locally.

    Measured 2026-10-29 23:19: the lease had run out, the account was healthy, and the
    card had fallen back to a `+` — the user had to send a turn to get it back. The
    verdict renews the lease itself now, so the card keeps saying "signed in".
    """
    import threading
    import time

    from quota_hub.providers.apps.antigravity import tool_bridge

    _empty_store(monkeypatch, tmp_path)
    prov = build_provider()
    slot = {"present": True, "expires_at": time.time() - 600, "expires_in_seconds": -600}
    monkeypatch.setattr(ds, "antigravity_desktop_token_status", lambda: dict(slot))

    asked = threading.Event()

    def _fake_refresh(**_kwargs):
        slot["expires_at"] = time.time() + 1200
        slot["expires_in_seconds"] = 1200
        asked.set()
        return True, "ok"

    monkeypatch.setattr(tool_bridge, "refresh_via_tool", _fake_refresh)

    # The first read sees the run-out lease: the tool is asked, and the verdict keeps
    # the answer it had instead of dropping the card back to a `+`.
    prov._verdict = (True, "ok")
    prov._verdict_at = 0.0  # force a re-read of the slot
    live, reason = prov.signed_in()
    assert live is True, reason
    assert asked.wait(5), "the tool that owns the account was never asked"

    # Once the renewal landed, the slot reads fresh again.
    prov._verdict_at = 0.0
    live, reason = prov.signed_in()
    assert live is True, reason


def test_a_lease_that_cannot_be_renewed_says_so(monkeypatch, tmp_path):
    """If the tool cannot re-push the account, the `+` is the honest answer.

    We must not keep the card on forever on a verdict nobody re-checked.
    """
    import time

    _empty_store(monkeypatch, tmp_path)
    prov = build_provider()
    slot = {"present": True, "expires_at": time.time() - 600, "expires_in_seconds": -600}
    monkeypatch.setattr(ds, "antigravity_desktop_token_status", lambda: dict(slot))

    prov._verdict = (True, "ok")
    prov._verdict_at = 0.0
    prov.signed_in()

    # The stub in conftest answers "could not renew"; wait for the background thread
    # to record that, then the verdict must turn honest.
    deadline = time.time() + 5
    while time.time() < deadline and prov._verdict != (False, "credential_expired"):
        time.sleep(0.02)
    assert prov._verdict == (False, "credential_expired"), prov._verdict

    # And the next read agrees — the card is honest from here on.
    prov._verdict_at = 0.0
    live, reason = prov.signed_in()
    assert live is False
    assert reason == "credential_expired"


def test_an_absent_slot_is_not_signed_in_and_asks_nobody(monkeypatch, tmp_path):
    """No credential at all is a plain `+`: there is nothing to renew."""
    import time

    from quota_hub.providers.apps.antigravity import tool_bridge

    _empty_store(monkeypatch, tmp_path)
    prov = build_provider()
    monkeypatch.setattr(ds, "antigravity_desktop_token_status", lambda: {
        "source": "windows-credential-manager", "present": False,
        "expires_at": None, "expires_in_seconds": None})

    asked = []

    def _fake_refresh(**_kwargs):
        asked.append(1)
        return True, "ok"

    monkeypatch.setattr(tool_bridge, "refresh_via_tool", _fake_refresh)

    live, reason = prov.signed_in()
    assert live is False
    assert reason == "no_desktop_credential"
    assert asked == [], "nothing to renew: the tool must not be run"


def test_the_tool_is_handed_the_exit_node_it_needs_to_renew(monkeypatch):
    """The tool renews the account against Google **itself**, so it needs the way out.

    Measured 2026-10-29 23:49: without a proxy it answered "无法连接 Google 授权服务器"
    and the shared slot stayed stale — the leg then said "credential expired" and the
    card fell back to a `+`. The node from Settings → VPN is the sanctioned exit for
    every Google byte (裁定 206), and the tool reads the standard proxy variables.
    """
    import os

    from quota_hub.providers.apps.antigravity import tool_bridge

    seen: dict = {}

    class _Proc:
        returncode = 0
        stdout = "switched"
        stderr = ""

    def _fake_run(binary, env, timeout):
        seen["env"] = env
        return _Proc()

    monkeypatch.setattr(tool_bridge, "find_tool", lambda: r"C:\fake\antigravity-tools.exe")
    monkeypatch.setattr(tool_bridge, "proxy_url", lambda: ("http://127.0.0.1:7895", "ok"))
    monkeypatch.setattr(tool_bridge, "_run_tool", _fake_run)

    ok, detail = tool_bridge.refresh_via_tool()
    assert ok, detail
    assert seen["env"]["HTTPS_PROXY"] == "http://127.0.0.1:7895"
    assert seen["env"]["HTTP_PROXY"] == "http://127.0.0.1:7895"


def test_without_a_node_the_tool_gets_no_proxy_of_ours(monkeypatch):
    """No node configured ⇒ we inject nothing; the tool's own environment stands."""
    import os

    from quota_hub.providers.apps.antigravity import tool_bridge

    seen: dict = {}

    class _Proc:
        returncode = 0
        stdout = "switched"
        stderr = ""

    def _fake_run(binary, env, timeout):
        seen["env"] = env
        return _Proc()

    monkeypatch.setattr(tool_bridge, "find_tool", lambda: r"C:\fake\antigravity-tools.exe")
    monkeypatch.setattr(tool_bridge, "proxy_url", lambda: ("", "no_proxy"))
    monkeypatch.setattr(tool_bridge, "_run_tool", _fake_run)

    ok, detail = tool_bridge.refresh_via_tool()
    assert ok, detail
    assert seen["env"].get("HTTPS_PROXY") == os.environ.get("HTTPS_PROXY")


def test_a_spent_quota_says_so_instead_of_pretending_to_be_logged_out(monkeypatch, tmp_path):
    """Every model answered 429 RESOURCE_EXHAUSTED while the credential was healthy.

    Measured 2026-10-29 23:5x: that is the account's allowance being spent — not a
    login problem and not a node problem. The card must not send him to either, and it
    must not keep the models in the picker as if they could serve a turn.
    """
    import time

    _empty_store(monkeypatch, tmp_path)
    prov = build_provider()
    monkeypatch.setattr(ds, "antigravity_desktop_token_status", lambda: {
        "source": "windows-credential-manager", "present": True,
        "expires_at": time.time() + 1800, "expires_in_seconds": 1800})
    prov.proxy_reason = "ok"

    # Signed in, node fine — and the upstream says the allowance is gone.
    assert prov.signed_in() == (True, "ok")
    prov.quota_exhausted = True
    live, reason = prov.credential_health()
    assert live is False
    assert reason == "quota_exhausted"

    # And a turn that works clears it again.
    prov.quota_exhausted = False
    assert prov.credential_health() == (True, "ok")


def test_classify_renewal_failure_names_the_node_not_the_credential():
    """2026-10-30 15:22, by hand, with the Settings → VPN node in the environment:

    `[Antigravity CLI Error] Token refresh failed: … oauth2.googleapis.com/token:
    client error (Connect). 无法连接 Google 授权服务器，请检查代理设置。`

    That is the node refusing Google, not a dead login — and it must not be answered
    with "open the app and sign in again", because signing in needs the same door.
    """
    from quota_hub.providers.apps.antigravity.api import (
        RENEW_NO_GOOGLE,
        RENEW_OK,
        RENEW_RATE_LIMITED,
        classify_renewal_failure,
    )

    real = (
        "[Antigravity CLI Error] Token refresh failed: Refresh failed for client "
        "[antigravity_enterprise]: Refresh request failed: error sending request for "
        "url (https://oauth2.googleapis.com/token): client error (Connect). "
        "无法连接 Google 授权服务器，请检查代理设置。"
    )
    assert classify_renewal_failure(real) == RENEW_NO_GOOGLE
    assert classify_renewal_failure("HTTP 429 Too Many Requests from upstream") == RENEW_RATE_LIMITED
    assert classify_renewal_failure("退出码 3") == RENEW_OK
    assert classify_renewal_failure("") == RENEW_OK


@pytest.mark.asyncio
async def test_a_stale_lease_with_a_node_that_refuses_google_says_so(monkeypatch, tmp_path):
    """The verdict the card and the turn error both read, for that same measurement."""
    import asyncio
    import time

    from quota_hub.providers.apps.antigravity import tool_bridge

    _empty_store(monkeypatch, tmp_path)
    prov = build_provider()
    monkeypatch.setattr(ds, "antigravity_desktop_token_status", lambda: {
        "source": "windows-credential-manager", "present": True,
        "expires_at": time.time() - 600, "expires_in_seconds": -600})
    prov.proxy_reason = "ok"
    # The node gets *asked* before it can be blamed (2026-10-31: this exact complaint also
    # arrived while the node was passing every Google host).
    monkeypatch.setattr("quota_hub.providers.apps.antigravity.api.probe_network",
                        lambda *_a, **_k: {"ok": False, "failed": ["google-oauth"]})

    class _Proc:
        returncode = 1
        stdout = "[Antigravity CLI] 正在检索目标账号..."
        stderr = "Token refresh failed: error sending request for url (https://oauth2.googleapis.com/token): 无法连接 Google 授权服务器"

    monkeypatch.setattr(tool_bridge, "find_tool", lambda: r"C:\fake\antigravity-tools.exe")
    monkeypatch.setattr(tool_bridge, "_run_tool", lambda *a, **k: _Proc())

    prov.signed_in()  # fires the renewal
    deadline = time.time() + 5
    while time.time() < deadline and prov._verdict is None:
        await asyncio.sleep(0.02)
    assert prov._verdict is not None, "the renewal never answered"
    live, reason = prov.signed_in()
    assert live is False
    assert "节点" in reason, reason
    assert "过期" not in reason, reason
    assert prov.credential_health()[1] == reason


@pytest.mark.asyncio
async def test_a_renewal_that_cannot_get_out_while_the_node_passes_blames_neither(monkeypatch, tmp_path):
    """Measured 2026-10-31 18:4x: the tool could not send its refresh request, and the node
    answered all three Google hosts inside 3 s. Saying "换一个节点" there sends him to swap a
    working node; the one thing that actually re-pushes the slot is opening that app once."""
    import asyncio
    import time

    from quota_hub.providers.apps.antigravity import tool_bridge

    _empty_store(monkeypatch, tmp_path)
    prov = build_provider()
    monkeypatch.setattr(ds, "antigravity_desktop_token_status", lambda: {
        "source": "windows-credential-manager", "present": True,
        "expires_at": time.time() - 865, "expires_in_seconds": -865})
    prov.proxy_reason = "ok"
    monkeypatch.setattr("quota_hub.providers.apps.antigravity.api.probe_network",
                        lambda *_a, **_k: {"ok": True, "failed": []})

    class _Proc:
        returncode = 1
        stdout = "[Antigravity CLI] 正在检索目标账号..."
        stderr = ("[Antigravity CLI Error] Token refresh failed: Refresh failed for client "
                  "[antigravity_enterprise]: Refresh request failed: error sending request for url "
                  "(https://oauth2.googleapis.com/token)")

    monkeypatch.setattr(tool_bridge, "find_tool", lambda: r"C:\fake\antigravity-tools.exe")
    monkeypatch.setattr(tool_bridge, "_run_tool", lambda *a, **k: _Proc())

    prov.signed_in()  # fires the renewal
    deadline = time.time() + 5
    while time.time() < deadline and prov._verdict is None:
        await asyncio.sleep(0.02)
    assert prov._verdict is not None, "the renewal never answered"
    live, reason = prov.credential_health()
    assert live is False
    assert "节点" not in reason, reason
    assert reason == "credential_expired", reason


@pytest.mark.asyncio
async def test_a_retired_account_comes_back_when_the_slot_is_fresh_again(monkeypatch, tmp_path):
    """2026-10-30 21:14: the lease was renewed (60 min of life), the card said `ok`, and
    every turn still answered `no more accounts for ['antigravity']`.

    The single account had been marked "needs re-auth" during the earlier failure and
    nothing looked at it again until a process restart. A retired account is not a
    logout; a restart is not the price of a blip.
    """
    import time

    from quota_hub.providers.base import AccountState

    prov = build_provider()
    acc = prov._access_only_account("antigravity-keychain", "OLD", "cid", "csec", desktop_store=True)
    acc.state = AccountState.REAUTH
    prov.accounts = [acc]
    monkeypatch.setattr(ds, "antigravity_desktop_token_status", lambda: {
        "source": "windows-credential-manager", "present": True,
        "expires_at": time.time() + 1800, "expires_in_seconds": 1800})
    monkeypatch.setattr(ds, "read_antigravity_access_token", lambda *a, **k: "NEW")

    assert await prov.revive_accounts() is True
    assert acc.state is AccountState.ACTIVE
    assert acc.cred.access_token == "NEW"
    assert acc.schedulable() is True


@pytest.mark.asyncio
async def test_revive_renews_the_lease_itself_before_giving_up(monkeypatch, tmp_path):
    """The slot is stale again, but the account that owns it can re-push it: wait for it."""
    import time

    from quota_hub.providers.apps.antigravity import tool_bridge
    from quota_hub.providers.base import AccountState

    prov = build_provider()
    acc = prov._access_only_account("antigravity-keychain", "OLD", "cid", "csec", desktop_store=True)
    acc.state = AccountState.REAUTH
    prov.accounts = [acc]

    slot = {"present": True, "expires_at": time.time() - 60, "expires_in_seconds": -60}
    monkeypatch.setattr(ds, "antigravity_desktop_token_status", lambda: dict(slot))
    monkeypatch.setattr(ds, "read_antigravity_access_token",
                        lambda *a, **k: None if slot["expires_in_seconds"] <= 0 else "FRESH")
    asked = []

    def _fake_refresh(**_kw):
        slot["expires_at"] = time.time() + 3600
        slot["expires_in_seconds"] = 3600
        asked.append(1)
        return True, "ok"

    monkeypatch.setattr(tool_bridge, "refresh_via_tool", _fake_refresh)

    assert await prov.revive_accounts() is True
    assert asked == [1]
    assert acc.cred.access_token == "FRESH"
    assert acc.state is AccountState.ACTIVE


@pytest.mark.asyncio
async def test_a_stale_slot_still_gives_the_leg_an_account(monkeypatch, tmp_path):
    """Discovery used to require a *fresh* lease: boot 5 minutes after an expiry and the
    leg had zero accounts forever — nothing re-discovered them until a restart.
    """
    import time

    _empty_store(monkeypatch, tmp_path)
    prov = build_provider()
    monkeypatch.setattr(ds, "antigravity_desktop_token_status", lambda: {
        "source": "windows-credential-manager", "present": True,
        "expires_at": time.time() - 60, "expires_in_seconds": -60})
    monkeypatch.setattr(ds, "read_antigravity_access_token",
                        lambda allow_stale=False: "STALE" if allow_stale else None)

    accs = await prov.discover_accounts()
    assert len(accs) == 1, "过期租约也要有一条账号，续期是发回合前那一步的事"
    assert accs[0].cred.access_token == "STALE"


@pytest.mark.asyncio
async def test_the_scheduler_lets_a_leg_un_retire_its_accounts_before_saying_none():
    """`no more accounts` must not be the last word when the credential is ours to renew."""
    from quota_hub.providers.base import Account, AccountState, Credential, Provider
    from quota_hub.scheduler import Scheduler

    class _Reviving(Provider):
        name = "reviving"

        def __init__(self):
            super().__init__({}, None)
            self.served_models = ["reviving/m1"]
            self.acc = Account(id="a1", provider="reviving", label="a1", cred=Credential(access_token="t"))
            self.acc.state = AccountState.REAUTH
            self.accounts = [self.acc]
            self.woke = 0

        def capabilities(self):
            from quota_hub.providers.base import Capabilities
            return Capabilities(chat=True, stream=True, tools=True)

        async def revive_accounts(self):
            self.woke += 1
            self.acc.state = AccountState.ACTIVE
            return True

        async def ensure_fresh(self, acc):
            return None

        async def chat(self, acc, oai_req):
            return {"choices": [{"message": {"content": "woke"}, "finish_reason": "stop"}]}

    prov = _Reviving()
    sched = Scheduler({prov.name: prov})
    out = await sched.dispatch(prov.name, {"model": "reviving/m1", "messages": [{"role": "user", "content": "x"}]}, None, stream=False)
    assert prov.woke == 1, "调度器没有先给这条腿一次把账号拉回来的机会"
    assert (out["choices"][0]["message"]["content"]) == "woke"
