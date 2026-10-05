"""Handset pairing on the token-auth seam, exercised in GATED mode (WP-APP-GATE).

The bug this suite pins down: the existing ``tests/plobi/test_app_token.py`` harness
mirrors the *legacy* loopback middleware (it calls
``web_server._has_valid_session_token`` directly). In a gated (non-loopback) bind
that function is never consulted — ``auth_required`` hands the decision to the
cookie gate — so a paired handset presenting a perfectly valid credential was
rejected 401 on every agenda call, and no test noticed because no test ran the
gated shape.

So every case here installs ``app.state.auth_required = True`` plus the real
``token_auth_middleware`` (outermost, as in production) over the real
``gated_auth_middleware``, and mounts the agenda router on both surfaces. The
pair of suites below is the point of the exercise: the handset's paths work by
device token, and the browser's paths still work by session cookie.

Since WP-APP-PAIR the credential is the device's *own* token (a paired handset
mints one by redeeming a pairing code) rather than the backend-wide ``app_token``,
which is what lets the desktop revoke a single lost device.
"""

from __future__ import annotations

from typing import Optional

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from plobi.agenda import router as agenda_router
from plobi.agenda import service as agenda_service
from plobi.agenda.service import AgendaService
from plobi_cli.dashboard_auth import devices as dev
from plobi_cli.dashboard_auth import registry
from plobi_cli.dashboard_auth.base import DashboardAuthProvider, LoginStart, Session
from plobi_cli.dashboard_auth.cookies import SESSION_AT_COOKIE
from plobi_cli.dashboard_auth.pairing import (
    HANDSET_API_PREFIX,
    HANDSET_PRINCIPAL,
    HANDSET_SCOPES,
    PROVIDER_NAME,
    install_pairing_auth,
)
from plobi_cli.dashboard_auth.scopes import enforce
from plobi_cli.dashboard_auth.token_auth import (
    SESSION_HEADER_NAME,
    clear_token_routes,
    is_token_route,
    token_auth_middleware,
)

BROWSER_AT = "browser-session-access-token"
BROWSER_PRINCIPAL = "user-1"
DAY_PARAMS = {"from": "2026-10-03T00:00:00", "to": "2026-10-04T23:59:59"}


class _BrowserSessionProvider(DashboardAuthProvider):
    """The human login that a gated bind is required to have — cookie-only."""

    name = "test-browser"
    display_name = "Test browser session"
    supports_token = False

    def start_login(self, *, redirect_uri: str) -> LoginStart:
        return LoginStart(redirect_url=redirect_uri, cookie_payload={})

    def complete_login(self, *, code, state, code_verifier, redirect_uri) -> Session:
        return Session(BROWSER_PRINCIPAL, "", "", "", self.name, 0, BROWSER_AT, "rt")

    def verify_session(self, *, access_token: str) -> Optional[Session]:
        if access_token != BROWSER_AT:
            return None
        return Session(
            BROWSER_PRINCIPAL, "u@example.test", "u", "org", self.name,
            4_102_444_800,  # year 2100: this stand-in never expires
            BROWSER_AT, "rt",
        )

    def refresh_session(self, *, refresh_token: str) -> Session:
        return Session(BROWSER_PRINCIPAL, "", "", "", self.name, 0, BROWSER_AT, "rt")

    def revoke_session(self, *, refresh_token: str) -> None:
        return None


