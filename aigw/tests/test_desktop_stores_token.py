"""Offline contracts for the desktop-store token reader.

Nothing here touches a real credential store: every case monkeypatches
``_win_credential_text`` (or the macOS ``security`` call), because reading the
operator's live Google token from a test would both leak a secret into CI
artifacts and make the suite depend on someone being logged in.

The Windows branch is new as of 2026-10-11 and is what lets aigw pick up the
token the Antigravity desktop CLI keeps refreshing in Credential Manager
(target ``gemini:antigravity``) instead of needing ``ANTIGRAVITY_ACCESS_TOKEN``
pasted into the environment by hand.
"""

from __future__ import annotations

import base64
import json
import time

import pytest

from aigw.tokens import desktop_stores as ds

LIVE = "ya29.test-token-not-a-secret"


def _payload(expiry, token=LIVE):
    return json.dumps({
        "auth_method": "oauth",
        "id_token": "eyJnot.a.real.id.token",
        "token": {"access_token": token, "refresh_token": "1//not-a-real-refresh",
                  "expiry": expiry, "token_type": "Bearer"},
    })


def _when(seconds_from_now):
    return time.strftime("%Y-%m-%dT%H:%M:%S",
                         time.gmtime(time.time() + seconds_from_now)) + "+00:00"


@pytest.fixture
def win_blob(monkeypatch):
    """Feed the Windows reader a blob as if Credential Manager held it."""
    def _set(raw):
        monkeypatch.setattr(ds, "_win_credential_text", lambda target: raw)
    return _set


def test_windows_branch_reads_a_live_token(win_blob):
    win_blob(_payload(_when(3600)))
    assert ds._read_antigravity_access_token_windows() == LIVE


def test_windows_branch_skips_a_token_the_store_says_is_dead(win_blob):
    """Handing this over is a guaranteed 401 round-trip, so discovery must not
    offer the account at all."""
    win_blob(_payload(_when(-30)))
    assert ds._read_antigravity_access_token_windows() is None


def test_windows_branch_keeps_a_token_when_the_expiry_is_unreadable(win_blob):
    """"Can't tell" must not become "expired" — the behaviour before this branch
    was to hand the token over and let the upstream answer."""
    for expiry in ("", "not-a-date", "2026-10-11T21:48:27", "2026-13-45T99:99:99+00:00"):
        win_blob(_payload(expiry))
        assert ds._read_antigravity_access_token_windows() == LIVE, expiry


def test_windows_branch_accepts_both_keyring_wrappings(win_blob):
    plain = _payload(_when(3600))
    win_blob("go-keyring-base64:" + base64.b64encode(plain.encode()).decode())
    assert ds._read_antigravity_access_token_windows() == LIVE
    # prefix present but the body is not base64: the unwrap gives up and the
    # loose-token regex still finds it.
    win_blob("go-keyring-base64:" + plain)
    assert ds._read_antigravity_access_token_windows() == LIVE
    win_blob("no json at all, just ya29.loose_token in the text")
    assert ds._read_antigravity_access_token_windows() == "ya29.loose_token"


def test_windows_branch_returns_none_when_there_is_no_entry_or_no_token(win_blob):
    win_blob(None)
    assert ds._read_antigravity_access_token_windows() is None
    win_blob(json.dumps({"auth_method": "oauth", "token": {}}))
    assert ds._read_antigravity_access_token_windows() is None
    win_blob("")
    assert ds._read_antigravity_access_token_windows() is None


def test_dispatcher_picks_the_branch_by_os(monkeypatch):
    hits = {"win": 0, "mac": 0}

    def _win():
        hits["win"] += 1
        return "from-win-store"

    def _mac_run(*a, **k):
        hits["mac"] += 1

        class _R:
            stdout = ""
        return _R()

    monkeypatch.setattr(ds, "_read_antigravity_access_token_windows", _win)
    monkeypatch.setattr(ds.subprocess, "run", _mac_run)

    monkeypatch.setattr(ds.os, "name", "nt")
    assert ds.read_antigravity_access_token() == "from-win-store"
    assert hits == {"win": 1, "mac": 0}, "Windows must not shell out to `security`"

    monkeypatch.setattr(ds.os, "name", "posix")
    monkeypatch.setattr(ds, "_read_antigravity_access_token_windows",
                        lambda: (_ for _ in ()).throw(AssertionError("posix must not read win store")))
    monkeypatch.setattr(ds.subprocess, "run", lambda *a, **k: type(
        "R", (), {"stdout": "go-keyring-base64:" + base64.b64encode(
            ("x " + LIVE + " y").encode()).decode()})())
    assert ds.read_antigravity_access_token() == LIVE


def test_iso_to_ts_shapes():
    from datetime import datetime, timezone

    # 7-digit fraction + offset, exactly what agy writes.
    got = ds._iso_to_ts("2026-10-11T21:48:27.9478661+08:00")
    assert got == datetime(2026, 10, 11, 13, 48, 27, 947866,
                           tzinfo=timezone.utc).timestamp()
    assert ds._iso_to_ts("2026-10-11T21:48:27.947Z") == datetime(
        2026, 10, 11, 21, 48, 27, 947000, tzinfo=timezone.utc).timestamp()
    assert ds._iso_to_ts("2026-10-11T21:48:27+08:00") == pytest.approx(
        datetime(2026, 10, 11, 13, 48, 27, tzinfo=timezone.utc).timestamp(), abs=0.001)
    assert ds._iso_to_ts("2026-10-11T21:48:27") is None  # naive: refuse to judge
    assert ds._iso_to_ts("") is None
    assert ds._iso_to_ts("tomorrow") is None
