"""Handset pairing token — the in-tree consumer of the token-auth seam (WP-APP-GATE).

The HarmonyOS handset pairs once with the desktop backend and keeps a long-lived
token at ``$PLOBI_HOME/plobi/app_token``. On a non-loopback bind the cookie gate is
authoritative and never consults that token (``web_server.py`` passes gated
requests straight to ``gated_auth_middleware``, and
``auth_middleware`` short-circuits on ``auth_required``), so every handset call to
the agenda API was rejected with 401 no matter which header it presented.

This provider puts the pairing token on the generic bearer seam and registers
exactly the handset-facing routes. It **fails closed**: when this backend has no
pairing token, nothing is registered and those routes keep requiring a browser
session.

Why the device has its own ``/api/handset/*`` namespace instead of reusing
``/api/agenda``: a token route is *owned* by the seam — for a registered path, a
bearer token is the only accepted credential, and the browser session is turned
away (that is the drain route's documented behaviour and a tested part of the
seam contract). The agenda board is driven by both the dashboard SPA and the
handset, so the two surfaces are separated by path instead of one of them being
locked out. Handlers are shared verbatim (same router, second mount); only the
auth scheme differs.

Scopes are attached but not yet enforced by the handlers — they exist so the
handset's authority can be narrowed later without touching the seam.
"""

from __future__ import annotations

import hmac
import logging
from typing import Optional

from plobi_cli.dashboard_auth.base import (
    DashboardAuthProvider,
    LoginStart,
    Session,
    TokenPrincipal,
)
from plobi_cli.dashboard_auth.token_auth import (
    register_token_route,
    register_token_route_pattern,
)

_log = logging.getLogger(__name__)

PROVIDER_NAME = "pairing"
HANDSET_PRINCIPAL = "handset"
HANDSET_SCOPES = ("agenda:read", "agenda:confirm", "task:read")

# Device-facing prefix. The agenda handlers are mounted here as well as under
# ``/api/agenda`` (browser session); this is the half the pairing token opens.
HANDSET_API_PREFIX = "/api/handset"

# Reads today's list, tomorrow's list, the pending queue, the assembled day, and
# the task/event stream (``plobi/tasks`` — Docs/specs/task-events.md).
_TOKEN_ROUTES = (
    f"{HANDSET_API_PREFIX}/agenda",
    f"{HANDSET_API_PREFIX}/agenda/pending",
    f"{HANDSET_API_PREFIX}/agenda/day",
    f"{HANDSET_API_PREFIX}/tasks",
    f"{HANDSET_API_PREFIX}/events",
)

# Approve or discard one pending item — POST only. Everything else under the
# agenda API (create, PATCH, DELETE) deliberately stays off the token path: the
# handset does not need write access to the board to show and confirm a day.
_TOKEN_ROUTE_PATTERNS_POST = (
    rf"{HANDSET_API_PREFIX}/agenda/[^/]+/confirm",
    rf"{HANDSET_API_PREFIX}/agenda/[^/]+/dismiss",
)

# One task's replayable event history — a read that happens to carry a path
# parameter, so it cannot be an exact route.
_TOKEN_ROUTE_PATTERNS_GET = (
    rf"{HANDSET_API_PREFIX}/tasks/[^/]+/events",
)


def pairing_token() -> Optional[str]:
    """The persisted handset token for this backend, or ``None`` if there is none.

    Read through :func:`plobi_cli.web_server._get_app_token` so a test that swaps
    the module-level token with a monkeypatched attribute is still honoured, and so
    a failed mint (``None``) never becomes an empty-string credential.
    """
    from plobi_cli import web_server

    token = (web_server._get_app_token() or "").strip()
    return token or None


class HandsetPairingProvider(DashboardAuthProvider):
    """Non-interactive provider that recognises the handset's pairing token."""

    name = PROVIDER_NAME
    display_name = "Plobi handset (pairing token)"
    supports_token = True
    # Not an interactive login: excluded from the /login chooser and from the
    # gate's cookie-verify loop, exactly like the drain service credential.
    supports_session = False

    def verify_token(self, *, token: str) -> Optional[TokenPrincipal]:
        expected = pairing_token()
        if not expected or not token:
            return None
        # Bytes, not str: compare_digest on str raises TypeError for non-ASCII,
        # which a hostile header would turn into a 500 instead of a 401.
        if not hmac.compare_digest(
            token.encode("utf-8"), expected.encode("utf-8")
        ):
            return None
        return TokenPrincipal(
            principal=HANDSET_PRINCIPAL,
            provider=self.name,
            scopes=HANDSET_SCOPES,
        )

    # ---- interactive methods: unsupported (a paired device is not a human login) --

    def start_login(self, *, redirect_uri: str) -> LoginStart:
        raise NotImplementedError(
            "HandsetPairingProvider is a paired-device credential; there is no "
            "login flow."
        )

    def complete_login(
        self, *, code: str, state: str, code_verifier: str, redirect_uri: str
    ) -> Session:
        raise NotImplementedError("HandsetPairingProvider is a paired-device credential.")

    def verify_session(self, *, access_token: str) -> Optional[Session]:
        # It never mints a Session, so it can never recognise a session cookie.
        # Return None (don't raise) to stack harmlessly in the cookie-verify loop.
        return None

    def refresh_session(self, *, refresh_token: str) -> Session:
        raise NotImplementedError("HandsetPairingProvider is a paired-device credential.")

    def revoke_session(self, *, refresh_token: str) -> None:
        # Best-effort by contract: pairing tokens live in $PLOBI_HOME and are
        # rotated by re-pairing on the desktop, not by this provider.
        return None


def install_pairing_auth() -> bool:
    """Register the provider and the handset routes. Returns whether it armed.

    Fails closed: no pairing token on this backend means no route is opened and no
    provider is registered, so the handset API keeps demanding a browser session.
    Idempotent — ``start_server`` may run more than once in a process, and routes
    are re-declared each call so a caller that only cleared the route registry
    still ends up with an armed surface.
    """
    from plobi_cli.dashboard_auth import registry

    if not pairing_token():
        _log.info(
            "dashboard-auth: pairing provider not armed (no app token under "
            "$PLOBI_HOME/plobi/app_token); handset-facing routes stay session-only"
        )
        return False

    if registry.get_provider(PROVIDER_NAME) is None:
        registry.register_provider(HandsetPairingProvider())
    for path in _TOKEN_ROUTES:
        register_token_route(path, methods=("GET",))
    for pattern in _TOKEN_ROUTE_PATTERNS_POST:
        register_token_route_pattern(pattern, methods=("POST",))
    for pattern in _TOKEN_ROUTE_PATTERNS_GET:
        register_token_route_pattern(pattern, methods=("GET",))

    _log.info(
        "dashboard-auth: pairing provider armed — %d exact route(s), %d pattern(s) "
        "accept the handset pairing token",
        len(_TOKEN_ROUTES),
        len(_TOKEN_ROUTE_PATTERNS_POST) + len(_TOKEN_ROUTE_PATTERNS_GET),
    )
    return True
