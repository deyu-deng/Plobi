"""The shared App token is retired (WP-APP-TOKEN, 裁定 68); this file proves it.

History: WP-H1-LAN kept ONE long-lived secret at ``$PLOBI_HOME/plobi/app_token`` and
handed it to every LAN client — the HTTP gate, the WS upgrade, and the bind-time LAN
safety net all consulted it. WP-APP-PAIR (裁定 64) made the credential per-device and
individually revocable (``dashboard_auth/devices.py``), which left the shared secret
with no role: revoking it means locking every device out at once. 裁定 68 removed it.

Coverage now:

1. **The retired symbols must not exist.** A leftover accessor is how a dead secret
   gets re-armed by accident.
2. **A stale ``app_token`` file is not a credential.** This is the real upgrade case:
   an installed home still has the file from the previous version. Presenting its
   bytes must 401 over HTTP and be refused over WS.
3. **The dashboard session token still works** in both header shapes
   (``X-Plobi-Session-Token`` / ``Authorization: Bearer …``), wrong and absent values
   still 401, ``/api/status`` stays public, and every response keeps carrying
   ``X-Plobi-Api-Version: 1`` — 401s included.
4. :func:`plobi_cli.web_server._verify_token` stays the one constant-time comparison
   behind that gate.
5. **The LAN bind gate is re-keyed on pairing, not on a secret**
   (:func:`plobi_cli.web_server._enforce_lan_pairing_gate`): a non-loopback bind
   whose pairing store cannot be written is downgraded to loopback with one WARNING;
   loopback binds are never touched. Authentication does not depend on this gate —
   ``start_server`` separately refuses a gated bind with no human auth provider.
6. **WS gated mode**: paired device token accepted; retired App token refused; the
   desktop's ephemeral session token is *not* a WS credential; legacy ``?token=``
   still refused; no presented secret reaches the reason text.

Test isolation: conftest redirects ``PLOBI_HOME`` to a per-test tempdir, the agenda
service singleton is replaced, and the device store is monkeypatched to that tempdir
(restored automatically) — nothing here writes to the developer's real home.

Placement note: the ``WP-FS-PROFILE-CWD`` block at the bottom has nothing to do with
credentials and has lived in this file for a long time. It stays put rather than
churning two files inside a retirement commit.
"""

from __future__ import annotations

import secrets

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from plobi_cli import web_server
from plobi_cli.dashboard_auth import devices as device_store_module
from plobi_cli.web_server import (
    API_VERSION,
    API_VERSION_HEADER,
    _verify_token,
)
from plobi.agenda import router as agenda_router
from plobi.agenda import service as agenda_service
from plobi.agenda.service import AgendaService

OLD_APP_TOKEN_REL_PATH = ("plobi", "app_token")
SPA_TOKEN = "known-spa-session-token"
AGENDA_RANGE = {"from": "2026-09-14", "to": "2026-09-15"}


def _use_store(monkeypatch, home) -> device_store_module.DeviceStore:
    """Point the process-wide device store at *home* for this test only."""
    store = device_store_module.DeviceStore(home)
    monkeypatch.setattr(device_store_module, "_DEFAULT", store)
    return store


# ---------------------------------------------------------------------------
# 1. The retired surface must not exist any more
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "symbol",
    [
        "_APP_TOKEN",
        "_app_token_path",
        "_load_or_mint_app_token",
        "_get_app_token",
        "_enforce_lan_app_token_gate",
        "APP_TOKEN_FILENAME",
        "APP_TOKEN_REL_DIR",
    ],
)
def test_the_app_token_surface_is_fully_removed(symbol):
    assert not hasattr(web_server, symbol), (
        f"web_server still exposes {symbol!r}; the shared App token was retired, "
        "so the accessor goes with it"
    )


# ---------------------------------------------------------------------------
# 4. Constant-time comparison helper (still used by the session gate)
# ---------------------------------------------------------------------------


