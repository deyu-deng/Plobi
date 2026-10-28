"""Gemini 这条腿的判据：原生 `functionCall` 必须被翻成 OpenAI `tool_calls`。

为什么这条腿值得单独写：ACP 那一路（opencode 实测）里工具是**上游闭环执行**、
回到 Plobi 的是一条成稿，`tool_calls` 恒为 0——L1 没法据此决定下一步。CodeBuddy／
Antigravity 那两条之所以能带工具，是因为它们走的是"本机凭据 → 厂商 HTTP → 原生
function call"。Gemini 这一笔就是同一个形状（裁定 204）。

本文件的替身全都用合成凭据与 MockTransport，**不读、不碰真 `~/.gemini`**。

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

BASE = "https://gemini.test/v1beta"


def _store(tmp_path, *, access="test-access", expires_in=3600.0, present=True):
    path = tmp_path / "oauth_creds.json"
    if not present:
        return str(path)
    data = {"access_token": access, "refresh_token": "r", "token_type": "Bearer"}
    if expires_in is not None:
        data["expiry_date"] = int((time.time() + expires_in) * 1000)  # the CLI's own unit
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def _models(*names: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={"models": [{"name": f"models/{n}", "supportedGenerationMethods": ["generateContent"]} for n in names]},
    )


def _http(routes: dict[str, httpx.Response]):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path in routes:
            return routes[request.url.path]
        return httpx.Response(404, json={"error": "route"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _leg(http, cfg: dict | None = None):
    """A provider whose cloud calls go through the test's mock transport.

    In production the leg builds its own client when a node is configured (that is how
    Settings → VPN reaches it); a test that wants to assert the wire format has to use
    its mock instead, so the dedicated client is cleared here — deliberately, once.
    """
    prov = GeminiApiProvider({"base_url": BASE, **(cfg or {})}, http)
    prov._proxied = None
    # The mock transport is the wire here; whether a node exists is a separate判据
    # (pinned by the three egress tests below).
    prov.proxy_reason = "ok"
    return prov


def test_a_function_call_comes_back_as_openai_tool_calls(tmp_path, monkeypatch):
    monkeypatch.setenv("QUOTA_HUB_GEMINI_STORE", _store(tmp_path))
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return _models("gemini-2.5-flash")
        if request.url.path.endswith(":generateContent"):
            seen.update(json.loads(request.content.decode()))
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {
                            "content": {
                                "parts": [{"functionCall": {"name": "read_file", "args": {"path": "/etc/hostname"}}}]
                            },
                            "finishReason": "STOP",
                        }
                    ],
                    "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 3, "totalTokenCount": 15},
                },
            )
        return httpx.Response(404, json={"error": "route"})

    async def _go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            prov = _leg(http)
            assert prov.credential_health() == (True, "ok")
            acc = (await prov.discover_accounts())[0]
            assert prov.served_models == ("gemini_api/gemini-2.5-flash",)

            tools = [{"type": "function", "function": {"name": "read_file", "description": "read", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}}]
            out = await prov.chat(acc, {"model": "gemini_api/gemini-2.5-flash", "messages": [{"role": "user", "content": "hi"}], "tools": tools})

        # the tool schema reached the vendor, in its own dialect
        assert seen["tools"][0]["functionDeclarations"][0]["name"] == "read_file"
        choice = out["choices"][0]
        assert choice["finish_reason"] == "tool_calls"
        call = choice["message"]["tool_calls"][0]
        assert call["function"]["name"] == "read_file"
        assert json.loads(call["function"]["arguments"]) == {"path": "/etc/hostname"}
        assert out["usage"]["total_tokens"] == 15

    asyncio.run(asyncio.wait_for(_go(), timeout=30))


def test_plain_text_stays_plain_text(tmp_path, monkeypatch):
    monkeypatch.setenv("QUOTA_HUB_GEMINI_STORE", _store(tmp_path))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return _models("gemini-2.5-flash")
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "hello"}]}, "finishReason": "STOP"}]})

    async def _go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            prov = _leg(http)
            acc = (await prov.discover_accounts())[0]
            out = await prov.chat(acc, {"model": "gemini_api/gemini-2.5-flash", "messages": [{"role": "user", "content": "hi"}]})
        assert out["choices"][0]["message"]["content"] == "hello"
        assert "tool_calls" not in out["choices"][0]["message"]
        assert out["choices"][0]["finish_reason"] == "stop"

    asyncio.run(asyncio.wait_for(_go(), timeout=30))


def test_no_credential_but_the_cli_installed_still_gets_its_card(tmp_path, monkeypatch):
    """A leg the user can sign into by clicking is PRESENT, not absent.

    Gating the card on the credential made the whole Gemini row disappear on a machine
    that has `gemini` installed and nobody signed in yet — so the one button that could
    have fixed it was not on screen. `present()` is the scan; `credential_health()` is
    the verdict that keeps it out of the picker.
    """
    monkeypatch.setenv("QUOTA_HUB_GEMINI_STORE", _store(tmp_path, present=False))
    monkeypatch.setattr("quota_hub.providers.apps.gemini.api.shutil", types.SimpleNamespace(which=lambda name: r"C:\fake\gemini.exe"))

    async def _go():
        async with _http({}) as http:
            prov = _leg(http)
            assert prov.present() is True
            assert prov.credential_health() == (False, "no_desktop_credential")
            assert prov.login_plan()["kind"] == "oauth"
            assert await prov.discover_accounts() == []
            assert prov.served_models == ()

    asyncio.run(asyncio.wait_for(_go(), timeout=30))


def test_no_credential_and_no_cli_is_no_card_at_all(tmp_path, monkeypatch):
    monkeypatch.setenv("QUOTA_HUB_GEMINI_STORE", _store(tmp_path, present=False))
    monkeypatch.setattr("quota_hub.providers.apps.gemini.api.shutil", types.SimpleNamespace(which=lambda name: None))

    async def _go():
        async with _http({}) as http:
            prov = _leg(http)
            assert prov.present() is False

    asyncio.run(asyncio.wait_for(_go(), timeout=30))


def test_an_expired_token_with_a_way_to_refresh_it_is_still_a_live_leg(tmp_path, monkeypatch):
    """An access token lives ~1h; the picker must not empty itself an hour after login.

    He asked for a box that is right after a restart. Restarting an hour later used to
    drop every Gemini model because the short-lived token had gone stale while the
    refresh token — the CLI's own, the one `ensure_fresh()` uses — was still good.
    """
    monkeypatch.setenv("QUOTA_HUB_GEMINI_STORE", _store(tmp_path, expires_in=-60))
    monkeypatch.setattr("quota_hub.providers.apps.gemini.oauth.discover_client", lambda: ("cid", "secret"))

    async def _go():
        async with _http({}) as http:
            prov = _leg(http)
            assert prov.credential_health() == (True, "ok")

    asyncio.run(asyncio.wait_for(_go(), timeout=30))


def test_an_expired_token_we_cannot_remint_reports_expired(tmp_path, monkeypatch):
    monkeypatch.setenv("QUOTA_HUB_GEMINI_STORE", _store(tmp_path, expires_in=-60))
    monkeypatch.setattr("quota_hub.providers.apps.gemini.oauth.discover_client", lambda: None)

    async def _go():
        async with _http({}) as http:
            prov = _leg(http)
            assert prov.credential_health() == (False, "credential_expired")
            assert await prov.discover_accounts() == []

    asyncio.run(asyncio.wait_for(_go(), timeout=30))


def test_ensure_fresh_mints_a_new_access_token_instead_of_failing_the_turn(tmp_path, monkeypatch):
    from quota_hub.providers.base import UpstreamError

    store = _store(tmp_path, expires_in=-60)
    monkeypatch.setenv("QUOTA_HUB_GEMINI_STORE", store)
    monkeypatch.setattr("quota_hub.providers.apps.gemini.oauth.discover_client", lambda: ("cid", "secret"))
    refreshed: list[bool] = []

    def _fake_refresh():
        refreshed.append(True)
        data = {"access_token": "new-access", "refresh_token": "r",
                "expiry_date": int((time.time() + 3600) * 1000)}
        (tmp_path / "oauth_creds.json").write_text(json.dumps(data), encoding="utf-8")
        return True

    monkeypatch.setattr("quota_hub.providers.apps.gemini.oauth.refresh_credential", _fake_refresh)

    async def _go():
        async with _http({}) as http:
            prov = _leg(http)
            acc = (await prov.discover_accounts())[0]
            acc.cred.expires_at = time.time() - 10
            await prov.ensure_fresh(acc)
            return acc

    acc = asyncio.run(asyncio.wait_for(_go(), timeout=30))
    assert refreshed == [True]
    assert acc.cred.access_token == "new-access"
    assert not acc.cred.is_expired()


def test_the_model_list_is_the_only_name_source(tmp_path, monkeypatch):
    """裁定 204.5: no `/models` ⇒ no models. A default table would be a second truth."""
    monkeypatch.setenv("QUOTA_HUB_GEMINI_STORE", _store(tmp_path))

    async def _go():
        async with _http({"/v1beta/models": httpx.Response(403, json={"error": "no key"})}) as http:
            prov = _leg(http, {"models": ["gemini_api/declared"]})
            await prov.discover_accounts()
            assert prov.served_models == ()

    asyncio.run(asyncio.wait_for(_go(), timeout=30))


def test_a_garbage_file_is_treated_as_absent(tmp_path, monkeypatch):
    bad = tmp_path / "oauth_creds.json"
    bad.write_text("not json", encoding="utf-8")
    monkeypatch.setenv("QUOTA_HUB_GEMINI_STORE", str(bad))

    assert read_credential() is None


@pytest.mark.parametrize("field", ["access", "access_token"])
def test_both_documented_key_names_are_accepted(tmp_path, monkeypatch, field):
    path = tmp_path / "oauth_creds.json"
    path.write_text(json.dumps({field: "tok"}), encoding="utf-8")
    monkeypatch.setenv("QUOTA_HUB_GEMINI_STORE", str(path))

    assert (read_credential() or {}).get("access_token") == "tok"


def test_the_leg_takes_its_own_proxy_and_nothing_else_does(monkeypatch, tmp_path):
    """裁定 206 的落地：Google 系那条腿单独挂代理，不用 TUN、也不动全局 trust_env。"""
    import quota_hub.providers.apps.gemini.api as mod

    captured: dict = {}
    real = mod.httpx.AsyncClient

    class _Recorder:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

        async def aclose(self):
            return None

    monkeypatch.setattr(mod.httpx, "AsyncClient", _Recorder)
    try:
        mod.GeminiApiProvider({"proxy": "http://127.0.0.1:7890"}, None)
        assert captured.get("proxy") == "http://127.0.0.1:7890"
        captured.clear()
        mod.GeminiApiProvider({}, None)  # no proxy configured -> no dedicated client
        assert "proxy" not in captured
    finally:
        monkeypatch.setattr(mod.httpx, "AsyncClient", real)


def _egress_home(tmp_path, port: int):
    home = tmp_path / "home"
    (home / "egress").mkdir(parents=True)
    (home / "egress" / "config.yaml").write_text(f"mixed-port: {port}" + chr(10) + "mode: rule", encoding="utf-8")
    return home


def test_the_settings_node_is_the_default_proxy(tmp_path, monkeypatch):
    """设置→VPN 那个节点池就是全 Plobi 的出口；腿不该再让人填第二遍。"""
    import socket

    from quota_hub.providers.apps.gemini.api import egress_proxy

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    monkeypatch.setenv("PLOBI_HOME", str(_egress_home(tmp_path, port)))
    try:
        assert egress_proxy() == (f"http://127.0.0.1:{port}", "ok")
    finally:
        srv.close()


def test_a_dead_node_is_named_as_such(tmp_path, monkeypatch):
    import socket

    from quota_hub.providers.apps.gemini.api import egress_proxy

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    dead = probe.getsockname()[1]
    probe.close()
    monkeypatch.setenv("PLOBI_HOME", str(_egress_home(tmp_path, dead)))

    assert egress_proxy() == ("", "proxy_unreachable")


def test_a_signed_in_user_without_a_node_is_told_about_the_node(tmp_path, monkeypatch):
    from quota_hub.providers.apps.gemini.api import GeminiApiProvider

    monkeypatch.setenv("QUOTA_HUB_GEMINI_STORE", _store(tmp_path))
    monkeypatch.setenv("PLOBI_HOME", str(tmp_path / "home-without-egress"))
    prov = GeminiApiProvider({"base_url": BASE}, None)
    assert prov.proxy == ""
    assert prov.proxy_reason == "no_proxy"
    # The credential exists, so the remaining blocker is the missing node — say that,
    # not "you are not signed in".
    assert prov.credential_health() == (False, "no_proxy")


def test_antigravity_reads_the_same_node(tmp_path, monkeypatch):
    """一条节点，两条 Google 腿共用（用户 10-28：整个 Plobi 和网络相关都可以利用）。"""
    import socket

    from quota_hub.providers.apps.antigravity.api import AntigravityProvider

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    monkeypatch.setenv("PLOBI_HOME", str(_egress_home(tmp_path, srv.getsockname()[1])))
    try:
        prov = AntigravityProvider({}, None)
        assert prov.proxy_reason == "ok"
        assert prov.proxy.startswith("http://127.0.0.1:")
        assert type(prov.http).__name__ == "AsyncClient"
    finally:
        srv.close()

    monkeypatch.setenv("PLOBI_HOME", str(tmp_path / "no-egress"))
    bare = AntigravityProvider({}, None)
    assert bare.proxy == "" and bare.proxy_reason == "no_proxy"
