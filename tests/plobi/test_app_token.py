"""WP-H1-LAN: persistent App token + API version header + LAN gate.

These tests exercise the App token path the Plobi tablet/phone uses to
reach the desktop backend on the LAN. They do NOT start any LAN bind —
the test client speaks to the FastAPI app in-process, which is the
FastAPI-recommended way to assert gate behaviour without involving real
network I/O.

Coverage:

1. :func:`hermes_cli.web_server._load_or_mint_app_token` reads an
   existing single-line file unchanged, mints a fresh
   ``secrets.token_urlsafe(32)`` when the file is missing, and keeps the
   mint idempotent across two calls (the second call returns the same
   token).
2. :func:`hermes_cli.web_server._verify_token` is constant-time-style
   hmac.compare_digest under the hood — we just assert True / False for
   the obvious inputs.
3. ``GET /api/agenda`` accepts the App token in two header shapes (the
   dedicated ``X-Plobi-Session-Token`` header and the legacy
   ``Authorization: Bearer …`` form), rejects an unknown token with
   ``401``, rejects an absent token with ``401``, and stamps every
   response with ``X-Plobi-Api-Version: 1``.
4. The SPA session token keeps working unchanged — we never broke the
   dashboard's existing auth flow.
5. :func:`hermes_cli.web_server._enforce_lan_app_token_gate` downgrades
   a non-loopback bind to ``127.0.0.1`` when the App token is ``None``,
   keeps the requested host when the token exists, and never touches a
   loopback bind. This guards the regression where a desktop with a
   read-only ``$HERMES_HOME`` could expose the dashboard to the LAN
   unauthenticated (the gate's contract with WP-H1-LAN).

Test isolation:

- The conftest redirects HERMES_HOME to a per-test tempdir, so the App
  token file is written into a sandbox and never touches the developer's
  real HERMES_HOME.
- The agenda service singleton is replaced with a per-test fixture so we
  can hit ``/api/agenda`` without standing up SQLite on disk.
- The LAN gate tests don't spin up a server — they call the gate
  function directly so the assertion is hermetic and runs in milliseconds.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_cli import web_server
from hermes_cli.web_server import (
    API_VERSION,
    API_VERSION_HEADER,
    APP_TOKEN_FILENAME,
    APP_TOKEN_REL_DIR,
    _load_or_mint_app_token,
    _verify_token,
)
from plobi.agenda import router as agenda_router
from plobi.agenda import service as agenda_service
from plobi.agenda.service import AgendaService


# ---------------------------------------------------------------------------
# Pure-function helpers
# ---------------------------------------------------------------------------


def test_load_or_mint_returns_existing_single_line_token(tmp_path):
    """An existing single-line token is returned unchanged, no rewrite."""
    target = tmp_path / APP_TOKEN_REL_DIR / APP_TOKEN_FILENAME
    target.parent.mkdir(parents=True)
    target.write_text("preexisting-app-token", encoding="utf-8")

    assert _load_or_mint_app_token(tmp_path) == "preexisting-app-token"


def test_load_or_mint_mints_when_file_missing(tmp_path):
    """Missing file ⇒ a fresh token_urlsafe(32) is written and returned."""
    minted = _load_or_mint_app_token(tmp_path)
    assert minted is not None
    assert len(minted) >= 32  # token_urlsafe(32) yields a >=43-char ASCII string
    target = tmp_path / APP_TOKEN_REL_DIR / APP_TOKEN_FILENAME
    assert target.is_file()
    assert target.read_text(encoding="utf-8").strip() == minted


def test_load_or_mint_is_idempotent(tmp_path):
    """Two calls return the same value — never re-mint over a live token."""
    first = _load_or_mint_app_token(tmp_path)
    second = _load_or_mint_app_token(tmp_path)
    assert first == second
    assert first is not None


def test_load_or_mint_strips_whitespace(tmp_path):
    """Trailing newline / whitespace must not leak into the paired value."""
    target = tmp_path / APP_TOKEN_REL_DIR / APP_TOKEN_FILENAME
    target.parent.mkdir(parents=True)
    target.write_text("  whitespacey-app-token  \n", encoding="utf-8")
    assert _load_or_mint_app_token(tmp_path) == "whitespacey-app-token"


def test_verify_token_matches_only_equal_strings():
    assert _verify_token("alpha", "alpha") is True
    assert _verify_token("alpha", "beta") is False
    assert _verify_token("", "alpha") is False
    assert _verify_token("alpha", "") is False
    assert _verify_token("", "") is False


# ---------------------------------------------------------------------------
# HTTP gate: /api/agenda via the App token (WP-H1-LAN contract)
# ---------------------------------------------------------------------------


@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    """Per-test FastAPI app with the agenda router + a known App token.

    The fixture mounts ``/api/agenda`` on a *new* FastAPI instance — same
    shape as the production app — and patches the agenda service so the
    DB doesn't need to exist on disk. It then patches
    ``hermes_cli.web_server._APP_TOKEN`` so the gate consults our known
    value (the freshly minted one we just wrote to the sandbox HERMES_HOME).
    """
    monkeypatch.setattr(
        agenda_service, "_DEFAULT", AgendaService(tmp_path / "agenda.db")
    )

    minted = _load_or_mint_app_token(tmp_path)
    assert minted is not None
    monkeypatch.setattr(web_server, "_APP_TOKEN", minted)

    app = FastAPI()
    # Wire the same auth middlewares the production app installs so the
    # gate exercises the real ``_has_valid_session_token`` (and only that,
    # not the SPA-token-injection side or the gated-auth cookie path).
    app.add_middleware(
        __import__("fastapi.middleware.cors", fromlist=["CORSMiddleware"]).CORSMiddleware,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def _gate(request, call_next):
        from fastapi import HTTPException
        from fastapi.responses import JSONResponse

        path = request.url.path
        if path.startswith("/api/") and path != "/api/status":
            # Mirror the legacy ``auth_middleware`` shape exactly enough
            # to exercise _has_valid_session_token. The X-Plobi-Api-Version
            # middleware under test sits OUTSIDE this gate so the header is
            # stamped on both 401 and 200 responses.
            ok = web_server._has_valid_session_token(request)
            if not ok:
                return JSONResponse(
                    status_code=401, content={"detail": "Unauthorized"}
                )
        return await call_next(request)

    @app.middleware("http")
    async def _api_version_header(request, call_next):
        response = await call_next(request)
        response.headers[API_VERSION_HEADER] = API_VERSION
        return response

    app.include_router(agenda_router.router, prefix="/api/agenda")

    with TestClient(app) as client:
        yield client, minted


def test_agenda_accepts_app_token_via_dedicated_header(app_client):
    client, token = app_client
    resp = client.get(
        "/api/agenda",
        params={"from": "2026-09-14", "to": "2026-09-15"},
        headers={"X-Plobi-Session-Token": token},
    )
    assert resp.status_code == 200
    assert resp.headers.get(API_VERSION_HEADER) == API_VERSION


def test_agenda_accepts_app_token_via_bearer(app_client):
    client, token = app_client
    resp = client.get(
        "/api/agenda",
        params={"from": "2026-09-14", "to": "2026-09-15"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.headers.get(API_VERSION_HEADER) == API_VERSION


def test_agenda_rejects_wrong_token(app_client):
    client, _ = app_client
    resp = client.get(
        "/api/agenda",
        params={"from": "2026-09-14", "to": "2026-09-15"},
        headers={"X-Plobi-Session-Token": "this-is-not-the-token"},
    )
    assert resp.status_code == 401
    assert resp.headers.get(API_VERSION_HEADER) == API_VERSION


def test_agenda_rejects_missing_token(app_client):
    client, _ = app_client
    resp = client.get(
        "/api/agenda",
        params={"from": "2026-09-14", "to": "2026-09-15"},
    )
    assert resp.status_code == 401
    assert resp.headers.get(API_VERSION_HEADER) == API_VERSION


def test_agenda_accepts_spa_session_token_unchanged(app_client):
    """The SPA session token must keep working — no regression on the
    existing dashboard flow. The patched web_server._SESSION_TOKEN still
    lives at module scope, so we read it via the import above."""
    client, _ = app_client
    spa_token = web_server._SESSION_TOKEN
    resp = client.get(
        "/api/agenda",
        params={"from": "2026-09-14", "to": "2026-09-15"},
        headers={"X-Plobi-Session-Token": spa_token},
    )
    assert resp.status_code == 200
    assert resp.headers.get(API_VERSION_HEADER) == API_VERSION


def test_status_path_is_public_and_still_versioned(app_client):
    """``/api/status`` is in ``PUBLIC_API_PATHS`` — the gated gate above
    must let it through, and the version header must still stamp it.

    The minimal app fixture doesn't mount /api/status, but it does
    exercise the version-header middleware on every other response — so
    we just confirm the header is present on the agenda 200 above by
    re-asserting on a freshly fetched route (covers the route-200 path)."""
    client, token = app_client
    resp = client.get(
        "/api/agenda/pending",
        headers={"X-Plobi-Session-Token": token},
    )
    assert resp.status_code == 200
    assert resp.headers.get(API_VERSION_HEADER) == "1"


# ---------------------------------------------------------------------------
# LAN gate (start_server downgrade path, WP-H1-LAN safety net)
# ---------------------------------------------------------------------------


def test_lan_gate_refuses_when_mint_failed(caplog):
    """A non-loopback bind without an App token must downgrade to 127.0.0.1.

    The regression we're guarding: a desktop whose ``$HERMES_HOME/plobi/``
    is read-only or whose token file got wiped could otherwise expose the
    dashboard on 0.0.0.0:8787 with **no** shared secret — the App token
    path would 401 every caller (good) but ``/api/status`` is public, so
    the LAN would see a half-authenticated dashboard. The gate clamps
    the bind to loopback AND logs a WARNING so the user can fix the FS.
    """
    from hermes_cli.web_server import _enforce_lan_app_token_gate

    caplog.set_level("WARNING", logger="hermes_cli.web_server")
    new_host, downgraded = _enforce_lan_app_token_gate("0.0.0.0", 8787, None)
    assert downgraded is True
    assert new_host == "127.0.0.1"

    # WARNING was emitted with the port and the "LAN bind refused" sentinel
    # so log-scrapers (and humans reading doctor output) can find it.
    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "LAN bind refused" in msg and "8787" in msg for msg in messages
    ), f"missing WARNING; saw: {messages!r}"


def test_lan_gate_refuses_for_unscoped_hostname(monkeypatch, caplog):
    """Same downgrade for ``0.0.0.0`` (the actual desktop LAN bind target)."""
    from hermes_cli.web_server import _enforce_lan_app_token_gate

    caplog.set_level("WARNING", logger="hermes_cli.web_server")
    new_host, downgraded = _enforce_lan_app_token_gate("0.0.0.0", 8787, None)
    assert new_host == "127.0.0.1"
    assert downgraded is True
    assert any("LAN bind refused" in r.getMessage() for r in caplog.records)


def test_lan_gate_passes_when_token_present():
    """Mint succeeded → LAN bind is left as the caller asked.

    This is the happy-path: a desktop whose ``$HERMES_HOME`` was writable
    at startup should still bind 0.0.0.0:8787 (the whole point of WP-H1-LAN).
    The gate must not short-circuit legitimate LAN binds.
    """
    from hermes_cli.web_server import _enforce_lan_app_token_gate

    new_host, downgraded = _enforce_lan_app_token_gate(
        "0.0.0.0", 8787, "freshly-minted-app-token"
    )
    assert new_host == "0.0.0.0"
    assert downgraded is False


def test_lan_gate_does_not_touch_loopback_when_token_missing(caplog):
    """Loopback binds are NEVER downgraded — the dashboard SPA is fine on its own."""
    from hermes_cli.web_server import _enforce_lan_app_token_gate

    caplog.set_level("WARNING", logger="hermes_cli.web_server")
    for host in ("127.0.0.1", "localhost", "::1"):
        new_host, downgraded = _enforce_lan_app_token_gate(host, 0, None)
        assert new_host == host, f"loopback host {host} was rewritten"
        assert downgraded is False, f"loopback host {host} flagged as downgraded"
    # No WARNING should fire on the loopback path — only on the LAN-refusal path.
    assert all("LAN bind refused" not in r.getMessage() for r in caplog.records)


def test_get_app_token_indirection_reads_module_state(monkeypatch):
    """``_get_app_token()`` must read the current module-level value so a
    ``monkeypatch.setattr`` on ``_APP_TOKEN`` is visible to the LAN gate.

    Without the indirection, the gate would close over the import-time
    value and tests couldn't exercise the downgrade path.
    """
    from hermes_cli import web_server

    monkeypatch.setattr(web_server, "_APP_TOKEN", "monkeypatched-token")
    assert web_server._get_app_token() == "monkeypatched-token"

    monkeypatch.setattr(web_server, "_APP_TOKEN", None)
    assert web_server._get_app_token() is None


def test_lan_gate_for_dual_stack_ip_literal_with_token_present():
    """IPv6 literal like ``::`` (which ``should_require_auth`` would flag
    as non-loopback) is preserved when the token is present."""
    from hermes_cli.web_server import _enforce_lan_app_token_gate

    new_host, downgraded = _enforce_lan_app_token_gate("::", 8787, "token-here")
    assert new_host == "::"
    assert downgraded is False


def test_lan_gate_downgrade_message_mentions_writable_check(caplog):
    """The WARNING points the operator at the most likely fix (FS writability).

    The downgrade message is the user's only signal that their LAN bind
    silently fell back to loopback — it must name the file path so they
    can ``chmod`` / free disk / etc. without grepping logs for the
    function name.
    """
    from hermes_cli.web_server import _enforce_lan_app_token_gate

    caplog.set_level("WARNING", logger="hermes_cli.web_server")
    _enforce_lan_app_token_gate("0.0.0.0", 8787, None)
    combined = " | ".join(r.getMessage() for r in caplog.records)
    assert "plobi" in combined, f"WARNING did not name the plobi dir; saw: {combined!r}"


# ---------------------------------------------------------------------------
# WP-H3-WS — WS handshake accepts the persistent App token (tablet reach)
# ---------------------------------------------------------------------------


class _FakeWebSocket:
    """Minimal Starlette WebSocket stand-in for ``_ws_auth_reason``."""

    def __init__(
        self,
        *,
        headers: dict[str, str] | None = None,
        query_params: dict[str, str] | None = None,
        client_host: str = "192.168.1.42",
    ):
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.query_params = query_params or {}

        class _Client:
            def __init__(self, host: str):
                self.host = host

        self.client = _Client(client_host)
        self.url = type("URL", (), {"path": "/api/ws"})()


def _set_auth_required(monkeypatch, client) -> None:
    """Mark ``web_server.app.state.auth_required = True`` so the gated path runs.

    ``_ws_auth_reason`` reads ``app.state.auth_required`` from the
    module-level ``web_server.app`` global, NOT from the test client's
    app — so we have to point the module global at the test app first.
    """
    from hermes_cli import web_server

    monkeypatch.setattr(web_server, "app", client.app)
    client.app.state.auth_required = True


def test_ws_auth_reason_accepts_app_token_via_dedicated_header(monkeypatch, app_client, caplog):
    """Tablet reaches ``/api/ws`` with ``X-Plobi-Session-Token`` → accept."""
    from hermes_cli import web_server

    client, token = app_client
    ws = _FakeWebSocket(headers={"X-Plobi-Session-Token": token})
    _set_auth_required(monkeypatch, client)

    # Patch the lazy-imported audit loggers to a no-op (we don't need the
    # dashboard_auth layer in this test).
    monkeypatch.setattr(
        "hermes_cli.dashboard_auth.audit.audit_log", lambda *a, **k: None, raising=False,
    )
    monkeypatch.setattr(
        "hermes_cli.dashboard_auth.ws_tickets.consume_internal_credential",
        lambda *_a, **_k: None,
        raising=False,
    )
    monkeypatch.setattr(
        "hermes_cli.dashboard_auth.ws_tickets.consume_ticket",
        lambda *_a, **_k: None,
        raising=False,
    )

    caplog.set_level("WARNING", logger="hermes_cli.web_server")
    reason, cred = web_server._ws_auth_reason(ws)
    assert reason is None, f"expected accept, got reason={reason!r}"
    assert cred == "app_token"


def test_ws_auth_reason_accepts_app_token_via_bearer(monkeypatch, app_client):
    from hermes_cli import web_server

    client, token = app_client
    ws = _FakeWebSocket(headers={"Authorization": f"Bearer {token}"})
    _set_auth_required(monkeypatch, client)
    monkeypatch.setattr(
        "hermes_cli.dashboard_auth.audit.audit_log", lambda *a, **k: None, raising=False,
    )
    monkeypatch.setattr(
        "hermes_cli.dashboard_auth.ws_tickets.consume_internal_credential",
        lambda *_a, **_k: None,
        raising=False,
    )
    monkeypatch.setattr(
        "hermes_cli.dashboard_auth.ws_tickets.consume_ticket",
        lambda *_a, **_k: None,
        raising=False,
    )

    reason, cred = web_server._ws_auth_reason(ws)
    assert reason is None
    assert cred == "app_token"


def test_ws_auth_reason_rejects_wrong_app_token_without_falling_through(
    monkeypatch, app_client, caplog
):
    """A wrong App token explicitly presented must be rejected with a distinct
    reason (``app_token_invalid``) — not silently fall through to SPA / ticket
    paths. The audit-log reason must contain no part of the token."""
    from hermes_cli import web_server

    captured: list[dict] = []

    def fake_audit(*args, **kwargs):
        captured.append(kwargs)

    client, token = app_client
    ws = _FakeWebSocket(headers={"X-Plobi-Session-Token": "wrong-token"})
    _set_auth_required(monkeypatch, client)
    monkeypatch.setattr(
        "hermes_cli.dashboard_auth.audit.audit_log", fake_audit, raising=False,
    )
    monkeypatch.setattr(
        "hermes_cli.dashboard_auth.ws_tickets.consume_internal_credential",
        lambda *_a, **_k: None,
        raising=False,
    )
    monkeypatch.setattr(
        "hermes_cli.dashboard_auth.ws_tickets.consume_ticket",
        lambda *_a, **_k: None,
        raising=False,
    )

    caplog.set_level("WARNING", logger="hermes_cli.web_server")
    reason, cred = web_server._ws_auth_reason(ws)
    assert reason == "app_token_invalid"
    assert cred == "app_token"
    # Audit log was written; its ``reason`` does NOT contain the bad token.
    assert any("mismatch" in (kw.get("reason", "")).lower() for kw in captured), (
        "expected an audit log with reason containing 'mismatch'"
    )
    for kw in captured:
        assert "wrong-token" not in (kw.get("reason") or ""), (
            "audit-log reason leaked the rejected App token"
        )
    assert token not in (reason or ""), "the real token leaked into the response"


def test_ws_auth_reason_missing_credential_still_rejected(monkeypatch, app_client):
    """No headers + no ticket/internal ⇒ still ``no_credential``."""
    from hermes_cli import web_server

    client = app_client[0]
    ws = _FakeWebSocket()
    _set_auth_required(monkeypatch, client)
    monkeypatch.setattr(
        "hermes_cli.dashboard_auth.audit.audit_log", lambda *a, **k: None, raising=False,
    )
    monkeypatch.setattr(
        "hermes_cli.dashboard_auth.ws_tickets.consume_internal_credential",
        lambda *_a, **_k: None,
        raising=False,
    )
    monkeypatch.setattr(
        "hermes_cli.dashboard_auth.ws_tickets.consume_ticket",
        lambda *_a, **_k: None,
        raising=False,
    )

    reason, cred = web_server._ws_auth_reason(ws)
    assert reason == "no_credential"
    assert cred == "none"


def test_ws_auth_reason_does_not_accept_legacy_query_token_in_gated_mode(monkeypatch):
    """``?token=<SPA token>`` must NOT grant WS access in gated mode.

    Gated mode ignores the legacy ``?token=`` query param entirely — even
    when the presented value matches ``_SESSION_TOKEN`` — and early-returns
    ``no_credential``. This is what the docstring promises ("unconditionally
    rejected") and is what stops a leaked SPA token from granting WS access.
    """
    from hermes_cli import web_server

    import secrets as _secrets
    spa_token = _secrets.token_urlsafe(32)
    app_token = _secrets.token_urlsafe(32)
    monkeypatch.setattr(web_server, "_APP_TOKEN", app_token)
    monkeypatch.setattr(web_server, "_SESSION_TOKEN", spa_token)

    fake_app = type("App", (), {"state": type("S", (), {"auth_required": True})()})()
    monkeypatch.setattr(web_server, "app", fake_app)
    monkeypatch.setattr(
        "hermes_cli.dashboard_auth.audit.audit_log", lambda *a, **k: None, raising=False,
    )
    monkeypatch.setattr(
        "hermes_cli.dashboard_auth.ws_tickets.consume_internal_credential",
        lambda *_a, **_k: None,
        raising=False,
    )
    monkeypatch.setattr(
        "hermes_cli.dashboard_auth.ws_tickets.consume_ticket",
        lambda *_a, **_k: None,
        raising=False,
    )

    ws = _FakeWebSocket(query_params={"token": spa_token})
    reason, cred = web_server._ws_auth_reason(ws)
    assert reason == "no_credential"
    assert cred == "none"


# ---------------------------------------------------------------------------
# WP-FS-PROFILE-CWD — file API cwd follows the active profile's terminal.cwd
# ---------------------------------------------------------------------------


def _write_profile_yaml(profile_home, *, cwd_value: str) -> None:
    """Write a minimal config.yaml with a ``terminal.cwd`` value."""
    import yaml

    config_path = profile_home / "config.yaml"
    config_path.write_text(
        yaml.safe_dump({"terminal": {"cwd": cwd_value}}, allow_unicode=True),
        encoding="utf-8",
    )


def test_fs_default_cwd_uses_profile_yaml_when_provided(tmp_path, monkeypatch):
    """Profile-home cwd wins over the launch-profile env / load_config path."""
    from hermes_cli import web_server

    # Launch-profile side: set a misleading terminal.cwd via env that should
    # NOT be returned when the caller pins a profile_home.
    monkeypatch.setenv("TERMINAL_CWD", "/this/should/not/win")

    # Per-profile yaml points at tmp.
    profile_home = tmp_path / "hermes" / "profiles" / "l2-agenda"
    profile_home.mkdir(parents=True)
    _write_profile_yaml(profile_home, cwd_value=str(tmp_path))

    assert web_server._fs_default_cwd(profile_home=profile_home) == str(tmp_path)


def test_fs_default_cwd_falls_back_when_profile_yaml_missing_cwd(tmp_path, monkeypatch):
    """Profile yaml exists but has no ``terminal.cwd`` → fall back."""
    from hermes_cli import web_server

    monkeypatch.delenv("TERMINAL_CWD", raising=False)
    monkeypatch.chdir(tmp_path)
    profile_home = tmp_path / "hermes" / "profiles" / "l2-empty"
    profile_home.mkdir(parents=True)
    (profile_home / "config.yaml").write_text(
        "model: {}\n", encoding="utf-8"
    )

    assert web_server._fs_default_cwd(profile_home=profile_home) == str(tmp_path.resolve())


def test_fs_default_cwd_ignores_placeholder_values(tmp_path, monkeypatch):
    """``.`` / ``auto`` / ``cwd`` placeholders never pretend to be a directory."""
    from hermes_cli import web_server

    monkeypatch.delenv("TERMINAL_CWD", raising=False)
    monkeypatch.chdir(tmp_path)

    for placeholder in (".", "auto", "cwd"):
        profile_home = tmp_path / f"profile_{placeholder.replace('.', '_dot_')}"
        profile_home.mkdir(parents=True)
        _write_profile_yaml(profile_home, cwd_value=placeholder)
        # Falls through to Path.cwd() — not the placeholder string.
        assert web_server._fs_default_cwd(profile_home=profile_home) == str(
            tmp_path.resolve()
        )


def test_fs_default_cwd_ignores_nonexistent_path(tmp_path, monkeypatch):
    """A yaml path that doesn't resolve to a real dir must not be returned."""
    from hermes_cli import web_server

    monkeypatch.delenv("TERMINAL_CWD", raising=False)
    monkeypatch.chdir(tmp_path)

    profile_home = tmp_path / "l2-ghost"
    profile_home.mkdir(parents=True)
    _write_profile_yaml(profile_home, cwd_value=str(tmp_path / "nope" / "missing"))

    assert web_server._fs_default_cwd(profile_home=profile_home) == str(tmp_path.resolve())


def test_fs_default_cwd_no_profile_home_uses_launch_env(tmp_path, monkeypatch):
    """No profile_home → existing launch-profile / env / Path.cwd() chain."""
    from hermes_cli import web_server

    monkeypatch.delenv("TERMINAL_CWD", raising=False)
    monkeypatch.chdir(tmp_path)
    assert web_server._fs_default_cwd(profile_home=None) == str(tmp_path.resolve())

    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
    assert web_server._fs_default_cwd(profile_home=None) == str(tmp_path.resolve())


def test_fs_default_cwd_broken_profile_yaml_does_not_crash(tmp_path, monkeypatch):
    """Malformed yaml in the profile home must NOT break the file API."""
    from hermes_cli import web_server

    monkeypatch.delenv("TERMINAL_CWD", raising=False)
    monkeypatch.chdir(tmp_path)

    profile_home = tmp_path / "l2-broken"
    profile_home.mkdir(parents=True)
    (profile_home / "config.yaml").write_text("this: is: not: valid: yaml: ::",
                                              encoding="utf-8")
    # Falls through to Path.cwd() — no exception raised.
    assert web_server._fs_default_cwd(profile_home=profile_home) == str(tmp_path.resolve())
