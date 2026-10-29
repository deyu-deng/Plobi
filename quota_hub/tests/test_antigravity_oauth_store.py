"""OAuth bootstrap storage: tokens stay in owner-only files, never in git/config."""

from __future__ import annotations

import asyncio
import socket

import httpx
import pytest
from quota_hub import google_oauth
from quota_hub.providers.apps.antigravity import account_store
from quota_hub.providers.apps.antigravity import oauth as oa
from quota_hub.providers.apps.antigravity import AntigravityProvider


@pytest.fixture(autouse=True)
def _no_google_calls(monkeypatch):
    """These tests are about our storage and proxy wiring, not about Google answering.

    Measured 2026-10-29: `run_bootstrap` was faked only as far as `sign_in`, so
    `discover_project` and `fetch_userinfo` still posted to Google with a fake token —
    with the Settings → VPN node half-up they blocked to their 20 s timeouts each and
    this 9-test file turned into minutes. A test that needs Google to answer is not
    testing us, so both calls are stubbed here for the whole file.
    """
    monkeypatch.setattr(oa, "discover_project", lambda _token: "")
    monkeypatch.setattr(oa, "fetch_userinfo", lambda _token: {})


def test_the_google_login_leaves_through_the_same_node_as_the_leg(monkeypatch, home):
    """回归本体的判据：抽公共流程那天，这条腿的换令牌改成了直连。

    用户因此在浏览器里走完两次登录、两次都被回 401——`401` 不是 Google 给的，是这台机器上
    拦直连的人给的。所以 `sign_in` 必须拿到节点，且节点不在的时候不许先把他的浏览器开出去。
    """
    monkeypatch.setenv("GOOGLE_CC_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CC_CLIENT_SECRET", "secret")
    seen: dict = {}

    def _fake_sign_in(**kwargs):
        seen.update(kwargs)
        return {"refresh_token": "r", "access_token": "a"}

    monkeypatch.setattr(oa.google_oauth, "sign_in", _fake_sign_in)
    monkeypatch.setattr(oa, "probe_network", lambda *_a, **_k: {"ok": True, "checks": []})
    monkeypatch.setattr(oa, "resolve_proxy", lambda: ("http://127.0.0.1:1", "ok"))

    oa.run_bootstrap(print_token=False)

    assert seen["proxy"] == "http://127.0.0.1:1"
    assert seen["client_id"] == "cid"


def test_the_consent_asks_google_the_way_the_working_clients_do(monkeypatch, home):
    """线级细节是从别人**跑通的实现**里读来的，不是我们发明的（2026-10-28）。

    抄的是 `router-for-me/CLIProxyAPI` 的 `internal/auth/antigravity/{constants.go,auth.go}`：
    固定回调口 51121、路径 `/oauth-callback`、**完全不用 PKCE**。我们原先那套随机口 +
    S256 challenge 是自己想的，等于多塞一种会失败的可能。
    """
    monkeypatch.setenv("GOOGLE_CC_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CC_CLIENT_SECRET", "secret")
    seen: dict = {}

    monkeypatch.setattr(oa.google_oauth, "sign_in", lambda **k: seen.update(k) or {"access_token": "a", "refresh_token": "r"})
    monkeypatch.setattr(oa, "probe_network", lambda *_a, **_k: {"ok": True, "checks": []})
    monkeypatch.setattr(oa, "resolve_proxy", lambda: ("http://127.0.0.1:1", "ok"))

    oa.run_bootstrap(print_token=False)

    assert seen["use_pkce"] is False
    assert seen["redirect_host"] == "localhost"
    assert seen["redirect_path"] == "/oauth-callback"
    assert seen["preferred_port"] == 51121


def test_a_busy_callback_port_falls_back_instead_of_failing_the_click():
    """那个固定口可能被厂商自己的应用占着——占了也不能让这一跳死。"""
    held = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    held.bind(("127.0.0.1", 0))
    held.listen(1)
    try:
        taken = held.getsockname()[1]
        assert google_oauth._reserve_port(taken) != taken
        assert google_oauth._reserve_port(0) > 0
    finally:
        held.close()


def test_a_missing_node_refuses_before_his_browser_is_opened(monkeypatch, home):
    monkeypatch.setenv("GOOGLE_CC_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CC_CLIENT_SECRET", "secret")
    monkeypatch.setattr(oa, "probe_network", lambda *_a, **_k: {"ok": True, "checks": []})
    monkeypatch.setattr(oa, "resolve_proxy", lambda: ("", "no_proxy"))
    opened: list[str] = []

    monkeypatch.setattr(oa.google_oauth, "sign_in", lambda **k: opened.append(k.get("on_url") or "called"))

    with pytest.raises(RuntimeError) as exc:
        oa.run_bootstrap(print_token=False, on_url=lambda url: opened.append(url))

    assert "VPN" in str(exc.value)
    assert "no_proxy" not in str(exc.value)
    assert opened == []


