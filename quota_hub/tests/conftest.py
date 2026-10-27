"""Shared fixtures for the hub's own test suite.

The login ledger (`quota_hub/login_ledger.py`) is the one piece of this package that
writes, so every test here gets a throwaway copy. Without this a passing
`test_authflow.py` would leave a real `quota_hub-login-ledger.json` behind in
`PLOBI_HOME` — and a test suite that reports "nobody is signed in" from a file it
wrote itself is not measuring anything.
"""

from __future__ import annotations

import pytest
from quota_hub import login_ledger


@pytest.fixture(autouse=True)
def isolated_login_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv(login_ledger.LEDGER_ENV, str(tmp_path / "login-ledger.json"))
