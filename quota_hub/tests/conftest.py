"""Shared fixtures for the hub's own test suite.

The two evidence ledgers (`quota_hub/login_ledger.py`, `quota_hub/model_health.py`) are
the only pieces of this package that write, so every test here gets a throwaway copy of
both. Without this a passing `test_authflow.py` would leave a real file behind in
`PLOBI_HOME` — and a test suite that reports "nobody is signed in" or "this model was
refused" from a file it wrote itself is not measuring anything.
"""

from __future__ import annotations

import pytest
from quota_hub import login_ledger, model_health


@pytest.fixture(autouse=True)
def isolated_ledgers(tmp_path, monkeypatch):
    monkeypatch.setenv(login_ledger.LEDGER_ENV, str(tmp_path / "login-ledger.json"))
    monkeypatch.setenv(model_health.MODEL_HEALTH_ENV, str(tmp_path / "model-health.json"))
    # Spawn-based legs get a scratch cwd by default; keep that out of the real home too.
    monkeypatch.setenv("QUOTA_HUB_WORKSPACE_ROOT", str(tmp_path / "workspaces"))


@pytest.fixture(autouse=True)
def no_external_tools(monkeypatch):
    """No test may drive another product's binary.

    The Antigravity leg asks Antigravity Tools to re-push its account when the shared
    Keychain slot goes stale (measured 2026-10-29). That is a real side effect on the
    owner's machine — it switches the account the agy CLI uses — so the whole suite runs
    with the bridge stubbed out.
    """
    from quota_hub.providers.apps.antigravity import tool_bridge

    monkeypatch.setattr(tool_bridge, "refresh_via_tool", lambda **_kw: (False, "stubbed in tests"))


@pytest.fixture(autouse=True)
def no_browser(monkeypatch):
    """No test may open a real browser tab.

    Measured 2026-10-29: a test called `google_oauth.sign_in` with the network stubbed but
    the browser left alone, so every suite run dropped a real Chrome window on the owner's
    desktop — eight of them by the time he said so. Tests still assert the URL where that
    matters; the *opening* is stubbed here once, for the whole suite.
    """
    import webbrowser

    monkeypatch.setattr(webbrowser, "open", lambda *a, **k: True)
