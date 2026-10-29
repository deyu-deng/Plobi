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


def test_catalog_lists_every_aggregated_leg_plus_the_login_only_targets(monkeypatch):
    # A login-only row is still a legitimate shape (a product whose only wired door is its
    # own login command). Qwen used to be the one real entry; it moved into the registry
    # as an ACP leg on 2026-10-29, so the shape is pinned with a stand-in instead of a
    # product name that no longer lives here.
    monkeypatch.setattr(authflow, "_resolve_binary", lambda name: r"C:\fake\ghost.exe")
    monkeypatch.setattr(
        authflow,
        "LOGIN_ONLY_TARGETS",
        {"ghost-cli": {"name": "Ghost", "binary": "ghost", "argv": ["login"], "tty": True}},
    )

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
    # A login-only target still gets a click, and is not an aggregated model row.
    assert rows["ghost-cli"]["aggregate"] is False
    assert rows["ghost-cli"]["kind"] == "cli_login"
    # Gemini is NOT a login-only row: it is a registry leg with its own sign-in plan, and
    # `--reauth` was a guessed argv that opened his browser on a YouTube link instead.
    assert "gemini-cli" not in rows


def test_a_url_that_is_not_a_sign_in_page_is_never_opened(tmp_path, monkeypatch):
    """Measured 2026-10-28: a wrong argv prints usage, and usage contains a URL.

    `gemini --reauth` (which does not exist) answered with its own help, whose `--yolo`
    line links to YouTube. The hub scraped that and opened it. So the scrape now has to
    recognise a sign-in endpoint, not "the first http thing in the buffer".
    """
    opened: list[str] = []
    monkeypatch.setattr(authflow.webbrowser, "open", lambda url: opened.append(url) or True)
    body = (
        "import sys\n"
        "print('  -y, --yolo  Auto-approve all (see "
        "https://www.youtube.com/watch?v=xvFZjo5PgG0 for details)')\n"
        "print('  -h, --help  Show help')\n"
        "sys.exit(41)\n"
    )

    session = _login(
        "usage-only",
        {"kind": "cli_login", "name": "Fake", "binary": sys.executable, "argv": [_script(tmp_path, body)]},
    )

    assert session.url == ""
    assert opened == []
    assert session.state == "failed"


