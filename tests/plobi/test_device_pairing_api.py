"""Handset pairing over HTTP, in GATED mode (WP-APP-PAIR · 裁定 64).

The shape this suite pins down is the one a lost tablet depends on: the desktop
opens a short window, the device trades its way in, and from then on it carries a
token that belongs to *it* — so "revoke the handset in the pocket" ends one device
and leaves the rest paired.

Everything runs the real gate stack (cookie gate under the token seam, as
``web_server.py`` installs them) rather than a stub, because the previous design
failed exactly where stubs can't see: a shared App token that the cookie gate
never consulted.
"""

from __future__ import annotations

import time
from typing import Optional

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from plobi.agenda import router as agenda_router
from plobi.agenda import service as agenda_service
from plobi.agenda.service import AgendaService
from plobi_cli import web_server
from plobi_cli.dashboard_auth import devices as dev
from plobi_cli.dashboard_auth import pairing_api, registry
from plobi_cli.dashboard_auth.base import DashboardAuthProvider, LoginStart, Session
from plobi_cli.dashboard_auth.cookies import SESSION_AT_COOKIE
from plobi_cli.dashboard_auth.pairing import (
    HANDSET_API_PREFIX,
    HANDSET_SCOPES,
    install_pairing_auth,
)
from plobi_cli.dashboard_auth.public_paths import PUBLIC_API_PATHS
from plobi_cli.dashboard_auth.scopes import enforce
from plobi_cli.dashboard_auth.token_auth import (
    SESSION_HEADER_NAME,
    clear_token_routes,
    token_auth_middleware,
)

BROWSER_AT = "browser-session-access-token"
DAY_PARAMS = {"from": "2026-10-03T00:00:00", "to": "2026-10-04T23:59:59"}
REDEEM = pairing_api.REDEEM_PATH
DEVICE_START = f"{pairing_api.DESKTOP_PAIRING_PREFIX}/device/start"
DEVICES = f"{pairing_api.DESKTOP_PAIRING_PREFIX}/devices"
DEVICE_REVOKE = f"{pairing_api.DESKTOP_PAIRING_PREFIX}/device/revoke"


class _BrowserSessionProvider(DashboardAuthProvider):
    """The human login a gated bind is required to have — cookie-only."""

    name = "test-browser"
    display_name = "Test browser session"
    supports_token = False

    def start_login(self, *, redirect_uri: str) -> LoginStart:
        return LoginStart(redirect_url=redirect_uri, cookie_payload={})

    def complete_login(self, *, code, state, code_verifier, redirect_uri) -> Session:
        return Session("user-1", "", "", "", self.name, 0, BROWSER_AT, "rt")

    def verify_session(self, *, access_token: str) -> Optional[Session]:
        if access_token != BROWSER_AT:
            return None
        return Session(
            "user-1", "u@example.test", "u", "org", self.name,
            4_102_444_800,  # year 2100: this stand-in never expires
            BROWSER_AT, "rt",
        )

    def refresh_session(self, *, refresh_token: str) -> Session:
        return Session("user-1", "", "", "", self.name, 0, BROWSER_AT, "rt")

    def revoke_session(self, *, refresh_token: str) -> None:
        return None


class _FakeWebSocket:
    """Minimal Starlette WebSocket stand-in for ``_ws_auth_reason``."""

    def __init__(self, *, headers: dict[str, str]):
        self.headers = {k.lower(): v for k, v in headers.items()}
        self.query_params: dict[str, str] = {}
        self.client = type("Client", (), {"host": "192.168.1.42"})()
        self.url = type("URL", (), {"path": "/api/ws"})()


@pytest.fixture
def gated(tmp_path, monkeypatch):
    """A gated backend with the production mounts, and an empty device store."""
    clear_token_routes()
    registry.clear_providers()
    store = dev.DeviceStore(tmp_path)
    # monkeypatch restores ``_DEFAULT`` afterwards, so no test inherits this store
    # and nothing here can reach the real ``~/.plobi``.
    monkeypatch.setattr(dev, "_DEFAULT", store)
    monkeypatch.setattr(
        agenda_service, "_DEFAULT", AgendaService(tmp_path / "agenda.db")
    )

    app = FastAPI()
    app.state.auth_required = True

    @app.middleware("http")
    async def _cookie_gate(request, call_next):
        from plobi_cli.dashboard_auth.middleware import gated_auth_middleware

        return await gated_auth_middleware(request, call_next)

    app.middleware("http")(token_auth_middleware)  # registered last ⇒ outermost

    app.include_router(agenda_router.router, prefix="/api/agenda")
    app.include_router(
        agenda_router.router,
        prefix=f"{HANDSET_API_PREFIX}/agenda",
        dependencies=[Depends(enforce)],
    )
    app.include_router(pairing_api.handset_router, prefix=pairing_api.HANDSET_PAIR_PREFIX)
    app.include_router(pairing_api.desktop_router, prefix=pairing_api.DESKTOP_PAIRING_PREFIX)

    registry.register_provider(_BrowserSessionProvider())
    install_pairing_auth()

    with TestClient(app) as client:
        yield client, store

    clear_token_routes()
    registry.clear_providers()