def test_a_google_error_is_named_and_a_non_google_answer_says_so():
    """`HTTP 401` 单独出现时是两种相反的故障：客户端对不上，或者根本没出门。"""
    res = httpx.Response(401, headers={"content-type": "text/html"}, text="<html>blocked</html>")
    assert "不是 Google 给的" in google_oauth._token_error(res)

    res = httpx.Response(401, json={"error": "invalid_client"})
    assert "invalid_client" in google_oauth._token_error(res)


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("PLOBI_HOME", str(tmp_path))
    monkeypatch.delenv("GOOGLE_CC_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CC_CLIENT_SECRET", raising=False)
    monkeypatch.delenv(account_store.ACCOUNTS_ENV, raising=False)
    monkeypatch.delenv(account_store.CLIENT_ENV, raising=False)
    monkeypatch.setenv(account_store.APP_SCAN_ENV, "0")
    return tmp_path


def test_missing_client_pair_is_named_not_faked(home):
    assert account_store.client_credentials() == ("", "")
    with pytest.raises(RuntimeError) as exc:
        oa.build_auth_url("http://127.0.0.1:1234/callback")
    # Refusing is the contract; the wording is the other half. This sentence lands in the
    # login session's `detail`, which the settings card shows — so it must not read like
    # an instruction to open a terminal (user ruling 2026-10-26: 用户不接触命令行), and it
    # must not name an environment variable as if that were the user's business.
    message = str(exc.value)
    assert "GOOGLE_CC_CLIENT_ID" not in message
    assert "quota_hub auth" not in message
    assert "客户端" in message


def test_public_client_pair_is_extracted_from_an_installed_app(home, monkeypatch, tmp_path):
    fake = tmp_path / "antigravity-tools.exe"
    fake.write_bytes(
        b"...REDACTED_GOOGLE_PUBLIC_CLIENT_ID..."
        b"...REDACTED_GOOGLE_PUBLIC_CLIENT_SECRET..."
    )
    monkeypatch.setattr(account_store, "_candidate_binaries", lambda: [fake])
    monkeypatch.delenv(account_store.APP_SCAN_ENV)

    pair = account_store.client_credentials()

    assert pair == (
        "REDACTED_GOOGLE_PUBLIC_CLIENT_ID",
        "REDACTED_GOOGLE_PUBLIC_CLIENT_SECRET",
    )
    assert account_store.client_path().is_file()


def test_client_pair_is_persisted_after_first_run(home, monkeypatch):
    monkeypatch.setenv("GOOGLE_CC_CLIENT_ID", "client-abc")
    monkeypatch.setenv("GOOGLE_CC_CLIENT_SECRET", "secret-xyz")

    url, verifier = oa.build_auth_url("http://127.0.0.1:1234/callback")
    assert "client_id=client-abc" in url
    assert "code_challenge=" in url and verifier
    account_store.save_client(*account_store.client_credentials())

    monkeypatch.delenv("GOOGLE_CC_CLIENT_ID")
    monkeypatch.delenv("GOOGLE_CC_CLIENT_SECRET")
    assert account_store.client_credentials() == ("client-abc", "secret-xyz")
    assert account_store.client_path().is_file()


def test_accounts_upsert_by_email_and_are_discovered(home, monkeypatch):
    first = account_store.upsert_account({
        "email": "a@example.com", "id": "antigravity-1",
        "project_id": "proj-1", "refresh_token": "refresh-1",
    })
    account_store.upsert_account({
        "email": "a@example.com", "id": "antigravity-2",
        "project_id": "proj-3", "refresh_token": "refresh-2",
    })

    rows = account_store.load_accounts()
    assert first["id"] == "antigravity-1"
    assert len(rows) == 1
    assert rows[0]["refresh_token"] == "refresh-2"
    assert rows[0]["project_id"] == "proj-3"

    monkeypatch.setattr("quota_hub.tokens.desktop_stores.read_antigravity_access_token", lambda: None)
    monkeypatch.setattr("quota_hub.tokens.desktop_stores.read_antigravity", lambda: None)
    provider = AntigravityProvider(
        {"host": "https://daily-cloudcode-pa.googleapis.com", "accounts": []}, object()
    )

    accounts = asyncio.run(provider.discover_accounts())
    stored = next(a for a in accounts if a.id == "antigravity-2")
    assert stored.cred.refresh_token == "refresh-2"
    assert stored.cred.extra["project_id"] == "proj-3"


