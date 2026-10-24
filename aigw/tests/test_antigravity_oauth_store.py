"""OAuth bootstrap storage: tokens stay in owner-only files, never in git/config."""

from __future__ import annotations

import asyncio

import pytest

from aigw.auth import account_store
from aigw.auth import antigravity_oauth as oa
from aigw.providers.antigravity import AntigravityProvider


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("PLOBI_HOME", str(tmp_path))
    monkeypatch.delenv("GOOGLE_CC_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CC_CLIENT_SECRET", raising=False)
    monkeypatch.delenv(account_store.ACCOUNTS_ENV, raising=False)
    monkeypatch.delenv(account_store.CLIENT_ENV, raising=False)
    return tmp_path


def test_missing_client_pair_is_named_not_faked(home):
    assert account_store.client_credentials() == ("", "")
    with pytest.raises(RuntimeError) as exc:
        oa.build_auth_url("http://127.0.0.1:1234/callback")
    assert "GOOGLE_CC_CLIENT_ID" in str(exc.value)


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

    monkeypatch.setattr("aigw.tokens.desktop_stores.read_antigravity_access_token", lambda: None)
    monkeypatch.setattr("aigw.tokens.desktop_stores.read_antigravity", lambda: None)
    provider = AntigravityProvider(
        {"host": "https://daily-cloudcode-pa.googleapis.com", "accounts": []}, object()
    )

    accounts = asyncio.run(provider.discover_accounts())
    stored = next(a for a in accounts if a.id == "antigravity-2")
    assert stored.cred.refresh_token == "refresh-2"
    assert stored.cred.extra["project_id"] == "proj-3"