def test_the_sign_in_url_wins_even_when_other_urls_came_first(tmp_path, monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(authflow.webbrowser, "open", lambda url: opened.append(url) or True)
    body = (
        "import sys\n"
        "print('docs at https://example.com/docs/getting-started')\n"
        "print('sign in at https://accounts.google.com/o/oauth2/v2/auth?client_id=x&state=y')\n"
        "sys.exit(0)\n"
    )

    session = _login(
        "two-urls",
        {"kind": "cli_login", "name": "Fake", "binary": sys.executable, "argv": [_script(tmp_path, body)]},
    )

    assert session.url == "https://accounts.google.com/o/oauth2/v2/auth?client_id=x&state=y"
    assert opened == [session.url]


def test_a_loopback_callback_page_is_not_something_to_open(tmp_path, monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(authflow.webbrowser, "open", lambda url: opened.append(url) or True)
    body = (
        "import sys\n"
        "print('listening on http://localhost:8085/oauth/callback')\n"
        "print('sign in: https://sso.vendor.test/authorize?client_id=q')\n"
        "sys.exit(0)\n"
    )

    session = _login(
        "loopback",
        {"kind": "cli_login", "name": "Fake", "binary": sys.executable, "argv": [_script(tmp_path, body)]},
    )

    assert session.url == "https://sso.vendor.test/authorize?client_id=q"


def test_already_signed_in_words_are_a_success_not_a_failed_click(tmp_path, monkeypatch):
    """`kiro-cli login` on a machine that IS signed in refuses, in words, and exits 1.

    Reading that as a failure made a working leg keep offering the `+` — and put the CLI's
    own "please run kiro-cli logout" sentence on the screen, which sends the user to a
    command line. The CLI's verdict about its own state is the evidence we want.
    """
    monkeypatch.setattr(authflow.webbrowser, "open", lambda url: True)
    body = (
        "import sys\n"
        "print('error: Already logged in, please logout with kiro-cli logout first', file=sys.stderr)\n"
        "sys.exit(1)\n"
    )

    session = _login(
        "kiro_cli",
        {"kind": "cli_login", "name": "Kiro", "binary": sys.executable, "argv": [_script(tmp_path, body)]},
    )

    assert session.state == "ok", session.detail
    assert "kiro-cli logout" not in session.detail


def test_a_tty_plan_gets_a_real_window_instead_of_a_pipe(monkeypatch):
    """Some CLIs refuse their login without a terminal (`cline auth`, measured 10-28).

    The window is the fix, so the plan must actually ask for one: a new console on
    Windows, a new session elsewhere, and no pipes pretending to be a login.
    """
    seen: dict = {}

    class _Proc:
        returncode = 0

        async def wait(self):
            return 0

    async def _fake_exec(*argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return _Proc()

    monkeypatch.setattr(authflow.asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(authflow, "_resolve_binary", lambda name: r"C:\fake\cline.exe")

    session = _login(
        "cline_cli",
        {"kind": "cli_login", "name": "Cline", "binary": "cline", "argv": ["auth"], "tty": True},
    )

    assert session.state == "ok", session.detail
    assert "stdout" not in seen["kwargs"]
    if sys.platform == "win32":
        assert seen["kwargs"]["creationflags"] == authflow.subprocess.CREATE_NEW_CONSOLE
    else:
        assert seen["kwargs"]["start_new_session"] is True


def test_a_click_waits_for_the_url_it_is_supposed_to_return(tmp_path):
    """The desktop opens what the POST hands back, so the POST must not outrun the CLI.

    Measured 2026-10-28: `authViaHub` returned `pending` with an empty `url`, and the
    settings view only re-reads the catalog once — so a leg that does print a sign-in
    link still looked like a button that does nothing.
    """
    body = (
        "import time, sys\n"
        "time.sleep(0.2)\n"
        "print('go to https://accounts.google.com/o/oauth2/v2/auth?client_id=q', flush=True)\n"
        "time.sleep(0.4)\n"
        "sys.exit(0)\n"
    )
    plan = {"kind": "cli_login", "name": "Slow", "binary": sys.executable, "argv": [_script(tmp_path, body)]}
    manager = authflow.LoginManager()

    async def _go():
        # `start()` answers while the human still has the browser open, which is the whole
        # point of the wait; the task is then joined like any other login.
        # The production window is 2s; the test waits far longer so a loaded machine
        # (interpreter start-up) cannot turn a real pass into a flaky failure.
        session = await manager.start("slow-settle", plan, settle=20.0)
        at_click = session.url
        await manager.wait("slow-settle")
        return at_click, session

    at_click, session = asyncio.run(_go())

    assert at_click == "https://accounts.google.com/o/oauth2/v2/auth?client_id=q"
    assert session.state == "ok"


def test_a_click_without_the_wait_still_returns_straight_away(tmp_path):
    body = (
        "import time, sys\n"
        "time.sleep(0.5)\n"
        "print('https://accounts.google.com/o/oauth2/v2/auth?client_id=q', flush=True)\n"
        "sys.exit(0)\n"
    )
    plan = {"kind": "cli_login", "name": "Slow", "binary": sys.executable, "argv": [_script(tmp_path, body)]}
    manager = authflow.LoginManager()

    async def _go():
        session = await manager.start("slow-nowait", plan)
        at_click = session.url
        await manager.wait("slow-nowait")
        return at_click

    assert asyncio.run(_go()) == ""


def test_a_login_that_needs_no_browser_is_not_made_to_wait(tmp_path):
    async def _go():
        return await authflow.LoginManager().start(
            "local", {"kind": "local_session", "name": "CodeBuddy"}, settle=5.0
        )

    session = asyncio.run(_go())

    assert session.state == "ok"


def test_an_oauth_runner_that_reports_its_url_leaves_the_browser_to_the_shell():
    """The hub is a detached child; the desktop is the process that owns the browser."""
    calls: list[object] = []

    def _runner(on_url=None):
        calls.append(on_url)
        on_url("https://accounts.google.com/o/oauth2/v2/auth?client_id=x")

    session = _login("antigravity", {"kind": "oauth", "name": "Antigravity", "runner": _runner})

    assert session.state == "ok", session.detail
    assert session.url == "https://accounts.google.com/o/oauth2/v2/auth?client_id=x"
    assert callable(calls[0])


def test_an_old_oauth_runner_takes_no_argument_and_still_works(monkeypatch):
    ran: list[str] = []

    def _legacy():
        ran.append("ran")

    session = _login("antigravity", {"kind": "oauth", "name": "Antigravity", "runner": _legacy})

    assert session.state == "ok"
    assert ran == ["ran"]


def test_a_signed_in_leg_with_a_dead_node_is_not_offered_as_a_login_again():
    """The two verdicts are two fields, because they are two questions (measured 10-28).

    `live` answers "can it serve a turn right now" (credential ∧ node). The card's `+`
    must read the credential alone — otherwise an egress node going away tells a user who
    just signed in that he hasn't, which is exactly what the Antigravity card did.
    """
    class _Prov(Provider):
        name = "google_like"
        display_name = "Google-ish"

        def login_plan(self):
            return {"kind": "oauth", "name": "Google-ish", "runner": lambda: None}

        def signed_in(self):
            return True, "ok"

        def credential_health(self):
            return False, "proxy_unreachable"

    class _Reg:
        providers = {"google_like": _Prov({}, None)}

        def credential_health(self, prov):
            return prov.credential_health()

    row = {r["id"]: r for r in authflow.auth_catalog(_Reg())}["google_like"]

    assert row["signed_in"] is True
    assert row["live"] is False
    assert row["reason"] == "proxy_unreachable"
    assert row["signed_in_reason"] == "ok"


def test_a_leg_that_answers_nothing_about_credentials_is_listed_as_signed_in():
    """`Provider.signed_in` defaults to the credential verdict — no invented third state."""
    class _Prov(Provider):
        name = "plain"

        def credential_health(self):
            return False, "not_signed_in"

    class _Reg:
        providers = {"plain": _Prov({}, None)}

        def credential_health(self, prov):
            return prov.credential_health()

    row = {r["id"]: r for r in authflow.auth_catalog(_Reg())}["plain"]

    assert (row["signed_in"], row["live"]) == (False, False)


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
    monkeypatch.setattr(
        authflow,
        "LOGIN_ONLY_TARGETS",
        {"ghost-cli": {"name": "Ghost", "binary": "ghost", "argv": ["login"], "tty": True}},
    )

    assert authflow.auth_catalog(None) == []


def test_an_installed_target_is_listed_as_not_signed_in(monkeypatch):
    monkeypatch.setattr(authflow, "_resolve_binary", lambda name: r"C:\fake\ghost.exe")
    monkeypatch.setattr(
        authflow,
        "LOGIN_ONLY_TARGETS",
        {"ghost-cli": {"name": "Ghost", "binary": "ghost", "argv": ["login"], "tty": True}},
    )

    rows = {row["id"]: row for row in authflow.auth_catalog(None)}

    assert rows["ghost-cli"]["live"] is False
    assert rows["ghost-cli"]["installed"] is True
    assert "还没登录" in rows["ghost-cli"]["reason"]
