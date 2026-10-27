"""Verification for the login ledger (`quota_hub/login_ledger.py`).

The bug it exists for: the hub reported every spawn-based CLI leg as signed in,
because the registry's default verdict is "live" for anything that does not answer.
Measured 2026-10-27 on a live hub — the Kiro and Cline cards read `live: true` with
no sign-in ever observed, which took the only working click off those cards.

Run:
    cd Code && scripts/run_tests.sh -j 1 quota_hub/tests/test_login_ledger.py
"""

from __future__ import annotations

import json

import pytest
from quota_hub import login_ledger
from quota_hub.providers.cli import CliProvider


@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    """Point the ledger at a throwaway file and start from "nobody has signed in"."""
    monkeypatch.delenv("PLOBI_HOME", raising=False)
    path = tmp_path / "login-ledger.json"
    monkeypatch.setenv(login_ledger.LEDGER_ENV, str(path))
    return path


def _provider(name: str = "kiro_cli") -> CliProvider:
    return CliProvider({"binary": name, "login_args": ("login",)}, None)


def test_an_unasked_leg_is_not_evidence_of_a_sign_in(ledger):
    assert login_ledger.seen_at("kiro_cli") is None
    assert login_ledger.verified("kiro_cli") is False


def test_a_login_that_exited_zero_is_evidence(ledger):
    login_ledger.remember("kiro_cli")

    assert login_ledger.verified("kiro_cli") is True
    assert login_ledger.seen_at("kiro_cli") is not None
    # Only a timestamp and the fact we ran it. No token, no argv, no credential.
    assert set(json.loads(ledger.read_text(encoding="utf-8"))) == {"kiro_cli"}


def test_forget_takes_the_evidence_back(ledger):
    login_ledger.remember("cline_cli")
    login_ledger.forget("cline_cli")

    assert login_ledger.verified("cline_cli") is False


def test_a_torn_file_reads_as_nobody_signed_in(ledger):
    ledger.write_text("{not json at all", encoding="utf-8")

    assert login_ledger.seen_at("kiro_cli") is None
    # and a later write still lands, rather than propagating the parse error
    login_ledger.remember("kiro_cli")
    assert login_ledger.verified("kiro_cli") is True


def test_a_cli_leg_answers_not_signed_in_until_plobi_ran_its_login(ledger):
    prov = _provider()

    assert prov.credential_health() == (False, "not_signed_in")

    login_ledger.remember(prov.name)

    assert prov.credential_health() == (True, "ok")


def test_the_verdict_reaches_the_catalog_row_the_desktop_reads(ledger):
    # This is the shape the settings card is built from: `live` false plus a declared
    # login command is what puts the `+` back on the card.
    prov = _provider()
    plan = prov.login_plan()

    assert plan["kind"] == "cli_login"
    assert prov.credential_health()[0] is False