def test_verify_token_matches_only_equal_strings():
    assert _verify_token("alpha", "alpha") is True
    assert _verify_token("alpha", "beta") is False
    assert _verify_token("", "alpha") is False
    assert _verify_token("alpha", "") is False
    assert _verify_token("", "") is False


# ---------------------------------------------------------------------------
# 2 + 3. HTTP gate: /api/agenda via the dashboard session token
# ---------------------------------------------------------------------------


@pytest.fixture()
def gate_client(monkeypatch, tmp_path):
    """Per-test app wired like production, gated by ``_has_valid_session_token``.

    Installs the same two middlewares as the real app — auth gate inside, API-version
    stamping outside it, so the header lands on 401s as well as 200s — and pins
    ``web_server._SESSION_TOKEN`` to a known value. ``/api/status`` is mounted as a
    stub because what is under test here is the gate's exemption, not the payload.
    """
    monkeypatch.setattr(
        agenda_service, "_DEFAULT", AgendaService(tmp_path / "agenda.db")
    )
    monkeypatch.setattr(web_server, "_SESSION_TOKEN", SPA_TOKEN)

    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def _gate(request, call_next):
        from fastapi.responses import JSONResponse

        path = request.url.path
        if path.startswith("/api/") and path != "/api/status":
            # Mirror the legacy ``auth_middleware`` shape exactly enough to
            # exercise _has_valid_session_token and nothing else.
            if not web_server._has_valid_session_token(request):
                return JSONResponse(
                    status_code=401, content={"detail": "Unauthorized"}
                )
        return await call_next(request)

    @app.middleware("http")
    async def _api_version_header(request, call_next):
        response = await call_next(request)
        response.headers[API_VERSION_HEADER] = API_VERSION
        return response

    @app.get("/api/status")
    async def _status():
        return {"ok": True}

    app.include_router(agenda_router.router, prefix="/api/agenda")

    with TestClient(app) as client:
        yield client


