"""Offline contracts for the CodeBuddy subscription HTTP leg.

Three things are pinned here, each because it was measured live first:

1. The upstream refuses non-stream (`{"code":11101,"msg":"Non-stream chat request
   is currently not supported"}`, seen 2026-10-12 from both failover origins) — so
   `chat()` must never send a JSON request body, and must fold the split SSE
   `tool_calls` deltas back into one completion.
2. The credential comes from the desktop app's own record and must never leak into
   any surface that can be logged.
3. Renewal is ours to perform (`X-Refresh-Token`), and a failed renewal must be a
   reauth verdict naming the one human action — not a silent retry on a dead token.

No network, no vendor calls, no real credential: the store is a temp file with a
fake JWT, and the transport is a recording fake.
"""

from __future__ import annotations

import base64
import json
import time

import pytest

from quota_hub.providers.base import Account, Credential, UpstreamError
from quota_hub.providers.apps.workbuddy import WorkbuddyApiProvider
from quota_hub.tokens import desktop_stores as ds

TOOLS = [{"type": "function", "function": {
    "name": "get_weather", "description": "Report weather for a city.",
    "parameters": {"type": "object", "properties": {"city": {"type": "string"}},
                   "required": ["city"]}}}]


def _fake_jwt(exp_offset_seconds: float) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": int(time.time() + exp_offset_seconds), "aud": "account"}).encode()
    ).decode().rstrip("=")
    return f"{header}.{payload}.sig"


class _FakeStream:
    def __init__(self, lines, status=200):
        self.lines, self.status_code = lines, status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def aiter_lines(self):
        for line in self.lines:
            yield line


class _FakeHttp:
    def __init__(self, sse_lines=None, post_response=None, get_response=None):
        self.sse = sse_lines or []
        self.post_response = post_response
        self.get_response = get_response
        self.stream_calls, self.post_calls, self.get_calls = [], [], []

    def stream(self, method, url, headers=None, json=None, **kw):
        self.stream_calls.append({"method": method, "url": url, "headers": headers, "json": json})
        return _FakeStream(self.sse)

    async def post(self, url, headers=None, json=None, **kw):
        self.post_calls.append({"url": url, "headers": headers, "json": json})
        return self.post_response

    async def get(self, url, headers=None, **kw):
        self.get_calls.append({"url": url, "headers": headers})
        return self.get_response


class _Resp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def json(self):
        return self._payload


def _sse_with_split_tool_call():
    frames = [
        {"choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "id": "call-1", "function": {"name": "get_weather", "arguments": ""}}]}}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": '{"city": '}}]}}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": '"北京"}'}}]}}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
         "usage": {"prompt_tokens": 196, "completion_tokens": 77, "total_tokens": 273}},
    ]
    return ["data: " + json.dumps(f) for f in frames] + ["data: [DONE]", "", "event: x"]


def _provider(http):
    return WorkbuddyApiProvider({
        "base_url": "https://www.workbuddy.cn", "dialect": "openai",
        "chat_path": "/v2/chat/completions", "token_source": "codebuddy_desktop_store",
        "models": ["workbuddy_api/hy3"], "header_templates": [
            {"name": "X-Agent-Intent", "value": "craft"}],
        "accounts": [{"id": "desktop"}],
    }, http)


def _catalog_provider(http):
    """The leg as it is configured in production: the card id is ``workbuddy``."""
    return WorkbuddyApiProvider({
        "base_url": "https://www.workbuddy.cn", "dialect": "openai",
        "chat_path": "/v2/chat/completions", "token_source": "codebuddy_desktop_store",
        "leg": "workbuddy", "header_templates": [
            {"name": "X-Agent-Intent", "value": "craft"}],
        "accounts": [{"id": "desktop"}],
    }, http)


def _catalog_payload() -> dict:
    """The vendor's own catalogue, trimmed to the shape that matters.

    Measured 2026-11-09 on this machine (`GET /v2/enterprises/personal/models`):
    the roster is the agent tagged ``default``, and every model row carries the
    window menu it sells, its own ceiling, its output cap and its thinking levels.
    """
    return {
        "code": 0,
        "data": {
            "agents": [
                {"name": "cli", "tags": ["cli", "default"],
                 "models": ["auto", "hy4free", "hy4std", "hy3"]},
                {"name": "general-purpose", "tags": ["cli", "general-purpose"], "models": []},
                {"name": "promptHookEvaluator", "tags": ["cli"], "models": ["lite"]},
            ],
            "models": [
                {"id": "auto", "name": "Auto", "maxInputTokens": 256000,
                 "maxOutputTokens": 32000, "reasoning": {"effort": "high"}},
                {"id": "hy4free", "name": "Hy4", "maxInputTokens": 960000,
                 "maxOutputTokens": 64000,
                 "contextWindow": {"defaultLength": 300000,
                                    "supportedLengths": [300000, 600000, 960000]},
                 "reasoning": {"defaultEffort": "high", "supportedEfforts": ["high"]}},
                {"id": "hy4std", "name": "Hy4", "maxInputTokens": 960000,
                 "maxOutputTokens": 64000,
                 "contextWindow": {"defaultLength": 300000,
                                    "supportedLengths": [300000, 600000, 960000]},
                 "reasoning": {"defaultEffort": "high", "supportedEfforts": ["high"]}},
                {"id": "hy3", "name": "Hy3", "maxInputTokens": 262144},
            ],
        },
    }


