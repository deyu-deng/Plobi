"""OAuth bootstrap storage: tokens stay in owner-only files, never in git/config."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from quota_hub import google_oauth
from quota_hub.providers.apps.antigravity import account_store
from quota_hub.providers.apps.antigravity import oauth as oa
from quota_hub.providers.apps.antigravity import AntigravityProvider


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
    monkeypatch.setattr(oa, "probe_network", lambda: {"ok": True, "checks": []})
    monkeypatch.setattr(oa, "resolve_proxy", lambda: ("http://127.0.0.1:1", "ok"))

    oa.run_bootstrap(print_token=False)

    assert seen["proxy"] == "http://127.0.0.1:1"
    assert seen["client_id"] == "cid"


def test_a_missing_node_refuses_before_his_browser_is_opened(monkeypatch, home):
    monkeypatch.setenv("GOOGLE_CC_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CC_CLIENT_SECRET", "secret")
    monkeypatch.setattr(oa, "probe_network", lambda: {"ok": True, "checks": []})
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
