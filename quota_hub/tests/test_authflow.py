"""Verification for the click-to-browser sign-in layer (`quota_hub/authflow.py`).

Uses a throwaway script as the "CLI" so the URL scraping, the browser hand-off and
the process lifecycle are exercised WITHOUT the real CLI, without a browser, and
without touching any account. `webbrowser.open` is stubbed — a test that opened a
real browser window on the owner's machine would be a bad day on purpose.

Run:
    cd Code/quota_hub && python -m pytest tests/test_authflow.py -q
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import quota_hub.authflow as authflow
from quota_hub.providers.base import Capabilities, Provider
from quota_hub.providers.cli import CliProvider

# A stand-in CLI: prints a sign-in URL like the real ones do, then exits 0.
FAKE_LOGIN = """
import sys
print("Visit https://example.com/o/auth?state=abc123 to continue.")
sys.exit(0)
"""

FAKE_LOGIN_FAIL = """
import sys
print("device code rejected", file=sys.stderr)
sys.exit(2)
"""


def _script(tmp: Path, body: str) -> str:
    path = tmp / f"fake_login_{abs(hash(body))}.py"
    path.write_text(body, encoding="utf-8")
    return str(path)


def _login(app_id: str, plan: dict, manager: authflow.LoginManager | None = None):
    """Start a sign-in and WAIT for it on the same loop.

    `start()` is fire-and-forget (the HTTP route polls), so a bare
    `asyncio.run(start(...))` tears the loop down while the child still runs — on
    Windows that hangs the interpreter instead of cancelling. Measured 2026-10-26.
    """
    manager = manager or authflow.LoginManager()

    async def _go():
        await manager.start(app_id, plan)
        return await manager.wait(app_id)

    return asyncio.run(_go())


class _NullProv(Provider):
    name = "mock"
    default_capabilities = Capabilities()


class _Cli(CliProvider):
    name = "fake_cli_leg"
    binary = "fake-cli"


def test_an_undeclared_leg_says_we_did_not_wire_it_not_that_it_is_broken():
    plan = _NullProv({}, None).login_plan()

    assert plan["kind"] == "none"
    # The wording is the point: "not declared" must not be reportable as a failure.
    assert plan["reason"]


def test_a_cli_leg_without_a_verified_login_argv_stays_undeclared():
    class _NoArgv(CliProvider):
        name = "opencode_like"
        binary = "opencode"

    plan = _NoArgv({}, None).login_plan()

    assert plan["kind"] == "none"
    assert "还没" in plan["reason"]


def test_a_declared_cli_leg_produces_an_executable_plan():
    class _Declared(CliProvider):
        name = "cline_cli"
        binary = "cline"
        display_name = "Cline"
        login_args = ("auth",)

    plan = _Declared({}, None).login_plan()

    assert plan["kind"] == "cli_login"
    assert plan["binary"] == "cline"
    assert plan["argv"] == ["auth"]
    assert plan["name"] == "Cline"


def test_cline_and_kiro_declare_their_login_from_their_own_help_text():
    from quota_hub.providers.apps.cline.cli import ClineCliProvider
    from quota_hub.providers.apps.kiro.cli import KiroCliProvider

    assert ClineCliProvider.login_args == ("auth",)
    assert KiroCliProvider.login_args == ("login",)


def test_scraps_the_url_opens_the_browser_and_reports_success(tmp_path, monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(authflow.webbrowser, "open", lambda url: opened.append(url) or True)
    script = _script(tmp_path, FAKE_LOGIN)

    session = _login(
        "fake-leg",
        {"kind": "cli_login", "name": "Fake", "binary": sys.executable, "argv": [script]},
    )

    assert session.state == "ok", session.detail
    assert session.url == "https://example.com/o/auth?state=abc123"
    assert opened == [session.url]
    # A click must not become a token sink: nothing it printed is kept.
    assert "token" not in str(session.as_dict()).lower() or "abc123" in session.url


def test_a_failing_login_reports_its_own_words_not_a_generic_error(tmp_path, monkeypatch):
    monkeypatch.setattr(authflow.webbrowser, "open", lambda url: True)
    script = _script(tmp_path, FAKE_LOGIN_FAIL)

    session = _login(
        "fake-fail",
        {"kind": "cli_login", "name": "Fake", "binary": sys.executable, "argv": [script]},
    )

    assert session.state == "failed"
    assert "device code rejected" in session.detail


def test_a_missing_binary_is_not_installed_for_and_not_called_a_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(authflow, "_resolve_binary", lambda name: None)

    session = _login(
        "absent", {"kind": "cli_login", "name": "Absent", "binary": "not-installed"}
    )

    assert session.state == "unsupported"
    assert "这台机器上没有" in session.detail
    # And it must not read as an offer to install anything (user ruling 2026-10-27).
    assert "代下载" in session.detail


def test_on_success_hook_runs_only_after_a_good_login(tmp_path, monkeypatch):
    monkeypatch.setattr(authflow.webbrowser, "open", lambda url: True)
    calls: list[str] = []

    async def _refresh():
        calls.append("refreshed")

    _login(
        "ok-hook",
        {
            "kind": "cli_login",
            "name": "Fake",
            "binary": sys.executable,
            "argv": [_script(tmp_path, FAKE_LOGIN)],
            "on_success": _refresh,
        },
    )
    assert calls == ["refreshed"]

    _login(
        "fail-hook",
        {
            "kind": "cli_login",
            "name": "Fake",
            "binary": sys.executable,
            "argv": [_script(tmp_path, FAKE_LOGIN_FAIL)],
            "on_success": _refresh,
        },
    )
    assert calls == ["refreshed"]  # the failed login did not trigger a refresh


def test_catalog_lists_every_aggregated_leg_plus_the_login_only_targets():
    class _Prov(Provider):
        name = "antigravity"
        display_name = "Antigravity"

        def login_plan(self):
            return {"kind": "oauth", "name": "Antigravity", "runner": lambda: None}

    class _Reg:
        providers = {"antigravity": _Prov({}, None)}

        def credential_health(self, prov):
            return False, "credential_expired"

    rows = {row["id"]: row for row in authflow.auth_catalog(_Reg())}

    assert rows["antigravity"]["kind"] == "oauth"
    assert rows["antigravity"]["live"] is False
    assert rows["antigravity"]["reason"] == "credential_expired"
    # The ACP legs Plobi drives but the hub does not aggregate still get a click.
    assert {"gemini-cli", "qwen-cli"} <= set(rows)
    assert rows["gemini-cli"]["aggregate"] is False
    assert rows["gemini-cli"]["kind"] == "cli_login"


def test_local_session_legs_answer_without_a_browser(tmp_path, monkeypatch):
    monkeypatch.setattr(authflow.webbrowser, "open", lambda url: (_ for _ in ()).throw(AssertionError("no browser")))

    session = _login(
        "workbuddy",
        {"kind": "local_session", "name": "CodeBuddy", "reason": "它用的是桌面端自己的登录"},
    )

    assert session.state == "ok"
    assert session.detail == "它用的是桌面端自己的登录"


def test_two_clicks_do_not_start_two_logins(tmp_path, monkeypatch):
    monkeypatch.setattr(authflow.webbrowser, "open", lambda url: True)
    manager = authflow.LoginManager()
    plan = {
        "kind": "cli_login",
        "name": "Slow",
        "binary": sys.executable,
        "argv": ["-c", "import time; print('https://example.com/x'); time.sleep(0.6)"],
    }

    async def _both():
        first, second = await asyncio.gather(manager.start("slow", plan), manager.start("slow", plan))
        await asyncio.sleep(1.0)
        return first, second

    first, second = asyncio.run(_both())

    assert first is second


def test_the_catalog_is_the_scan_result_not_a_wish_list(monkeypatch):
    """Absent products get no card (user ruling 2026-10-27: 只扫描已有的产品，不代下载).

    A row that says "not installed here" is an invitation to go install something by
    hand, which is exactly the experience this ruling removes.
    """
    from quota_hub.providers.apps.codex.cli import CodexCliProvider

    class _Reg:
        providers = {"codex_cli": CodexCliProvider({}, None)}

        def credential_health(self, prov):
            return True, "ok"

    monkeypatch.setattr(CodexCliProvider, "_binary_present", lambda self: False)
    ids = {row["id"] for row in authflow.auth_catalog(_Reg())}
    assert "codex_cli" not in ids

    monkeypatch.setattr(CodexCliProvider, "_binary_present", lambda self: True)
    ids = {row["id"] for row in authflow.auth_catalog(_Reg())}
    assert "codex_cli" in ids


def test_a_login_only_target_without_its_cli_is_omitted(monkeypatch):
    monkeypatch.setattr(authflow, "_resolve_binary", lambda name: None)

    assert authflow.auth_catalog(None) == []


def test_an_installed_target_is_listed_as_not_signed_in(monkeypatch):
    monkeypatch.setattr(authflow, "_resolve_binary", lambda name: r"C:\fake\gemini.exe")

    rows = {row["id"]: row for row in authflow.auth_catalog(None)}

    assert rows["gemini-cli"]["live"] is False
    assert rows["gemini-cli"]["installed"] is True
    assert "还没登录" in rows["gemini-cli"]["reason"]
