"""Handset pairing — the in-tree consumer of the token-auth seam (WP-APP-GATE / WP-APP-PAIR).

A handset pairs once with the desktop backend by typing a short numeric code, and
keeps its **own** long-lived device token after that (``dashboard_auth.devices``).
On a non-loopback bind the cookie gate is authoritative and never consults device
credentials (``web_server.py`` passes gated requests straight to
``gated_auth_middleware``, and ``auth_middleware`` short-circuits on
``auth_required``), so the seam below is the only thing a device can knock on.

It **fails closed**: with no paired device and no open pairing code, every route
registered here demands a credential that nothing can present, so the handset API
returns 401 — it never falls through to "browser session accepted".

Why the device has its own ``/api/handset/*`` namespace instead of reusing
``/api/agenda``: a token route is *owned* by the seam — for a registered path, a
bearer token is the only accepted credential, and the browser session is turned
away (that is the drain route's documented behaviour and a tested part of the
seam contract). The agenda board is driven by both the dashboard SPA and the
handset, so the two surfaces are separated by path instead of one of them being
locked out. Handlers are shared verbatim (same router, second mount); only the
auth scheme differs.

Scopes are enforced by :mod:`plobi_cli.dashboard_auth.scopes`, which is what keeps
a pairing code from reading the agenda while it is supposed to only redeem itself.
"""

from __future__ import annotations

import logging
from typing import Optional

from plobi_cli.dashboard_auth.base import (
    DashboardAuthProvider,
    LoginStart,
    Session,
    TokenPrincipal,
)
from plobi_cli.dashboard_auth.devices import get_store
from plobi_cli.dashboard_auth.token_auth import (
    register_token_route,
    register_token_route_pattern,
)

_log = logging.getLogger(__name__)

PROVIDER_NAME = "pairing"
HANDSET_PRINCIPAL = "handset"

SCOPE_AGENDA_READ = "agenda:read"
SCOPE_AGENDA_CONFIRM = "agenda:confirm"
SCOPE_TASK_READ = "task:read"

# A paired device may read the board and the task stream, confirm or dismiss a
# pending item, and nothing else. Re-pairing is deliberately *not* among the
# grants: a device that is already paired has no business minting peers, and a
# pairing code is not a credential at all here — it is consumed by
# ``pairing_api.redeem`` before any token exists (裁定 64).
HANDSET_SCOPES = (SCOPE_AGENDA_READ, SCOPE_AGENDA_CONFIRM, SCOPE_TASK_READ)

# Device-facing prefix. The agenda handlers are mounted here as well as under
# ``/api/agenda`` (browser session); this is the half the device token opens.
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


class HandsetPairingProvider(DashboardAuthProvider):
    """Non-interactive provider for the device tokens a paired handset keeps."""

    name = PROVIDER_NAME
    display_name = "Plobi handset (pairing token)"
    supports_token = True
    # Not an interactive login: excluded from the /login chooser and from the
    # cookie-verify loop, exactly like the drain service credential.
    supports_session = False

    def verify_token(self, *, token: str) -> Optional[TokenPrincipal]:
        if not token:
            return None
        device = get_store().verify_token(token)
        if device is None:
            return None
        # The device id is in the principal so an audit line names *which* handset
        # was at the door — the thing the shared app_token could never do.
        return TokenPrincipal(
            principal=f"{HANDSET_PRINCIPAL}:{device.device_id}",
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
        # Best-effort by contract: devices are revoked by name from the desktop's
        # pairing surface (dashboard_auth.devices), not through a session.
        return None


def install_pairing_auth() -> bool:
    """Register the provider and the handset routes. Returns whether it armed.

    Unlike the previous single-shared-token design this always arms: the store
    itself is what fails closed, so a backend with no paired device simply has no
    credential that verifies — which is also the state a device must pair out of.
    Idempotent — ``start_server`` may run more than once in a process, and routes
    are re-declared each call so a caller that only cleared the route registry
    still ends up with an armed surface.
    """
    from plobi_cli.dashboard_auth import registry

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
        "accept device tokens",
        len(_TOKEN_ROUTES),
        len(_TOKEN_ROUTE_PATTERNS_POST) + len(_TOKEN_ROUTE_PATTERNS_GET),
    )
    return True