@pytest.fixture
def gated_client(tmp_path, monkeypatch):
    """A gated backend (as a handset would reach it) with one paired device.

    Mirrors ``web_server.py``'s real stack: Starlette makes the middleware
    registered LAST the outermost, so production order (cookie gate → legacy
    session gate → token seam) is reproduced by putting the real
    ``gated_auth_middleware`` in first and ``token_auth_middleware`` last. The
    device gets its token the way a real handset does — open a window, redeem it.
    """
    clear_token_routes()
    registry.clear_providers()

    monkeypatch.setattr(
        agenda_service, "_DEFAULT", AgendaService(tmp_path / "agenda.db")
    )
    monkeypatch.setattr(dev, "_DEFAULT", dev.DeviceStore(tmp_path))

    app = FastAPI()
    app.state.auth_required = True  # non-loopback bind: cookie gate is authoritative

    @app.middleware("http")
    async def _cookie_gate(request, call_next):
        # The real gate, not a stub: it is the component that was rejecting the
        # handset, and the one that honours ``token_authenticated``.
        from plobi_cli.dashboard_auth.middleware import gated_auth_middleware

        return await gated_auth_middleware(request, call_next)

    app.middleware("http")(token_auth_middleware)

    app.include_router(agenda_router.router, prefix="/api/agenda")
    app.include_router(
        agenda_router.router,
        prefix=f"{HANDSET_API_PREFIX}/agenda",
        dependencies=[Depends(enforce)],
    )

    registry.register_provider(_BrowserSessionProvider())
    assert install_pairing_auth() is True

    # One pairing, in-process: the same exchange the App performs over HTTP.
    store = dev.get_store()
    code, _expires_at = store.issue_code()
    device_id, token = store.redeem(code, name="test handset")

    with TestClient(app) as client:
        yield client, token, device_id

    clear_token_routes()
    registry.clear_providers()


def _as_browser(client):
    client.cookies.set(SESSION_AT_COOKIE, BROWSER_AT)


# ---------------------------------------------------------------- device reads


