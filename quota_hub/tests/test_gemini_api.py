"""Gemini 这条腿的判据：Google 账号的 token 走 Code Assist（v1internal），并且真回 tool_calls。

为什么这份测试整档重写：这条腿原先打的是 `generativelanguage.googleapis.com/v1beta`——那是
**API key 的门**。实测装好的 0.65.0 里 `CODE_ASSIST_ENDPOINT = https://cloudcode-pa.googleapis.com`
+ `CODE_ASSIST_API_VERSION = v1internal`，也就是 `oauth-personal` 那个 token 只在这扇门上有效。
形状接错门时，翻译写得再对也永远服务不了一次回合——那正是"半成品"。

替身全程合成：不读真 `~/.gemini`、不开浏览器、不打网络。

Run:
    cd Code && scripts/run_tests.sh -j 1 quota_hub/tests/test_gemini_api.py
"""

from __future__ import annotations

import asyncio
import json
import time
import types

import httpx
import pytest
from quota_hub.providers.apps.gemini.api import GeminiApiProvider, read_credential

HOST = "https://cloudcode.test"
GENERATE = "/v1internal:generateContent"
STREAM = "/v1internal:streamGenerateContent"  # `?alt=sse` is a query, not part of the path
MODELS = "/v1internal:fetchAvailableModels"
LOAD = "/v1internal:loadCodeAssist"


def _store(tmp_path, *, access="test-access", expires_in=3600.0, present=True, refresh="r"):
    path = tmp_path / "oauth_creds.json"
    if not present:
        return str(path)
    data = {"access_token": access, "refresh_token": refresh, "token_type": "Bearer"}
    if expires_in is not None:
        data["expiry_date"] = int((time.time() + expires_in) * 1000)  # the CLI's own unit
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def _leg(http, cfg: dict | None = None):
    prov = GeminiApiProvider({"host": HOST, **(cfg or {})}, http)
    # The mock transport IS the wire for this test; whether a node exists is a separate
    # judgement (pinned by the egress tests below).
    prov.cloudcode.http = http
    prov.proxy_reason = "ok"
    # The node verdict is cached per leg and refreshed by `_rebind_node`; seed the cache so
    # that refresh does not overwrite the "no node in this test" decision above.
    prov._proxy_seen = (time.time(), "ok")
    return prov