def _as_browser(client):
    client.cookies.set(SESSION_AT_COOKIE, BROWSER_AT)


def _open_window(client) -> str:
    """The operator's half: ask the desktop for a code, then leave the session."""
    _as_browser(client)
    started = client.post(DEVICE_START)
    assert started.status_code == 200, started.text
    code = started.json()["code"]
    client.cookies.clear()
    return code


def _pair(client, name="客厅平板") -> dict:
    """The whole handshake, both halves, as they actually happen."""
    code = _open_window(client)
    redeemed = client.post(REDEEM, json={"code": code, "deviceName": name})
    assert redeemed.status_code == 200, redeemed.text
    return redeemed.json()


def _bearer(token) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _not(code: str) -> str:
    """A guess known to differ from ``code`` — which may be the live one."""
    return "000000" if code != "000000" else "111111"


# ------------------------------------------------------------- the handshake


def test_desktop_opens_one_timed_window(gated):
    client, _store = gated
    _as_browser(client)
    started = client.post(DEVICE_START)
    assert started.status_code == 200, started.text
    body = started.json()
    assert len(body["code"]) == dev.CODE_DIGITS and body["code"].isdigit()
    assert body["ttlSeconds"] == dev.CODE_TTL_SECONDS
    assert body["expiresAt"] == pytest.approx(time.time() + dev.CODE_TTL_SECONDS, abs=2)
    assert client.get(DEVICES).json()["codeExpiresAt"] > time.time()