@pytest.mark.parametrize(
    "path",
    [f"{HANDSET_API_PREFIX}/agenda", f"{HANDSET_API_PREFIX}/agenda/pending"],
)
def test_handset_reads_accept_bearer_pairing_token(gated_client, path):
    """`Authorization: Bearer <pairing token>` passes the seam, then the gate."""
    client, token, _device_id = gated_client
    resp = client.get(path, params=DAY_PARAMS, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text


def test_handset_read_accepts_dedicated_session_header(gated_client):
    """The header the desktop shell uses is accepted as a second token source.

    Kept because a reverse proxy may own ``Authorization`` — the reason the
    dedicated header exists at all.
    """
    client, token, _device_id = gated_client
    resp = client.get(
        f"{HANDSET_API_PREFIX}/agenda/day", headers={SESSION_HEADER_NAME: token}
    )
    assert resp.status_code == 200, resp.text


def test_handset_rejects_wrong_and_missing_pairing_token(gated_client):
    client, _token, _device_id = gated_client
    path = f"{HANDSET_API_PREFIX}/agenda/pending"
    bad = client.get(path, headers={SESSION_HEADER_NAME: "not-the-token"})
    assert bad.status_code == 401
    assert client.get(path).status_code == 401
    # An unpaired browser session is not a device credential either.
    _as_browser(client)
    assert client.get(path).status_code == 401


def test_handset_confirm_and_dismiss_reach_the_handler(gated_client):
    """Parameterised routes are token-authable, and a bad id is the handler's
    problem (404), not the gate's (401) — that difference is the assertion."""
    client, token, _device_id = gated_client
    headers = {"Authorization": f"Bearer {token}"}
    for verb in ("confirm", "dismiss"):
        resp = client.post(f"{HANDSET_API_PREFIX}/agenda/no-such-event/{verb}", headers=headers)
        assert resp.status_code != 401, (verb, resp.status_code, resp.text)


# ------------------------------------------------------------- authority edges


def test_pairing_token_cannot_write_the_board(gated_client):
    """Read + confirm is the whole grant. POST/PATCH/DELETE on the handset prefix
    are real routes (same router) but not token routes, so the seam hands them to
    the cookie gate and a device credential is refused."""
    client, token, _device_id = gated_client
    headers = {"Authorization": f"Bearer {token}"}
    created = client.post(
        f"{HANDSET_API_PREFIX}/agenda",
        json={"title": "from the phone", "start_at": "2026-10-05T19:00:00"},
        headers=headers,
    )
    assert created.status_code == 401
    assert client.patch(f"{HANDSET_API_PREFIX}/agenda/evt_x", json={"title": "y"}, headers=headers).status_code == 401
    assert client.delete(f"{HANDSET_API_PREFIX}/agenda/evt_x", headers=headers).status_code == 401


def test_registry_narrows_by_method(gated_client):
    """A path is not one right: ``/api/handset/agenda`` is a read under GET and a
    write under POST, and only the read is token-authable."""
    _client, _token, _device_id = gated_client
    path = f"{HANDSET_API_PREFIX}/agenda"
    assert is_token_route(path, "GET") is True
    assert is_token_route(path, "POST") is False
    assert is_token_route(f"{path}/abc123") is False
    assert is_token_route(f"{path}/abc123/confirm", "POST") is True
    # A pattern can never swallow a longer path by accident.
    assert is_token_route(f"{path}/abc123/confirm/extra") is False


def test_browser_paths_are_untouched_by_the_device_surface(gated_client):
    """The regression this whole change could have caused: the dashboard SPA calls
    ``/api/agenda`` with a session cookie, and a registered token route would have
    demanded a bearer token there and locked the board out. Both directions are
    asserted — the browser keeps working, and the pairing token gets it nowhere."""
    client, token, _device_id = gated_client

    _as_browser(client)
    listed = client.get("/api/agenda", params=DAY_PARAMS)
    assert listed.status_code == 200, listed.text
    assert client.get("/api/agenda/pending").status_code == 200
    assert client.get("/api/agenda/day").status_code == 200
    assert client.post("/api/agenda/evt_x/confirm").status_code == 404  # handler's problem

    client.cookies.clear()
    assert client.get("/api/agenda", headers={"Authorization": f"Bearer {token}"}).status_code == 401


# ------------------------------------------------------------------ fail closed


def test_an_unpaired_backend_has_no_device_credential(tmp_path, monkeypatch):
    """Arming is not authorising — with no paired device nothing verifies.

    The surface stays registered (that is the state a device pairs *into*, and
    the seam answers 401 rather than falling through to the cookie gate), but the
    only credential it recognises is a token this store minted. Nothing
    backend-wide can stand in for it, which is the point: revoking one handset
    must not require rotating a secret every device shares.
    """
    clear_token_routes()
    registry.clear_providers()
    monkeypatch.setattr(dev, "_DEFAULT", dev.DeviceStore(tmp_path))

    assert install_pairing_auth() is True
    assert is_token_route(f"{HANDSET_API_PREFIX}/agenda", "GET") is True
    provider = registry.get_provider(PROVIDER_NAME)
    assert provider is not None
    assert provider.verify_token(token="some-shared-app-token") is None
    assert provider.verify_token(token="钥匙") is None
    assert provider.verify_token(token="") is None

    clear_token_routes()
    registry.clear_providers()


def test_principal_names_the_device_and_carries_its_scopes(gated_client):
    """The verified caller is a *which device*, so guards and audits can narrow it."""
    _client, token, device_id = gated_client
    provider = registry.get_provider(PROVIDER_NAME)
    assert provider is not None
    principal = provider.verify_token(token=token)
    assert principal is not None
    assert principal.provider == PROVIDER_NAME
    assert principal.principal == f"{HANDSET_PRINCIPAL}:{device_id}"
    assert tuple(principal.scopes) == tuple(HANDSET_SCOPES)
    # Non-ASCII must not blow up the constant-time compare (401, not 500).
    assert provider.verify_token(token="钥匙") is None
    # It is not a cookie-session provider: it stays out of the login chooser.
    assert provider.verify_session(access_token=token) is None
    assert provider.name not in [p.name for p in registry.list_session_providers()]