def _write_old_app_token(home, value: str) -> str:
    """Put a file at the *old* App-token path and hand back its contents."""
    path = home.joinpath(*OLD_APP_TOKEN_REL_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    return value


def test_agenda_accepts_session_token_via_dedicated_header(gate_client):
    resp = gate_client.get(
        "/api/agenda",
        params=AGENDA_RANGE,
        headers={"X-Plobi-Session-Token": SPA_TOKEN},
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers.get(API_VERSION_HEADER) == API_VERSION


def test_agenda_accepts_session_token_via_bearer(gate_client):
    resp = gate_client.get(
        "/api/agenda",
        params=AGENDA_RANGE,
        headers={"Authorization": f"Bearer {SPA_TOKEN}"},
    )
    assert resp.status_code == 200, resp.text


def test_agenda_rejects_wrong_token(gate_client):
    resp = gate_client.get(
        "/api/agenda",
        params=AGENDA_RANGE,
        headers={"X-Plobi-Session-Token": "this-is-not-the-token"},
    )
    assert resp.status_code == 401
    assert resp.headers.get(API_VERSION_HEADER) == API_VERSION


def test_agenda_rejects_missing_token(gate_client):
    resp = gate_client.get("/api/agenda", params=AGENDA_RANGE)
    assert resp.status_code == 401
    assert resp.headers.get(API_VERSION_HEADER) == API_VERSION


def test_a_stale_app_token_file_grants_nothing_over_http(gate_client, tmp_path):
    """Both header shapes are checked: the retired secret used to be accepted in both.

    A half-removal would still pass one of them, and the file being there is the
    normal state after an upgrade rather than a contrived input.
    """
    stale = _write_old_app_token(tmp_path, "left-over-shared-app-token")

    dedicated = gate_client.get(
        "/api/agenda", params=AGENDA_RANGE, headers={"X-Plobi-Session-Token": stale}
    )
    bearer = gate_client.get(
        "/api/agenda", params=AGENDA_RANGE, headers={"Authorization": f"Bearer {stale}"}
    )
    assert dedicated.status_code == 401, "the retired shared token still opens HTTP"
    assert bearer.status_code == 401, "the retired shared token still opens Bearer HTTP"


def test_status_path_is_public_and_still_versioned(gate_client):
    """``/api/status`` is in ``PUBLIC_API_PATHS`` — the gate exempts it, and the
    version middleware still stamps it."""
    resp = gate_client.get("/api/status")
    assert resp.status_code == 200, resp.text
    assert resp.headers.get(API_VERSION_HEADER) == "1"


# ---------------------------------------------------------------------------
# 5. LAN gate — keyed on pairing, not on a secret
# ---------------------------------------------------------------------------


def _unwritable_home(home):
    """Make the pairing dir impossible: ``<home>/plobi`` is already a file.

    The honest stand-in for a read-only / broken ``$PLOBI_HOME`` — it makes the
    store's own ``mkdir`` fail, which is exactly what the gate asks about.
    """
    (home / "plobi").write_text("not a directory", encoding="utf-8")
    return home


def test_lan_gate_refuses_when_devices_cannot_pair(monkeypatch, tmp_path, caplog):
    """Non-loopback bind + unwritable pairing store ⇒ loopback + one WARNING.

    The regression guarded: LAN mode exists so a handset can reach the desktop. If no
    device can ever be recorded, that bind is a surface nobody can join, so it is
    clamped and the operator gets a signal instead of a silently useless URL.
    """
    _use_store(monkeypatch, _unwritable_home(tmp_path))
    caplog.set_level("WARNING", logger="plobi_cli.web_server")

    new_host, downgraded = web_server._enforce_lan_pairing_gate("0.0.0.0", 8787)
    assert downgraded is True
    assert new_host == "127.0.0.1"
    assert any(
        "LAN bind refused" in r.getMessage() and "8787" in r.getMessage()
        for r in caplog.records
    ), f"missing WARNING; saw: {[r.getMessage() for r in caplog.records]!r}"


def test_lan_gate_passes_when_pairing_is_possible(monkeypatch, tmp_path):
    """A writable home keeps the bind the caller asked for — no silent clamp."""
    _use_store(monkeypatch, tmp_path)

    new_host, downgraded = web_server._enforce_lan_pairing_gate("0.0.0.0", 8787)
    assert (new_host, downgraded) == ("0.0.0.0", False)


def test_lan_gate_preserves_ipv6_literal_when_pairing_is_possible(monkeypatch, tmp_path):
    """``::`` is non-loopback to ``should_require_auth`` but a valid bind target."""
    _use_store(monkeypatch, tmp_path)

    new_host, downgraded = web_server._enforce_lan_pairing_gate("::", 8787)
    assert (new_host, downgraded) == ("::", False)


def test_lan_gate_never_touches_loopback(monkeypatch, tmp_path, caplog):
    """Loopback binds stay alone even with a broken home — the SPA needs no pairing."""
    _use_store(monkeypatch, _unwritable_home(tmp_path))
    caplog.set_level("WARNING", logger="plobi_cli.web_server")

    for host in ("127.0.0.1", "localhost", "::1"):
        new_host, downgraded = web_server._enforce_lan_pairing_gate(host, 0)
        assert new_host == host, f"loopback host {host} was rewritten"
        assert downgraded is False, f"loopback host {host} flagged as downgraded"
    assert all("LAN bind refused" not in r.getMessage() for r in caplog.records)


def test_lan_gate_warning_names_the_pairing_store(monkeypatch, tmp_path, caplog):
    """The WARNING is the operator's only signal; it must name what to fix."""
    _use_store(monkeypatch, _unwritable_home(tmp_path))
    caplog.set_level("WARNING", logger="plobi_cli.web_server")

    web_server._enforce_lan_pairing_gate("0.0.0.0", 8787)
    combined = " | ".join(r.getMessage() for r in caplog.records)
    assert "plobi" in combined, f"WARNING did not name the pairing dir; saw: {combined!r}"
    assert "PLOBI_HOME" in combined, (
        f"WARNING did not point at $PLOBI_HOME; saw: {combined!r}"
    )


# ---------------------------------------------------------------------------
# 6. WP-H3-WS — WS handshake credentials in gated mode
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


def _gated_mode(monkeypatch) -> None:
    """Put ``web_server.app.state.auth_required`` True without starting a server."""
    fake_app = type("App", (), {"state": type("S", (), {"auth_required": True})()})()
    monkeypatch.setattr(web_server, "app", fake_app)


@pytest.fixture()
def _no_other_way_in(monkeypatch):
    """Neutralise the ticket / internal paths so only the header branch is under test."""
    monkeypatch.setattr(
        "plobi_cli.dashboard_auth.audit.audit_log", lambda *a, **k: None, raising=False
    )
    monkeypatch.setattr(
        "plobi_cli.dashboard_auth.ws_tickets.consume_internal_credential",
        lambda *_a, **_k: None,
        raising=False,
    )
    monkeypatch.setattr(
        "plobi_cli.dashboard_auth.ws_tickets.consume_ticket",
        lambda *_a, **_k: None,
        raising=False,
    )


def _pair_a_device(store) -> str:
    """Pair a handset in-process and return its plaintext token."""
    code, _expires = store.issue_code()
    device_id, token = store.redeem(code, name="ws-probe-tablet")
    assert device_id and token
    return token


def test_ws_auth_reason_accepts_a_paired_device_token(
    monkeypatch, tmp_path, _no_other_way_in
):
    store = _use_store(monkeypatch, tmp_path)
    token = _pair_a_device(store)
    _gated_mode(monkeypatch)

    reason, cred = web_server._ws_auth_reason(
        _FakeWebSocket(headers={"X-Plobi-Session-Token": token})
    )
    assert reason is None, f"expected accept, got reason={reason!r}"
    assert cred == "device_token"

    bearer_reason, bearer_cred = web_server._ws_auth_reason(
        _FakeWebSocket(headers={"Authorization": f"Bearer {token}"})
    )
    assert (bearer_reason, bearer_cred) == (None, "device_token")


def test_ws_auth_reason_refuses_the_retired_app_token(
    monkeypatch, tmp_path, _no_other_way_in
):
    """A handset still holding the old shared secret must be turned away.

    It is not treated as an unknown-but-fatal credential either: the header simply is
    not a credential this path knows, so auth falls through to the ticket / internal
    checks and ends at ``no_credential``. Either way the presented secret must not
    reach the reason text.
    """
    stale = _write_old_app_token(tmp_path, "left-over-shared-app-token")
    _use_store(monkeypatch, tmp_path)
    _gated_mode(monkeypatch)

    reason, cred = web_server._ws_auth_reason(
        _FakeWebSocket(headers={"X-Plobi-Session-Token": stale})
    )
    assert reason == "no_credential", f"retired token changed the WS outcome: {reason!r}"
    assert cred == "none"
    assert stale not in f"{reason}/{cred}"


def test_ws_auth_reason_refuses_the_session_token_as_a_header_credential(
    monkeypatch, tmp_path, _no_other_way_in
):
    """The desktop's ephemeral token is an HTTP credential, not a WS one.

    Pinned so the retirement did not quietly widen the header branch to "whatever this
    process also accepts on HTTP".
    """
    spa_token = secrets.token_urlsafe(32)
    monkeypatch.setattr(web_server, "_SESSION_TOKEN", spa_token)
    _use_store(monkeypatch, tmp_path)
    _gated_mode(monkeypatch)

    reason, cred = web_server._ws_auth_reason(
        _FakeWebSocket(headers={"X-Plobi-Session-Token": spa_token})
    )
    assert reason == "no_credential"
    assert cred == "none"


def test_ws_auth_reason_missing_credential_still_rejected(monkeypatch, _no_other_way_in):
    _gated_mode(monkeypatch)

    reason, cred = web_server._ws_auth_reason(_FakeWebSocket())
    assert reason == "no_credential"
    assert cred == "none"


def test_ws_auth_reason_does_not_accept_legacy_query_token_in_gated_mode(
    monkeypatch, _no_other_way_in
):
    """``?token=<SPA token>`` must NOT grant WS access in gated mode.

    The session token is still a real HTTP credential, but gated mode ignores the
    query param outright — a leaked ``_SESSION_TOKEN`` must not buy socket access.
    """
    spa_token = secrets.token_urlsafe(32)
    monkeypatch.setattr(web_server, "_SESSION_TOKEN", spa_token)
    _gated_mode(monkeypatch)

    reason, cred = web_server._ws_auth_reason(
        _FakeWebSocket(query_params={"token": spa_token})
    )
    assert reason == "no_credential"
    assert cred == "none"


# ---------------------------------------------------------------------------
# WP-FS-PROFILE-CWD — file API cwd follows the active profile's terminal.cwd
# (unrelated to credentials; kept in this file for history, see module docstring)
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
    from plobi_cli import web_server

    # Launch-profile side: set a misleading terminal.cwd via env that should
    # NOT be returned when the caller pins a profile_home.
    monkeypatch.setenv("TERMINAL_CWD", "/this/should/not/win")

    # Per-profile yaml points at tmp.
    profile_home = tmp_path / "plobi" / "profiles" / "l2-agenda"
    profile_home.mkdir(parents=True)
    _write_profile_yaml(profile_home, cwd_value=str(tmp_path))

    assert web_server._fs_default_cwd(profile_home=profile_home) == str(tmp_path)


def test_fs_default_cwd_falls_back_when_profile_yaml_missing_cwd(tmp_path, monkeypatch):
    """Profile yaml exists but has no ``terminal.cwd`` → fall back."""
    from plobi_cli import web_server

    monkeypatch.delenv("TERMINAL_CWD", raising=False)
    monkeypatch.chdir(tmp_path)
    profile_home = tmp_path / "plobi" / "profiles" / "l2-empty"
    profile_home.mkdir(parents=True)
    (profile_home / "config.yaml").write_text(
        "model: {}\n", encoding="utf-8"
    )

    assert web_server._fs_default_cwd(profile_home=profile_home) == str(tmp_path.resolve())


def test_fs_default_cwd_ignores_placeholder_values(tmp_path, monkeypatch):
    """``.`` / ``auto`` / ``cwd`` placeholders never pretend to be a directory."""
    from plobi_cli import web_server

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
    from plobi_cli import web_server

    monkeypatch.delenv("TERMINAL_CWD", raising=False)
    monkeypatch.chdir(tmp_path)

    profile_home = tmp_path / "l2-ghost"
    profile_home.mkdir(parents=True)
    _write_profile_yaml(profile_home, cwd_value=str(tmp_path / "nope" / "missing"))

    assert web_server._fs_default_cwd(profile_home=profile_home) == str(tmp_path.resolve())


def test_fs_default_cwd_no_profile_home_uses_launch_env(tmp_path, monkeypatch):
    """No profile_home → existing launch-profile / env / Path.cwd() chain."""
    from plobi_cli import web_server

    monkeypatch.delenv("TERMINAL_CWD", raising=False)
    monkeypatch.chdir(tmp_path)
    assert web_server._fs_default_cwd(profile_home=None) == str(tmp_path.resolve())

    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
    assert web_server._fs_default_cwd(profile_home=None) == str(tmp_path.resolve())


def test_fs_default_cwd_broken_profile_yaml_does_not_crash(tmp_path, monkeypatch):
    """Malformed yaml in the profile home must NOT break the file API."""
    from plobi_cli import web_server

    monkeypatch.delenv("TERMINAL_CWD", raising=False)
    monkeypatch.chdir(tmp_path)

    profile_home = tmp_path / "l2-broken"
    profile_home.mkdir(parents=True)
    (profile_home / "config.yaml").write_text("this: is: not: valid: yaml: ::",
                                              encoding="utf-8")
    # Falls through to Path.cwd() — no exception raised.
    assert web_server._fs_default_cwd(profile_home=profile_home) == str(tmp_path.resolve())
