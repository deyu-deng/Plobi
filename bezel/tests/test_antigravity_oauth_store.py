"""OAuth bootstrap storage: tokens stay in owner-only files, never in git/config."""

from __future__ import annotations

import asyncio
import socket

import httpx
import pytest
from bezel import google_oauth
from bezel.providers.apps.antigravity import account_store
from bezel.providers.apps.antigravity import oauth as oa


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


#: The *shape* of Google's public OAuth client constants, not their values. The extractor
#: only accepts this shape (`_CLIENT_SECRET_RE` is `GOCSPX-` plus exactly 28, 35 total;
#: `_CLIENT_ID_RE` is `<digits>-<lowercase>.apps.googleusercontent.com`), so a fixture that
#: names the constant in prose instead of matching it can never pair anything — which is
#: exactly how these three went red after the values came out of the repo on 10-31. The
#: real strings are public; they still do not belong in a file with a remote.
PUBLIC_CLIENT_ID = "1234567890123-abcdefghijklmnopqrstuvwx.apps.googleusercontent.com"
PUBLIC_CLIENT_SECRET = "GOCSPX-" + "aB3dE6fG9hI2jK5lM8nQ1rStUvWx"

assert len(PUBLIC_CLIENT_SECRET) == 35, len(PUBLIC_CLIENT_SECRET)


def test_the_extractor_pairs_a_secret_with_the_client_id_it_sits_next_to(tmp_path):
    """Two measured facts, one test.

    `antigravity-tools.exe` embeds several OAuth clients, and the old extractor took the
    first id and the first secret from anywhere in the file - a mixed pair, which Google
    answers `invalid_client`. We read that as "the app ships its own runtime client" and
    stopped trying our own sign-in; it was our own pairing. Google's public constants are
    also exactly 35 characters, and here one is packed against another with no separator,
    so an open-ended pattern stored 60.
    """
    other_id = "1234567890102-abcdefghijklmnopqrstuvwxys.apps.googleusercontent.com"
    ours_id = PUBLIC_CLIENT_ID
    ours_secret = PUBLIC_CLIENT_SECRET
    blob = (other_id.encode() + b"\x00" + b"Z" * 600 + b"\x00"      # a far client, unpaired
            + b'{"client_id":"' + ours_id.encode() + b'","client_secret":"' + ours_secret.encode()
            + b"TAIL_AFTER_THE_CONSTANT" + b"\x00")
    path = tmp_path / "bundle.bin"
    path.write_bytes(blob)

    found_id, found_secret = account_store._find_pair_in_file(path)
    assert found_id == ours_id, "the id must be the one next to this secret"
    assert found_secret == ours_secret, "and 35 characters, not the constant plus its neighbour"


def test_a_poisoned_saved_pair_is_ignored_and_the_pair_is_rediscovered(tmp_path, monkeypatch, home):
    monkeypatch.setattr(account_store, "client_path", lambda: tmp_path / "client.json")
    (tmp_path / "client.json").write_text(
        '{"client_id": "cid.apps.googleusercontent.com", "client_secret": '
        'PUBLIC_CLIENT_SECRET + "_TAIL_999"}', encoding="utf-8")

    monkeypatch.setattr(account_store, "discover_client_from_apps",
                        lambda: (PUBLIC_CLIENT_ID,
                                 PUBLIC_CLIENT_SECRET))
    found_id, found_secret = account_store.client_credentials(persist_discovered=False)
    assert found_secret == PUBLIC_CLIENT_SECRET, found_secret

    # an env-supplied pair is the operator's own words and is taken as given
    monkeypatch.setenv(account_store.CLIENT_ID_ENV, "whatever-id")
    monkeypatch.setenv(account_store.CLIENT_SECRET_ENV, "not-a-GOCSPX-shaped-value")
    assert account_store.client_credentials(persist_discovered=False) == (
        "whatever-id", "not-a-GOCSPX-shaped-value")


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
    assert "bezel auth" not in message
    assert "客户端" in message


def test_public_client_pair_is_extracted_from_an_installed_app(home, monkeypatch, tmp_path):
    fake = tmp_path / "antigravity-tools.exe"
    fake.write_bytes(
        b'...' + PUBLIC_CLIENT_ID.encode() + b'...'
        b'...' + PUBLIC_CLIENT_SECRET.encode() + b'...'
    )
    monkeypatch.setattr(account_store, "_candidate_binaries", lambda: [fake])
    monkeypatch.delenv(account_store.APP_SCAN_ENV)

    pair = account_store.client_credentials()

    assert pair == (
        PUBLIC_CLIENT_ID,
        PUBLIC_CLIENT_SECRET,
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