def _json(routes: dict[str, object]):
    """A MockTransport over the Code Assist routes; anything else answers 404 with a marker."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path in routes:
            body = routes[request.url.path]
            if isinstance(body, tuple):
                return httpx.Response(body[0], text=body[1])
            return httpx.Response(200, json=body)
        return httpx.Response(404, json={"error": {"message": f"unrouted {request.url.path}"}})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), seen


def _answer(*parts, usage=(11, 4, 15)):
    return {"response": {"candidates": [{"content": {"parts": list(parts)}, "finishReason": "STOP"}],
                         "usageMetadata": {"promptTokenCount": usage[0], "candidatesTokenCount": usage[1],
                                           "totalTokenCount": usage[2]}}}


def test_a_function_call_comes_back_as_openai_tool_calls(tmp_path, monkeypatch):
    monkeypatch.setenv("QUOTA_HUB_GEMINI_STORE", _store(tmp_path))
    http, _seen = _json({
        LOAD: {"cloudaicompanionProject": {"id": "p-42"}},
        GENERATE: _answer({"functionCall": {"name": "read_file", "args": {"path": "/etc/hostname"}}}),
    })

    async def _go():
        prov = _leg(http)
        acc = (await prov.discover_accounts())[0]
        tools = [{"type": "function", "function": {
            "name": "read_file", "description": "read",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        }}]
        return await prov.chat(acc, {
            "model": "gemini_api/gemini-2.5-pro", "messages": [{"role": "user", "content": "hi"}], "tools": tools,
        })

    out = asyncio.run(asyncio.wait_for(_go(), timeout=30))

    choice = out["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    call = choice["message"]["tool_calls"][0]
    assert call["function"]["name"] == "read_file"
    assert json.loads(call["function"]["arguments"]) == {"path": "/etc/hostname"}
    assert out["usage"]["total_tokens"] == 15


def test_the_request_is_the_code_assist_envelope_not_the_api_key_one(tmp_path, monkeypatch):
    """信封判据：`project`/`model` 在外层，工具在里层用 `functionDeclarations`。"""
    monkeypatch.setenv("QUOTA_HUB_GEMINI_STORE", _store(tmp_path))
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == GENERATE:
            captured.update(json.loads(request.content.decode()))
            return httpx.Response(200, json=_answer({"text": "ok"}))
        if request.url.path == LOAD:
            return httpx.Response(200, json={"cloudaicompanionProject": {"id": "p-42"}})
        return httpx.Response(404, json={})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def _go():
        prov = _leg(http)
        acc = (await prov.discover_accounts())[0]
        await prov.chat(acc, {"model": "gemini_api/gemini-2.5-flash",
                              "messages": [{"role": "user", "content": "hi"}],
                              "tools": [{"type": "function", "function": {"name": "terminal", "parameters": {"type": "object"}}}]})

    asyncio.run(asyncio.wait_for(_go(), timeout=30))

    assert captured["project"] == "p-42" and captured["model"] == "gemini-2.5-flash"
    assert captured["request"]["tools"][0]["functionDeclarations"][0]["name"] == "terminal"
    assert captured["request"]["contents"][0]["parts"][0]["text"] == "hi"
    # 我们不再敲那扇 API-key 的门：这一条腿的每一次调用都在 v1internal 上。
    assert all(not p.startswith("/v1beta") for p in [GENERATE, LOAD])


def test_model_names_come_only_from_fetchAvailableModels(tmp_path, monkeypatch):
    """裁定 204.5：名字只认上游自报。没有默认表，读不到就是没有。"""
    monkeypatch.setenv("QUOTA_HUB_GEMINI_STORE", _store(tmp_path))
    http, _seen = _json({
        LOAD: {"cloudaicompanionProject": {"id": "p-42"}},
        MODELS: {"models": {
            "gemini-2.5-pro": {"displayName": "Gemini 2.5 Pro"},
            "gemini-2.5-flash": {},
            "chat_internal": {},           # 内部号要滤掉
            "imagen-3": {},                # 非聊天模型要滤掉
        }},
    })

    async def _go():
        prov = _leg(http)
        await prov.discover_accounts()
        return prov.served_models

    assert asyncio.run(asyncio.wait_for(_go(), timeout=30)) == ("gemini_api/gemini-2.5-pro", "gemini_api/gemini-2.5-flash")


def test_a_refused_model_list_yields_no_models_rather_than_a_default_table(tmp_path, monkeypatch):
    monkeypatch.setenv("QUOTA_HUB_GEMINI_STORE", _store(tmp_path))
    http, _seen = _json({LOAD: {"cloudaicompanionProject": {"id": "p-42"}},
                         MODELS: (503, "upstream unavailable")})

    async def _go():
        prov = _leg(http)
        await prov.discover_accounts()
        return prov.served_models

    assert asyncio.run(asyncio.wait_for(_go(), timeout=30)) == ()


def test_plain_text_stays_plain_text(tmp_path, monkeypatch):
    monkeypatch.setenv("QUOTA_HUB_GEMINI_STORE", _store(tmp_path))
    http, _seen = _json({LOAD: {"cloudaicompanionProject": {"id": "p-42"}}, GENERATE: _answer({"text": "hello"})})

    async def _go():
        prov = _leg(http)
        acc = (await prov.discover_accounts())[0]
        return await prov.chat(acc, {"model": "gemini_api/gemini-2.5-flash", "messages": [{"role": "user", "content": "hi"}]})

    out = asyncio.run(asyncio.wait_for(_go(), timeout=30))
    assert out["choices"][0]["message"]["content"] == "hello"
    assert "tool_calls" not in out["choices"][0]["message"]
    assert out["choices"][0]["finish_reason"] == "stop"


def test_a_streamed_answer_carries_text_and_tool_chunks(tmp_path, monkeypatch):
    monkeypatch.setenv("QUOTA_HUB_GEMINI_STORE", _store(tmp_path))
    sse = (
        b'data: {"response":{"candidates":[{"content":{"parts":[{"text":"he"}]}}]}}\n\n'
        b'data: {"response":{"candidates":[{"content":{"parts":[{"text":"llo"}]}}]}}\n\n'
        b'data: {"response":{"candidates":[{"content":{"parts":[{"functionCall":{"name":"read_file","args":{"path":"/x"}}}]}}]}}\n\n'
        b"data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == STREAM:
            return httpx.Response(200, content=sse, headers={"content-type": "text/event-stream"})
        if request.url.path == LOAD:
            return httpx.Response(200, json={"cloudaicompanionProject": {"id": "p-42"}})
        return httpx.Response(404, json={})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def _go():
        prov = _leg(http)
        acc = (await prov.discover_accounts())[0]
        return [c async for c in prov.chat_stream(acc, {"model": "gemini_api/gemini-2.5-flash", "messages": [{"role": "user", "content": "hi"}]})]

    chunks = asyncio.run(asyncio.wait_for(_go(), timeout=30))
    text = "".join(c["choices"][0]["delta"].get("content") or "" for c in chunks)
    calls = [tc for c in chunks for tc in (c["choices"][0]["delta"].get("tool_calls") or [])]
    assert text == "hello"
    assert calls and calls[0]["function"]["name"] == "read_file"


def test_the_cli_being_installed_is_enough_for_a_card_but_not_for_models(tmp_path, monkeypatch):
    """`present()` 是"这台有这个东西"，`credential_health` 才是"还没登录"。"""
    monkeypatch.setenv("QUOTA_HUB_GEMINI_STORE", _store(tmp_path, present=False))
    monkeypatch.setattr("quota_hub.providers.apps.gemini.api.shutil", types.SimpleNamespace(which=lambda name: r"C:\fake\gemini.exe"))
    http, _seen = _json({})

    async def _go():
        prov = _leg(http)
        assert prov.present() is True
        assert prov.credential_health() == (False, "no_desktop_credential")
        assert prov.login_plan()["kind"] == "oauth"
        assert await prov.discover_accounts() == []
        assert prov.served_models == ()

    asyncio.run(asyncio.wait_for(_go(), timeout=30))


def test_no_cli_and_no_credential_means_no_card(tmp_path, monkeypatch):
    monkeypatch.setenv("QUOTA_HUB_GEMINI_STORE", _store(tmp_path, present=False))
    monkeypatch.setattr("quota_hub.providers.apps.gemini.api.shutil", types.SimpleNamespace(which=lambda name: None))
    http, _seen = _json({})

    async def _go():
        prov = _leg(http)
        assert prov.present() is False

    asyncio.run(asyncio.wait_for(_go(), timeout=30))


def test_an_expired_token_with_a_way_to_refresh_is_still_live(tmp_path, monkeypatch):
    monkeypatch.setenv("QUOTA_HUB_GEMINI_STORE", _store(tmp_path, expires_in=-60))
    monkeypatch.setattr("quota_hub.providers.apps.gemini.oauth.discover_client", lambda: ("cid", "secret"))
    http, _seen = _json({})

    async def _go():
        assert _leg(http).credential_health() == (True, "ok")

    asyncio.run(asyncio.wait_for(_go(), timeout=30))


def test_an_expired_token_we_cannot_remint_reports_expired(tmp_path, monkeypatch):
    monkeypatch.setenv("QUOTA_HUB_GEMINI_STORE", _store(tmp_path, expires_in=-60, refresh=""))
    monkeypatch.setattr("quota_hub.providers.apps.gemini.oauth.discover_client", lambda: None)
    http, _seen = _json({})

    async def _go():
        prov = _leg(http)
        assert prov.credential_health() == (False, "credential_expired")
        assert await prov.discover_accounts() == []

    asyncio.run(asyncio.wait_for(_go(), timeout=30))


def test_a_dead_node_keeps_the_signed_in_verdict_but_not_the_pickability(tmp_path, monkeypatch):
    """三问三态：登录过没有 ≠ 现在通不通。掉线不许把卡片退回加号。"""
    monkeypatch.setenv("QUOTA_HUB_GEMINI_STORE", _store(tmp_path))
    http, _seen = _json({})

    async def _go():
        prov = _leg(http)
        prov.proxy_reason = "proxy_unreachable"
        assert prov.signed_in() == (True, "ok")
        assert prov.credential_health() == (False, "proxy_unreachable")

    asyncio.run(asyncio.wait_for(_go(), timeout=30))


def test_ensure_fresh_remints_the_access_token_instead_of_failing_the_turn(tmp_path, monkeypatch):
    store = _store(tmp_path, expires_in=-60)
    monkeypatch.setenv("QUOTA_HUB_GEMINI_STORE", store)
    monkeypatch.setattr("quota_hub.providers.apps.gemini.oauth.discover_client", lambda: ("cid", "secret"))
    refreshed: list[bool] = []

    def _fake_refresh():
        refreshed.append(True)
        (tmp_path / "oauth_creds.json").write_text(json.dumps(
            {"access_token": "new-access", "refresh_token": "r", "expiry_date": int((time.time() + 3600) * 1000)}
        ), encoding="utf-8")
        return True

    monkeypatch.setattr("quota_hub.providers.apps.gemini.oauth.refresh_credential", _fake_refresh)
    http, _seen = _json({LOAD: {"cloudaicompanionProject": {"id": "p-42"}}})

    async def _go():
        prov = _leg(http)
        acc = (await prov.discover_accounts())[0]
        acc.cred.expires_at = time.time() - 10
        await prov.ensure_fresh(acc)
        return acc

    acc = asyncio.run(asyncio.wait_for(_go(), timeout=30))
    assert refreshed == [True]
    assert acc.cred.access_token == "new-access"
    assert not acc.cred.is_expired()


def test_the_credential_reader_treats_an_unparsable_file_as_no_credential(tmp_path, monkeypatch):
    path = tmp_path / "oauth_creds.json"
    path.write_text("{ not json", encoding="utf-8")
    monkeypatch.setenv("QUOTA_HUB_GEMINI_STORE", str(path))

    assert read_credential() is None


def test_renewing_without_the_node_fails_before_the_wire(monkeypatch, tmp_path):
    """Same rule as the antigravity leg (measured 2026-10-29): a renewal is a network call,
    so without the node it cannot succeed — refuse before the ten-second timeout and keep
    the account out of a cooldown it did not earn."""
    from quota_hub.providers.base import UpstreamError
    from quota_hub.providers.apps.gemini import api as ga

    monkeypatch.setattr(ga, "egress_proxy", lambda: ("", "proxy_unreachable"))
    prov = GeminiApiProvider({"host": HOST}, None)
    prov._proxy_seen = None
    prov.proxy_reason = "proxy_unreachable"

    from quota_hub.providers.base import Account, Credential

    acc = Account(id="a", provider="gemini_api", cred=Credential(access_token="old", expires_at=1.0))

    calls = {"n": 0}

    def _boom():
        calls["n"] += 1
        raise AssertionError("renewal must not be attempted without the node")

    from quota_hub.providers.apps.gemini import oauth as g_oauth

    monkeypatch.setattr(g_oauth, "refresh_credential", _boom)

    with pytest.raises(UpstreamError) as exc:
        asyncio.run(prov.ensure_fresh(acc))

    assert "VPN" in str(exc.value)
    assert exc.value.cooldown == 0.0, "a network condition must not cool the account down"
    assert calls["n"] == 0


def test_a_flaky_model_list_is_retried_before_the_leg_stays_empty(monkeypatch):
    """Measured 2026-10-29: the names were only ever read inside `discover_accounts`, so one
    flaky answer at sign-in left this leg with zero models until the next restart."""
    from quota_hub.providers.base import Account, Credential

    prov = GeminiApiProvider({"host": HOST}, None)
    prov._proxy_seen = (time.time(), "ok")
    prov.credential_health = lambda: (True, "ok")
    acc = Account(id="a", provider="gemini_api", cred=Credential(access_token="tok"))
    calls = {"n": 0}

    async def _names(_token):
        calls["n"] += 1
        return [] if calls["n"] == 1 else ["gemini_api/gemini-2.5-pro"]

    monkeypatch.setattr(prov, "_discovered_models", _names)

    assert asyncio.run(prov.refresh_models(acc)) == ["gemini_api/gemini-2.5-pro"]
    assert calls["n"] == 2
    assert prov.served_models == ("gemini_api/gemini-2.5-pro",)


def test_a_model_list_that_stays_empty_keeps_what_we_had(monkeypatch):
    from quota_hub.providers.base import Account, Credential

    prov = GeminiApiProvider({"host": HOST}, None)
    prov.served_models = ("gemini_api/old-one",)
    prov.credential_health = lambda: (True, "ok")
    acc = Account(id="a", provider="gemini_api", cred=Credential(access_token="tok"))

    async def _names(_token):
        return []

    monkeypatch.setattr(prov, "_discovered_models", _names)

    assert asyncio.run(prov.refresh_models(acc)) is None
    assert prov.served_models == ("gemini_api/old-one",)
