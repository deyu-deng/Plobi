"""``plobi pairing device`` — the CLI half of handset pairing, proven on the live seam.

``WP-PAIR-UI`` shipped the desktop UI (open a code, list devices, revoke one) but left
the command line unbuilt. Three criteria, and this file is one test per criterion:

1. one command opens a pairing code **and** redeems it into a device token that the
   existing token-auth seam accepts — not a parallel credential path;
2. the listing says *which* device, when it paired, when it was last seen, and whether
   this home can be written at all (a pairing store nobody can write is the failure
   mode 裁定 68 turned into a bind-time downgrade, so an operator must be able to see it);
3. after ``--revoke``, the device's **next request** is refused with a real status code —
   the UI hiding a row is not evidence, a 401 is.

The store is the shared one: the CLI and the middleware both go through
``devices.get_store()``, which re-reads ``<PLOBI_HOME>/plobi/devices.json`` per call.
That is what makes criterion 3 mean something — a cache here would let a revoke look
harmless while the running backend kept trusting the token.
"""

from __future__ import annotations

import argparse
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
from plobi_cli.dashboard_auth.pairing import HANDSET_API_PREFIX, install_pairing_auth
from plobi_cli.dashboard_auth.scopes import enforce
from plobi_cli.dashboard_auth.token_auth import (
    SESSION_HEADER_NAME,
    clear_token_routes,
    token_auth_middleware,
)
from plobi_cli.subcommands.pairing_device import cmd_pairing_device

BROWSER_AT = "browser-session-access-token"
BROWSER_PRINCIPAL = "user-1"


class _BrowserSessionProvider(DashboardAuthProvider):
    """A gated bind must have a human login registered; this is the stand-in."""

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
            4_102_444_800, BROWSER_AT, "rt",
        )

    def refresh_session(self, *, refresh_token: str) -> Session:
        return Session(BROWSER_PRINCIPAL, "", "", "", self.name, 0, BROWSER_AT, "rt")

    def revoke_session(self, *, refresh_token: str) -> None:
        return None


@pytest.fixture
def gated_app(tmp_path, monkeypatch):
    """The real middleware stack a handset would reach, with an empty agenda."""
    clear_token_routes()
    registry.clear_providers()
    monkeypatch.setattr(agenda_service, "_DEFAULT", AgendaService(tmp_path / "agenda.db"))
    monkeypatch.setattr(dev, "_DEFAULT", dev.DeviceStore(tmp_path))

    app = FastAPI()
    app.state.auth_required = True

    @app.middleware("http")
    async def _cookie_gate(request, call_next):
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
    try:
        yield app
    finally:
        clear_token_routes()
        registry.clear_providers()


def _run_cli(**kwargs) -> tuple[int, str]:
    """Invoke the CLI handler the way argparse does, capturing what it printed."""
    ns = argparse.Namespace(name="", list_devices=False, revoke_device="")
    for key, value in kwargs.items():
        setattr(ns, key, value)
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cmd_pairing_device(ns)
    return rc, buf.getvalue()


def _line_value(out: str, key: str) -> str:
    """Pull one aligned "  key  : value" line out of what the CLI printed."""
    for ln in out.splitlines():
        head = ln.split(":", 1)[0].strip()
        if head == key:
            return ln.split(":", 1)[1].strip()
    raise AssertionError("no " + repr(key) + " line in CLI output:" + chr(10) + out)


def _device_id_of(out: str) -> str:
    return _line_value(out, "paired a device")


def _get_as_device(client, token: str):
    return client.get(
        f"{HANDSET_API_PREFIX}/agenda",
        params={"from": "2026-10-03T00:00:00", "to": "2026-10-04T23:59:59"},
        headers={SESSION_HEADER_NAME: token},
    )


# 判据 ① — 一条命令：开码 + 换成一台设备、且这张令牌走的正是现成的那道门
def test_cli_pairs_a_device_the_seam_accepts(gated_app):
    rc, out = _run_cli(name="office tablet")
    assert rc == 0, out
    device_id = _device_id_of(out)
    token = _line_value(out, "token")
    assert device_id.startswith("dev-") and len(token) >= 32

    with TestClient(gated_app) as client:
        assert _get_as_device(client, token).status_code == 200


def test_cli_refuses_a_name_the_http_surface_would_reject(gated_app):
    """Same 64-character ceiling as ``RedeemBody`` — the two paths must not disagree."""
    rc, out = _run_cli(name="x" * 65)
    assert rc == 2 and "at most 64" in out
    assert dev.get_store().list_devices() == []


# 判据 ② — 列表给得出：哪台设备、什么时候配的、当前能不能写
def test_cli_lists_device_with_times_and_write_state(gated_app):
    rc, out = _run_cli(name="living room")
    device_id = _device_id_of(out)

    rc, listing = _run_cli(list_devices=True)
    assert rc == 0
    assert device_id in listing
    assert "living room" in listing
    assert "paired 20" in listing and "last seen" in listing
    assert "pairing store writable: yes" in listing
    assert "devices paired: 1" in listing


def test_listing_says_so_when_the_store_cannot_be_written(gated_app, tmp_path, monkeypatch):
    """The writable check is the same predicate the LAN bind gate uses; a read-only
    home must be visible here rather than discovered later as a downgraded bind."""
    _rc, out = _run_cli(name="one")
    device_id = _device_id_of(out)

    store_dir = tmp_path / "plobi"
    monkeypatch.setattr(dev.DeviceStore, "root_writable", lambda self: False)
    rc, listing = _run_cli(list_devices=True)
    assert rc == 0 and device_id in listing
    assert "pairing store writable: NO" in listing


# 判据 ③ — 撤销之后，那台设备的下一次请求当场被拒（拿到状态码，不是界面上看不见）
def test_revoke_is_refused_on_the_next_request(gated_app):
    rc, out = _run_cli(name="to be revoked")
    device_id = _device_id_of(out)
    token = _line_value(out, "token")

    with TestClient(gated_app) as client:
        assert _get_as_device(client, token).status_code == 200

        rc, out = _run_cli(revoke_device=device_id)
        assert rc == 0 and "stops verifying" in out

        refused = _get_as_device(client, token)
        assert refused.status_code == 401, refused.text
        # Pinned as observed, not as guessed: the seam answers with the
        # unauthenticated envelope. Which sub-reason it names is the gate's business —
        # the contract a handset relies on is "401, and my other devices still work".
        body = refused.json()
        assert body.get("error") == "unauthenticated", body

        # Revoking one device must not dent another: that is the whole point of
        # per-device tokens over the retired shared app_token (裁定 68).
        rc2, out2 = _run_cli(name="still fine")
        other = _line_value(out2, "token")
        assert _get_as_device(client, other).status_code == 200


def test_revoking_an_unknown_device_says_so_and_changes_nothing(gated_app):
    rc, out = _run_cli(name="real one")
    device_id = _device_id_of(out)

    rc, out = _run_cli(revoke_device="dev-not-here")
    assert rc == 1 and "nothing was revoked" in out
    assert [d.device_id for d in dev.get_store().list_devices()] == [device_id]
