"""Gemini 登录这一刀的判据：一次点击必须真的走到浏览器，而且落回 CLI 自己的凭据文件。

为什么单独一个文件：`--reauth` 那条 argv 是猜的，实测 0.65.0 里根本没有这个开关——它
打印 usage 后退出 41，hub 从 usage 里抓到的第一个 URL 是 `--yolo` 那行help里的 YouTube
链接，于是"点登录跳出的是视频"。这一档把三件事钉住：① 登录客户端只从**装在那台机器上的
CLI 自己**身上取（不进 git、不写死）；② 落点是 CLI 自己那份 `oauth_creds.json`，多余字段
不丢；③ 出口节点不在的时候要说人话，不许把 `no_proxy` 这类机器词推到屏上。

本文件全程合成，不碰真 `~/.gemini`，不开浏览器，不打网络。

Run:
    cd Code && scripts/run_tests.sh -j 1 quota_hub/tests/test_gemini_oauth.py
"""

from __future__ import annotations

import json
import time
import types

import httpx
from quota_hub import google_oauth
from quota_hub.providers.apps.gemini import oauth as gem_oauth

BUNDLE_TAIL = "node_modules/@google/gemini-cli/bundle"


def _fake_cli(tmp_path, *, client_id="cid-from-bundle", client_secret="secret-from-bundle"):
    """A stand-in for `gemini` installed through npm: a binary plus its own bundle."""
    root = tmp_path / "node"
    bundle = root / BUNDLE_TAIL
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "chunk-FAKE.js").write_text(
        f'var OAUTH_CLIENT_ID = "{client_id}";\n'
        f'var OAUTH_CLIENT_SECRET = "{client_secret}";\n'
        'var OAUTH_SCOPE = [\n'
        '  "https://www.googleapis.com/auth/cloud-platform",\n'
        '  "https://www.googleapis.com/auth/userinfo.email",\n'
        '  "https://www.googleapis.com/auth/userinfo.profile"\n'
        '];\n'
        'var OAUTH_FILE = "oauth_creds.json";\n',
        encoding="utf-8",
    )
    return root


def _which_returns(tmp_path):
    root = _fake_cli(tmp_path)
    binary = root / "gemini.CMD"
    binary.write_text("@echo off\r\n", encoding="utf-8")
    return types.SimpleNamespace(which=lambda name: str(binary) if name == "gemini" else None)


def test_the_client_pair_comes_from_the_cli_that_is_actually_installed(tmp_path, monkeypatch):
    monkeypatch.delenv(gem_oauth.CLIENT_ID_ENV, raising=False)
    monkeypatch.delenv(gem_oauth.CLIENT_SECRET_ENV, raising=False)
    monkeypatch.setattr(gem_oauth, "shutil", _which_returns(tmp_path))
    gem_oauth.reset_discovery_cache()

    assert gem_oauth.discover_client() == ("cid-from-bundle", "secret-from-bundle")


def test_a_cli_that_ships_no_oauth_client_is_reported_as_such(tmp_path, monkeypatch):
    root = tmp_path / "node"
    bundle = root / BUNDLE_TAIL
    bundle.mkdir(parents=True)
    (bundle / "chunk-FAKE.js").write_text("var OAUTH_CLIENT_ID = \"only-one-half\";", encoding="utf-8")
    monkeypatch.delenv(gem_oauth.CLIENT_ID_ENV, raising=False)
    monkeypatch.delenv(gem_oauth.CLIENT_SECRET_ENV, raising=False)
    monkeypatch.setattr(
        gem_oauth, "shutil", types.SimpleNamespace(which=lambda name: str(root / "gemini.CMD"))
    )
    gem_oauth.reset_discovery_cache()

    # Half a pair is no pair: inventing the other half is how a click opens a wrong page.
    assert gem_oauth.discover_client() is None


def test_an_explicit_pair_in_the_environment_wins(tmp_path, monkeypatch):
    monkeypatch.setenv(gem_oauth.CLIENT_ID_ENV, "env-id")
    monkeypatch.setenv(gem_oauth.CLIENT_SECRET_ENV, "env-secret")
    gem_oauth.reset_discovery_cache()

    assert gem_oauth.discover_client() == ("env-id", "env-secret")
    gem_oauth.reset_discovery_cache()


def test_writing_the_credential_keeps_what_the_cli_stored_alongside_it(tmp_path, monkeypatch):
    path = tmp_path / "oauth_creds.json"
    path.write_text(json.dumps({"project_id": "p-42", "credits": {"t": 1}}), encoding="utf-8")
    monkeypatch.setattr(gem_oauth._api, "store_path", lambda: path)

    gem_oauth.write_credential({
        "access_token": "ya29.new",
        "refresh_token": "1//old",
        "expires_in": 3599,
        "scope": "https://www.googleapis.com/auth/cloud-platform",
        "id_token": "eyJ.abc",
    })

    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["project_id"] == "p-42"          # the CLI's own extras survive
    assert stored["credits"] == {"t": 1}
    assert stored["access_token"] == "ya29.new"
    assert stored["refresh_token"] == "1//old"
    # The CLI's unit is milliseconds; a seconds-based expiry would read as expired in 1970.
    assert stored["expiry_date"] > int(time.time() * 1000)