def test_redeem_needs_no_session_and_returns_the_token_once(gated):
    code = _open_window(gated[0])
    client, _store = gated

    resp = client.post(REDEEM, json={"code": code, "deviceName": "客厅平板"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token"] and body["deviceId"].startswith("dev-")
    assert tuple(body["scopes"]) == tuple(HANDSET_SCOPES)
    # One use: the same code is worth nothing a second time.
    assert client.post(REDEEM, json={"code": code}).status_code == 401


def test_only_redeem_is_credential_free(gated):
    """Opening / listing / revoking a window is the desktop's, never a stranger's."""
    client, _store = gated
    assert client.post(DEVICE_START).status_code == 401
    assert client.get(DEVICES).status_code == 401
    assert client.post(DEVICE_REVOKE, json={"deviceId": "dev-x"}).status_code == 401
    # …while the device's call reaches its handler instead of the gate.
    assert "pairing code" in client.post(
        REDEEM, json={"code": "000000"}
    ).json()["detail"]


def test_desktop_lists_the_device_it_paired(gated):
    client, _store = gated
    _pair(client)
    _as_browser(client)
    listed = client.get(DEVICES).json()
    assert [d["name"] for d in listed["devices"]] == ["客厅平板"]
    assert listed["devices"][0]["pairedAt"] <= time.time()
    assert listed["codeExpiresAt"] == 0.0  # redemption closed the window


# ---------------------------------------------------------------- authority


def test_a_paired_token_reads_the_device_surface(gated):
    client, _store = gated
    token = _pair(client)["token"]
    assert client.get(
        f"{HANDSET_API_PREFIX}/agenda/day", headers=_bearer(token)
    ).status_code == 200
    assert client.get(
        f"{HANDSET_API_PREFIX}/agenda/pending", headers={SESSION_HEADER_NAME: token}
    ).status_code == 200
    # …and never the browser's surface, where a session is the credential.
    assert client.get(
        "/api/agenda", params=DAY_PARAMS, headers=_bearer(token)
    ).status_code == 401


def test_a_pairing_code_is_not_a_credential(gated):
    """The code is an *input* to one route. It authenticates nothing, anywhere."""
    code = _open_window(gated[0])
    client, _store = gated
    assert client.get(
        f"{HANDSET_API_PREFIX}/agenda/day", headers=_bearer(code)
    ).status_code == 401
    assert client.get(DEVICES, headers=_bearer(code)).status_code == 401
    # And it is still spendable afterwards — these refusals did not consume it.
    assert client.post(REDEEM, json={"code": code}).status_code == 200


def test_a_browser_session_stays_off_the_device_surface(gated):
    """The seam owns the GETs; ``scopes.enforce`` owns the rest of the namespace."""
    client, _store = gated
    _as_browser(client)
    assert client.get(f"{HANDSET_API_PREFIX}/agenda/day").status_code == 401
    # POST on the handset prefix is not a registered token route, so the cookie
    # gate lets the browser through and the scope guard is what refuses it — a
    # device-only namespace means exactly that, even for a logged-in dashboard.
    refused = client.post(
        f"{HANDSET_API_PREFIX}/agenda",
        json={"title": "from the browser", "start_at": "2026-10-05T19:00:00"},
    )
    assert refused.status_code == 401
    assert "paired device token required" in refused.json()["detail"]


def test_the_retired_shared_app_token_does_not_open_the_device_surface(
    gated, tmp_path
):
    """The credential a handset from before pairing still might be carrying is inert.

    Written at the *old* path and read back rather than invented, because a leftover
    ``<home>/plobi/app_token`` is the normal state after an upgrade. On the handset
    namespace it must not read as a paired device: revoking one device would otherwise
    mean rotating a secret every device shares — and since 裁定 68 that secret does not
    exist anywhere, so the file opens nothing at all.
    """
    client, _store = gated
    legacy_dir = tmp_path / "plobi"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    legacy = legacy_dir / "app_token"
    legacy.write_text("shared-app-token-value", encoding="utf-8")

    refused = client.get(
        f"{HANDSET_API_PREFIX}/agenda/day", headers=_bearer(legacy.read_text())
    )
    assert refused.status_code == 401


# ---------------------------------------------------------------- the budget


def test_five_bad_codes_lock_the_door_then_the_desktop_too(gated):
    code = _open_window(gated[0])
    client, _store = gated
    bad = _not(code)

    for _ in range(dev.MAX_FAILED_REDEEM - 1):
        assert client.post(REDEEM, json={"code": bad}).status_code == 401
    locked = client.post(REDEEM, json={"code": bad})
    assert locked.status_code == 429, locked.text
    assert int(locked.headers["retry-after"]) > 0

    # A locked store refuses to open a window as well — the desktop gets the same
    # answer the device did instead of a code nobody can spend.
    _as_browser(client)
    assert client.post(DEVICE_START).status_code == 429


def test_a_stale_window_is_not_a_guess(gated):
    """Redeeming after the code lapsed is 'too late', not 'one wrong guess'."""
    client, store = gated
    code = _open_window(client)
    active, failed = store._codes()
    active["expires_at"] = time.time() - 1
    store._save_codes(active, failed)

    assert client.post(REDEEM, json={"code": code}).status_code == 401
    assert store._failed_attempts()[0] == 0
    _as_browser(client)
    assert client.post(DEVICE_START).status_code == 200


# ------------------------------------------------------------------- revoke


def test_revoking_ends_one_device_and_only_that_one(gated):
    client, _store = gated
    first = _pair(client, name="phone")
    second = _pair(client, name="tablet")
    _as_browser(client)

    assert [d["name"] for d in client.get(DEVICES).json()["devices"]] == ["phone", "tablet"]
    assert client.post(
        DEVICE_REVOKE, json={"deviceId": first["deviceId"]}
    ).status_code == 200
    assert client.post(
        DEVICE_REVOKE, json={"deviceId": first["deviceId"]}
    ).status_code == 404

    client.cookies.clear()
    assert client.get(
        f"{HANDSET_API_PREFIX}/agenda/day", headers=_bearer(first["token"])
    ).status_code == 401
    assert client.get(
        f"{HANDSET_API_PREFIX}/agenda/day", headers=_bearer(second["token"])
    ).status_code == 200


# ------------------------------------------------------------------------ ws


def test_ws_upgrade_accepts_a_paired_device_token(gated, monkeypatch):
    """A handset attaches the stream with the same token it reads with."""
    client, _store = gated
    minted = _pair(client)
    monkeypatch.setattr(web_server, "app", client.app)

    accepted = web_server._ws_auth_reason(
        _FakeWebSocket(headers={"X-Plobi-Session-Token": minted["token"]})
    )
    assert accepted == (None, "device_token")

    _as_browser(client)
    assert client.post(
        DEVICE_REVOKE, json={"deviceId": minted["deviceId"]}
    ).status_code == 200
    reason, _cred = web_server._ws_auth_reason(_FakeWebSocket(headers=_bearer(minted["token"])))
    assert reason is not None, "a revoked device must not attach again"


# ------------------------------------------------------------------- wiring


def test_the_real_app_mounts_the_pairing_surface():
    """The constants and the production mounts must not drift apart.

    A mismatch between ``PUBLIC_API_PATHS`` and the mount is not a 404: it is
    either a route nobody can reach, or a route that is public while its handler
    believes it is gated. Both halves are checked against the app
    ``web_server`` actually builds.
    """
    assert REDEEM in PUBLIC_API_PATHS
    mounted = {
        (route.path, method)
        for route in web_server.app.routes
        for method in (getattr(route, "methods", None) or ())
    }
    assert {
        (REDEEM, "POST"),
        (DEVICES, "GET"),
        (DEVICE_START, "POST"),
        (DEVICE_REVOKE, "POST"),
    } <= mounted