def test_a_hub_sign_in_counts_even_if_the_desktop_app_never_logged_in(home, monkeypatch):
    """Measured 2026-10-29: `signed_in()` only read the desktop app's record, so a
    *successful* click through Plobi left the card a `+` while the leg could already
    serve turns from the file `run_bootstrap` wrote."""
    from quota_hub.providers.apps.antigravity import api as aa

    monkeypatch.setattr(aa, "proxy_url", lambda: ("", "no_proxy"))
    # The owner's machine may hold a live Keychain entry (Antigravity Tools pushes one);
    # a test must not depend on what the owner has on the machine.
    monkeypatch.setattr(
        aa.ds,
        "antigravity_desktop_token_status",
        lambda: {"present": False, "expires_in_seconds": None, "expires_at": None},
    )
    prov = AntigravityProvider({}, None)
    prov.config = {}
    assert prov.signed_in()[0] is False

    account_store.upsert_account(
        {"id": "a1", "email": "x@example.com", "refresh_token": "rt-1", "project_id": "p-1"}
    )

    assert prov.signed_in() == (True, "ok")


def test_a_node_that_comes_up_after_boot_is_adopted_without_a_restart(home, monkeypatch):
    """The verdict *and* the client were frozen at construction, so starting the VPN after
    the hub was up sent turns direct even once the card said ok."""
    from quota_hub.providers.apps.antigravity import api as aa

    monkeypatch.setattr(aa, "proxy_url", lambda: ("", "proxy_unreachable"))
    prov = AntigravityProvider({}, None)
    prov.config = {}
    before = prov.http

    monkeypatch.setattr(aa, "proxy_url", lambda: ("http://127.0.0.1:7895", "ok"))
    prov._proxy_seen = None
    asyncio.run(prov._rebind_node())

    assert prov.proxy_reason == "ok"
    assert prov.http is not before
    assert prov.cloudcode.http is prov.http
    asyncio.run(prov.http.aclose())


def test_a_turn_without_its_node_names_the_node_not_a_missing_account(home, monkeypatch):
    """Measured 2026-10-29: with a credential present and the node down, a turn answered
    `503 no upstream account available: no more accounts for ['antigravity']` after 11.5 s
    — English, and it reads like a config bug instead of "the node is off"."""
    from quota_hub import main as hubmain
    from quota_hub.providers.base import UpstreamError
    from quota_hub.providers.apps.antigravity import api as aa

    class _Prov:
        def credential_health(self):
            return False, "no_proxy"

    assert hubmain._no_account_detail(_Prov()) == "还没配 VPN 节点，这条渠道连不上"

    # The leg refuses *before* the wire: a refresh here would be a ten-second timeout.
    monkeypatch.setattr(aa, "proxy_url", lambda: ("", "no_proxy"))
    calls = {"n": 0}

    async def _boom(*a, **k):
        calls["n"] += 1
        raise AssertionError("refresh must not be attempted without the node")

    monkeypatch.setattr(aa.ds, "refresh_antigravity", _boom)

    prov = aa.AntigravityProvider({}, None)
    prov.config = {}
    acc = prov._make_account(id="a", label="", refresh_token="rt", client_id="c", client_secret="s")
    acc.cred.access_token = ""

    with pytest.raises(UpstreamError) as exc:
        asyncio.run(prov.ensure_fresh(acc))

    assert "VPN" in str(exc.value)
    assert exc.value.cooldown == 0.0, "a network condition must not cool the account down"
    assert calls["n"] == 0


def test_a_flaky_node_is_looked_at_twice_before_the_click_is_refused(home, monkeypatch):
    """Measured 2026-10-29 on this machine: nine of ten probes to Google failed through
    the node (TLS handshake timeouts). A refusal opens no browser at all, so one unlucky
    probe must not decide the click."""
    calls = {"n": 0}

    def _probe(*_a, **_k):
        calls["n"] += 1
        return {"ok": calls["n"] > 1, "advice": "节点连不上", "checks": []}

    monkeypatch.setattr(oa, "probe_network", _probe)
    monkeypatch.setattr(oa, "client_credentials", lambda: ("cid", "secret"))
    monkeypatch.setattr(oa, "resolve_proxy", lambda: ("http://127.0.0.1:7895", "ok"))
    monkeypatch.setattr(oa, "discover_project", lambda _token: "")
    monkeypatch.setattr(oa, "fetch_userinfo", lambda _token: {})
    monkeypatch.setattr(oa.google_oauth, "sign_in", lambda **k: {"refresh_token": "r", "access_token": "a"})

    oa.run_bootstrap(print_token=False)

    assert calls["n"] == 2, "the click was refused after a single unlucky probe"