def test_a_click_without_an_egress_node_refuses_in_words_the_screen_can_show(tmp_path, monkeypatch):
    monkeypatch.setattr(gem_oauth, "shutil", _which_returns(tmp_path))
    gem_oauth.reset_discovery_cache()
    monkeypatch.setattr(gem_oauth, "proxy_for_leg", lambda: ("", "no_proxy"))
    opened: list[str] = []

    try:
        gem_oauth.run_login(on_url=opened.append)
    except RuntimeError as exc:
        assert "VPN" in str(exc)
        assert "no_proxy" not in str(exc)
    else:
        raise AssertionError("没有节点也不许假装登录已经开始")
    assert opened == []


def test_a_click_hands_the_consent_url_over_and_stores_the_result(tmp_path, monkeypatch):
    path = tmp_path / "oauth_creds.json"
    monkeypatch.setattr(gem_oauth._api, "store_path", lambda: path)
    monkeypatch.setattr(gem_oauth, "shutil", _which_returns(tmp_path))
    gem_oauth.reset_discovery_cache()
    monkeypatch.setattr(gem_oauth, "proxy_for_leg", lambda: ("http://127.0.0.1:1", "ok"))
    seen: dict = {}

    def _fake_sign_in(**kwargs):
        seen.update(kwargs)
        kwargs["on_url"]("https://accounts.google.com/o/oauth2/v2/auth?client_id=cid-from-bundle")
        return {"access_token": "ya29.a", "refresh_token": "1//r", "expires_in": 3600}

    monkeypatch.setattr(gem_oauth.google_oauth, "sign_in", _fake_sign_in)
    urls: list[str] = []

    result = gem_oauth.run_login(on_url=urls.append)

    assert urls == ["https://accounts.google.com/o/oauth2/v2/auth?client_id=cid-from-bundle"]
    assert seen["client_id"] == "cid-from-bundle"
    assert seen["client_secret"] == "secret-from-bundle"
    assert result == {"refresh_token": True}
    assert json.loads(path.read_text(encoding="utf-8"))["access_token"] == "ya29.a"


def test_refresh_needs_all_three_halves_and_rewrites_the_store(tmp_path, monkeypatch):
    path = tmp_path / "oauth_creds.json"
    monkeypatch.setattr(gem_oauth._api, "store_path", lambda: path)

    # 1. nothing stored at all
    assert gem_oauth.refresh_credential() is False

    # 2. a credential with no refresh token behind it
    path.write_text(json.dumps({"access_token": "a", "expiry_date": int(time.time() * 1000)}), encoding="utf-8")
    assert gem_oauth.refresh_credential() is False

    # 3. a refresh token but no client pair to spend it with
    path.write_text(json.dumps({"access_token": "a", "refresh_token": "r"}), encoding="utf-8")
    monkeypatch.setattr(gem_oauth, "discover_client", lambda: None)
    assert gem_oauth.refresh_credential() is False

    monkeypatch.setattr(gem_oauth, "discover_client", lambda: ("cid", "sec"))
    monkeypatch.setattr(gem_oauth, "proxy_for_leg", lambda: ("", "ok"))
    calls: dict = {}

    def _fake_refresh(client_id, client_secret, refresh_token, *, proxy="", timeout=30.0):
        calls.update({"client_id": client_id, "refresh_token": refresh_token, "proxy": proxy})
        return {"access_token": "ya29.fresh", "expires_in": 3600}

    monkeypatch.setattr(gem_oauth.google_oauth, "refresh", _fake_refresh)
    assert gem_oauth.refresh_credential() is True
    assert calls == {"client_id": "cid", "refresh_token": "r", "proxy": ""}
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["access_token"] == "ya29.fresh"
    # Google leaves the refresh token out of a refresh response; the old one must survive.
    assert stored["refresh_token"] == "r"


def test_a_refused_refresh_is_a_health_verdict_and_not_a_crash(tmp_path, monkeypatch):
    path = tmp_path / "oauth_creds.json"
    path.write_text(json.dumps({"access_token": "a", "refresh_token": "r"}), encoding="utf-8")
    monkeypatch.setattr(gem_oauth._api, "store_path", lambda: path)
    monkeypatch.setattr(gem_oauth, "discover_client", lambda: ("cid", "sec"))
    monkeypatch.setattr(gem_oauth, "proxy_for_leg", lambda: ("", "ok"))

    def _boom(*a, **k):
        raise httpx.ConnectError("no route to oauth2.googleapis.com")

    monkeypatch.setattr(gem_oauth.google_oauth, "refresh", _boom)
    assert gem_oauth.refresh_credential() is False


