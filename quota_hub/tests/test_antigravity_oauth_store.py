"""OAuth bootstrap storage: tokens stay in owner-only files, never in git/config."""

from __future__ import annotations

import asyncio

import pytest

from quota_hub.providers.apps.antigravity import account_store
from quota_hub.providers.apps.antigravity import oauth as oa
from quota_hub.providers.apps.antigravity import AntigravityProvider


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