@pytest.fixture
def no_store(monkeypatch):
    """Pin the hermetic case: the machine's real CodeBuddy login must not leak in."""
    monkeypatch.setattr(ds, "read_codebuddy_token", lambda: None)


def _account(token="old", refresh="rt", expires_at=0.0):
    return Account(id="desktop", provider="workbuddy_api",
                   cred=Credential(access_token=token, refresh_token=refresh,
                                   expires_at=expires_at,
                                   extra={"token_source": "codebuddy_desktop_store"}))


@pytest.mark.asyncio
async def test_chat_always_streams_and_folds_split_tool_calls():
    http = _FakeHttp(sse_lines=_sse_with_split_tool_call())
    prov = _provider(http)
    acc = _account()
    got = await prov.chat(acc, {"model": "workbuddy_api/hy3", "tools": TOOLS,
                                "messages": [{"role": "user", "content": "weather?"}]})

    assert http.post_calls == [], "chat() must not issue a JSON (non-stream) request"
    assert len(http.stream_calls) == 1
    body = http.stream_calls[0]["json"]
    assert body["stream"] is True
    assert body["model"] == "hy3", "the unified id must be mapped before sending"
    hdrs = {k.lower(): v for k, v in http.stream_calls[0]["headers"].items()}
    assert hdrs["x-agent-intent"] == "craft", "templated headers must reach the wire"
    assert hdrs["authorization"] == "Bearer old"
    assert http.stream_calls[0]["url"] == "https://www.workbuddy.cn/v2/chat/completions"

    call = got["choices"][0]["message"]["tool_calls"][0]
    assert call["id"] == "call-1"
    assert call["function"]["name"] == "get_weather"
    assert json.loads(call["function"]["arguments"]) == {"city": "北京"}
    assert got["choices"][0]["finish_reason"] == "tool_calls"
    assert got["usage"]["prompt_tokens"] == 196
    assert got["model"] == "workbuddy_api/hy3", "the caller must see the id it asked for"


@pytest.mark.asyncio
async def test_ensure_fresh_renews_near_expiry_and_keeps_it_in_memory(no_store):
    new_access = _fake_jwt(3600)
    http = _FakeHttp(post_response=_Resp({"data": {"accessToken": new_access,
                                                   "refreshToken": "rt2"}}))
    prov = _provider(http)
    acc = _account(token="stale", refresh="rt", expires_at=time.time() + 30)
    await prov.ensure_fresh(acc)

    url, headers = http.post_calls[0]["url"], http.post_calls[0]["headers"]
    assert url == "https://www.workbuddy.cn/v2/auth/token/refresh"
    assert headers["X-Refresh-Token"] == "rt" and headers["X-Auth-Refresh-Source"] == "plugin"
    assert acc.cred.access_token == new_access
    assert acc.cred.refresh_token == "rt2", "rotated refresh token must be kept"
    assert acc.cred.expires_at > time.time() + 3000


@pytest.mark.asyncio
async def test_ensure_fresh_leaves_a_healthy_token_alone(no_store):
    http = _FakeHttp()
    prov = _provider(http)
    acc = _account(token="fine", refresh="rt", expires_at=time.time() + 86400)
    await prov.ensure_fresh(acc)
    assert http.post_calls == [] and acc.cred.access_token == "fine"


@pytest.mark.asyncio
async def test_failed_renewal_is_a_reauth_verdict_not_a_silent_retry(no_store):
    http = _FakeHttp(post_response=_Resp({"msg": "expired"}, status=401))
    prov = _provider(http)
    acc = _account(token="stale", refresh="rt", expires_at=time.time() + 5)
    with pytest.raises(UpstreamError) as e:
        await prov.ensure_fresh(acc)
    assert getattr(e.value, "reauth", False) is True
    assert "sign in" in str(e.value).lower() or "登录" in str(e.value)
    assert len(http.post_calls) == 1, "one attempt; no hammering a refused refresh"