def test_the_click_asks_google_the_way_the_cli_itself_does(tmp_path, monkeypatch):
    """Same scopes, no PKCE — the request a terminal would have sent.

    Two ways this can go wrong that a user feels as "Google 拒绝了": asking for scopes the
    installed client is not registered for (Antigravity's list is not Gemini's), and
    sending a code challenge the CLI's own flow never sends.
    """
    path = tmp_path / "oauth_creds.json"
    monkeypatch.setattr(gem_oauth._api, "store_path", lambda: path)
    monkeypatch.setattr(gem_oauth, "shutil", _which_returns(tmp_path))
    gem_oauth.reset_discovery_cache()
    monkeypatch.setattr(gem_oauth, "proxy_for_leg", lambda: ("http://127.0.0.1:1", "ok"))
    seen: dict = {}

    def _fake_sign_in(**kwargs):
        seen.update(kwargs)
        kwargs["on_url"]("https://accounts.google.com/o/oauth2/v2/auth?client_id=cid")
        return {"access_token": "ya29.a", "refresh_token": "1//r", "expires_in": 3600}

    monkeypatch.setattr(gem_oauth.google_oauth, "sign_in", _fake_sign_in)
    gem_oauth.run_login(on_url=lambda _url: None)

    assert seen["scopes"] == [
        "https://www.googleapis.com/auth/cloud-platform",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/userinfo.profile",
    ]
    assert seen["use_pkce"] is False
    assert seen["proxy"] == "http://127.0.0.1:1"


def test_the_consent_url_we_build_carries_no_code_challenge_when_pkce_is_off():
    url, verifier = google_oauth.auth_url(
        "cid", "http://127.0.0.1:999/callback", ["https://www.googleapis.com/auth/cloud-platform"],
        use_pkce=False,
    )

    assert "code_challenge" not in url
    assert "access_type=offline" in url and "prompt=consent" in url
    assert "127.0.0.1%3A999%2Fcallback" in url
    # An empty verifier is what tells `exchange` not to send one back.
    assert verifier


def test_the_shared_flow_opens_the_browser_only_after_reporting_the_url(monkeypatch):
    """`on_url` first: a detached hub child may have no browser to open, the shell does."""
    order: list[str] = []
    monkeypatch.setattr(gem_oauth.google_oauth.webbrowser, "open", lambda url: order.append("browser"))
    monkeypatch.setattr(
        gem_oauth.google_oauth, "wait_for_code",
        lambda port, timeout=300.0, **kw: (order.append("wait") or "code-123"),
    )

    def _record(url):
        order.append("url")

    def _fake_client(*args, **kwargs):
        class _C:
            def post(self, url, data=None):
                order.append("token")
                return httpx.Response(200, json={"access_token": "a", "refresh_token": "r"})

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return _C()

    monkeypatch.setattr(gem_oauth.google_oauth.httpx, "Client", _fake_client)

    tokens = google_oauth.sign_in(
        client_id="cid", client_secret="sec", scopes=google_oauth.CODE_ASSIST_SCOPES, on_url=_record
    )

    assert tokens["access_token"] == "a"
    assert order[0] == "url"          # the desktop gets the link before anything is opened
    assert "token" in order


def test_a_non_https_or_loopback_link_is_never_the_one_we_hand_over():
    from quota_hub.authflow import sign_in_url

    assert sign_in_url("see http://localhost:8085/authorize?x=1") == ""
    assert sign_in_url("see https://accounts.google.com/o/oauth2/v2/auth?x=1").startswith("https://accounts")


def test_the_consent_url_is_opened_by_the_hub_even_when_it_is_handed_over(monkeypatch):
    """Measured 2026-10-29 (user report: 「点击没有弹出浏览器」): the hub handed the URL
    to the desktop and left the opening to it — and that hand-off did not reliably
    happen, so a click opened nothing at all. The hub opens it itself now; a possible
    second tab from the desktop is the smaller problem."""
    from quota_hub import google_oauth

    opened: list[str] = []
    seen: list[str] = []
    monkeypatch.setattr(google_oauth.webbrowser, "open", lambda url: opened.append(url) or True)
    monkeypatch.setattr(google_oauth, "wait_for_code", lambda port, timeout=0.0, path="/callback": "code")
    monkeypatch.setattr(google_oauth, "exchange", lambda *a, **k: {"access_token": "a"})

    google_oauth.sign_in(client_id="cid", client_secret="s", scopes=["scope"], on_url=seen.append)

    assert seen and seen[0].startswith("https://accounts.google.com/")
    assert opened and opened[0].startswith("https://accounts.google.com/")

    google_oauth.sign_in(client_id="cid", client_secret="s", scopes=["scope"])

    assert len(opened) == 2
