"""Qoder's device-code login: the contracts the measured flow must keep.

Local only - MockTransport, a temp PLOBI_HOME, no browser, no vendor call. The values
being asserted are the ones measured on this machine 2026-10-31 (the client id, the two
hosts, the parameter set), but each test asserts a *relationship*, not a snapshot.
"""
from __future__ import annotations

import json
import time

import httpx
import pytest

from quota_hub.providers.apps.qoder import oauth


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("PLOBI_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def test_the_verifier_is_url_safe_and_the_challenge_is_its_s256():
    for _ in range(20):
        verifier, challenge = oauth.pkce_pair()
        assert 43 <= len(verifier) <= 128, "RFC 7636 bounds the verifier, and the CLI draws inside them"
        assert set(verifier) <= set(oauth._ALPHABET)
        assert "=" not in challenge and "+" not in challenge and "/" not in challenge
    # the alphabet must have exactly the 64 values the modulus indexes into - a copied
    # constant that lost or gained a character silently changes the verifier distribution
    assert len(oauth._ALPHABET) == 64


def test_the_consent_url_carries_every_parameter_the_measured_page_demands():
    url = oauth.login_url("CHAL", "NONCE")
    assert url.startswith(f"{oauth.BASE}/device/selectAccounts?")
    for needle in ("challenge=CHAL", "challenge_method=S256", "nonce=NONCE",
                   "machine_id=", "client_id="):
        assert needle in url, needle


def test_the_machine_id_reuses_the_cli_installation_id(tmp_path, monkeypatch):
    cli_home = tmp_path / ".qoder-cn"
    cli_home.mkdir()
    (cli_home / "installation_id").write_text("  abc-123  \n", encoding="utf-8")
    monkeypatch.setattr(oauth.Path, "home", lambda: tmp_path)
    assert oauth.machine_id() == "abc-123", "a login must not look like a new device"


def test_the_stored_credential_is_ours_and_never_the_cli_file(_isolated_home):
    path = oauth.store_path()
    assert path.parent.name == "plobi"
    assert ".qoder" not in str(path), "the CLI's own directory is not ours to write"
    written = oauth.write_credential({"token": "T", "user_id": "U1", "expires_at": int(time.time()) + 3600})
    assert written == path
    assert json.loads(path.read_text(encoding="utf-8"))["user_id"] == "U1"


def test_signed_in_answers_the_credential_question_in_three_states(_isolated_home):
    assert oauth.signed_in() == (False, "no_desktop_credential")

    # a lease is not a logout: it is still HIS account, it just cannot serve now
    oauth.write_credential({"token": "T", "expires_at": int(time.time()) - 10})
    assert oauth.signed_in() == (False, "credential_expired")

    oauth.write_credential({"token": "T", "expires_at": int(time.time()) + 1800})
    assert oauth.signed_in() == (True, "ok")


def test_the_expiry_is_read_whatever_shape_the_answer_carries(_isolated_home):
    # measured 2026-10-31: the device answer stores `expires_at` as an ISO string
    future = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600))
    past = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 60))
    for value, expected in ((future, True), (past, False),
                            (int(time.time()) + 3600, True), (int(time.time()) - 5, False),
                            (int((time.time() + 3600) * 1000), True), (int((time.time() - 5) * 1000), False),
                            ("not a date at all", True)):
        oauth.write_credential({"token": "T", "expires_at": value})
        live, reason = oauth.signed_in()
        assert live is expected, (value, reason)
        if not live:
            assert reason == "credential_expired"


def test_run_login_polls_until_the_consent_lands_and_stores_only_what_is_returned():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url.path))
        if len(calls) < 3:
            return httpx.Response(404, json={"error": "authorization_pending"})
        return httpx.Response(200, json={"token": "SECRET-VALUE", "refresh_token": "R",
                                         "user_id": "U1", "expires_at": int(time.time()) + 86400})

    original = httpx.Client
    httpx.Client = lambda *a, **k: original(transport=httpx.MockTransport(handler), **{  # noqa: E731
        key: value for key, value in k.items() if key != "timeout"})

    seen_urls: list[str] = []
    try:
        result = oauth.run_login(on_url=seen_urls.append, timeout=30)
    finally:
        httpx.Client = original  # type: ignore[misc]

    assert result == {"user_id": "U1", "expires_at": pytest.approx(int(time.time()) + 86400, abs=5)}
    assert len(calls) == 3 and calls[0] == "/api/v1/deviceToken/poll"
    assert seen_urls and "device/selectAccounts" in seen_urls[0]
    # the runner hands back an answer, not the secret
    assert "SECRET-VALUE" not in json.dumps(result)
    assert json.loads(oauth.store_path().read_text(encoding="utf-8"))["token"] == "SECRET-VALUE"


def test_run_login_says_what_failed_instead_of_hanging_forever(monkeypatch):
    monkeypatch.setattr(oauth.time, "sleep", lambda _s: None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="gateway shrugged")

    original = httpx.Client
    httpx.Client = lambda *a, **k: original(transport=httpx.MockTransport(handler), **{  # noqa: E731
        key: value for key, value in k.items() if key != "timeout"})
    try:
        with pytest.raises(RuntimeError) as caught:
            oauth.run_login(on_url=lambda _u: None, timeout=0.2)
    finally:
        httpx.Client = original  # type: ignore[misc]
    assert "HTTP 500" in str(caught.value), "the last thing we saw belongs in the message"