def test_status_surface_never_carries_token_material(tmp_path, monkeypatch):
    access, refresh = _fake_jwt(7200), _fake_jwt(9000)
    rec = tmp_path / "CodeBuddyExtension" / "Data" / "Public" / "auth"
    rec.mkdir(parents=True)
    (rec / "Tencent-Cloud.coding-copilot.info").write_text(json.dumps(
        {"auth": {"accessToken": access, "refreshToken": refresh, "tokenType": "Bearer",
                 "expiresAt": int((time.time() + 7200) * 1000)}}), encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    st = ds.codebuddy_token_status()
    assert st["present"] is True and st["can_refresh"] is True
    assert 7000 < st["expires_in_seconds"] < 7300
    dumped = json.dumps(st)
    assert access not in dumped and refresh not in dumped
    assert "eyJ" not in dumped, "a JWT's own prefix must never reach a loggable surface"


def test_missing_or_broken_store_reads_as_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert ds.read_codebuddy_token() is None
    assert ds.codebuddy_token_status()["present"] is False

    rec = tmp_path / "CodeBuddyExtension" / "Data" / "Public" / "auth"
    rec.mkdir(parents=True)
    broken = rec / "Tencent-Cloud.coding-copilot.info"
    broken.write_text("{not json", encoding="utf-8")
    assert ds.read_codebuddy_token() is None
    broken.write_text(json.dumps({"auth": {"tokenType": "Bearer"}}), encoding="utf-8")
    assert ds.read_codebuddy_token() is None, "no access token must not yield an empty credential"


# --- the roster and the windows come from the vendor, not from a copy --------


@pytest.mark.asyncio
async def test_the_roster_is_the_vendor_s_own_catalogue(no_store):
    """The names in the picker are the vendor's, read now — never our copy of them.

    Measured 2026-11-09: the vendor's roster names `hy4free` / `hy4std` (display
    name "Hy4") and `auto`; the list we had copied by hand (10-13) still said
    `hy4-preview`, `hy3-x` and `minimax-m2.7` — names the vendor no longer sells
    (user 11-09 18:4x: 「别人Workbuddy都是Hy4了。你还是Hy4 Preview.」).
    """
    http = _FakeHttp(get_response=_Resp(_catalog_payload()))
    prov = _catalog_provider(http)

    got = await prov.refresh_models(_account(token="fine", expires_at=time.time() + 86400))

    assert got == ["workbuddy/auto", "workbuddy/hy4free", "workbuddy/hy4std", "workbuddy/hy3"], got
    assert prov.served_models == tuple(got), prov.served_models
    call = http.get_calls[0]
    assert call["url"] == "https://www.workbuddy.cn/v2/enterprises/personal/models", call["url"]
    assert call["headers"]["authorization"] == "Bearer fine"
    assert call["headers"]["X-Agent-Intent"] == "craft", "the same headers the chat uses"


@pytest.mark.asyncio
async def test_each_model_publishes_its_own_window_menu(no_store):
    """The window dial must answer for *this* model, in the vendor's own numbers.

    Measured 2026-11-09: the hy4 rows sell a menu (300000 / 600000 / 960000,
    default 300000) and `auto` sells none and has one fixed window. Before this
    the leg said nothing about any window, so the picker had to invent one out of
    Plobi's own stale table (user 11-09 18:4x: 「上下文调整选项根本不符合这个
    模型」).
    """
    http = _FakeHttp(get_response=_Resp(_catalog_payload()))
    prov = _catalog_provider(http)
    await prov.refresh_models(_account(token="fine", expires_at=time.time() + 86400))

    hy4 = prov.tuning("workbuddy/hy4free")
    assert hy4["context"]["choices"] == [300000, 600000, 960000], hy4
    assert hy4["context"]["default"] == 300000
    assert hy4["context"]["adjustable"] is True
    assert hy4["max_output"] == 64000
    assert hy4["efforts"] == ["high"] and hy4["default_effort"] == "high", hy4

    auto = prov.tuning("workbuddy/auto")
    assert auto["context"]["choices"] == [256000], auto
    assert auto["context"]["adjustable"] is False, "one window is not a menu"
    assert auto["max_output"] == 32000


@pytest.mark.asyncio
async def test_a_failed_catalogue_read_invents_nothing(no_store):
    """A vendor that does not answer must not leave a list of our own behind.

    The leg starts with no roster at all: a name we made up is a name that may
    not work, and the picker must not promise it.
    """
    http = _FakeHttp(get_response=_Resp({"code": 1}, status=500))
    prov = _catalog_provider(http)
    assert prov.served_models == (), "no copy of our own may sit in the roster"

    got = await prov.refresh_models(_account(token="fine", expires_at=time.time() + 86400))

    assert got is None, "a failed read keeps what we had; it does not make one up"
    assert prov.served_models == (), prov.served_models
