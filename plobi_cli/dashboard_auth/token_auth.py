"""Route-agnostic non-interactive (bearer-token) auth seam for the dashboard.

This is the generic API-token capability (decisions.md Q-C): a reusable seam
that ANY service-to-service / machine-credential provider plugs into, NOT a
drain-specific hook. The drain bearer-secret plugin is merely the first
consumer.

How it fits the existing auth framework:

  * The interactive gate (``gated_auth_middleware``) authenticates a human
    via a session cookie on every non-public route. A service caller has no
    cookie — it presents a bearer token in the ``Authorization`` header on a
    single request. That is what this seam verifies.

  * A route opts in by registering its exact path via
    :func:`register_token_route`. Only registered paths are token-authable;
    everything else is untouched, so this can never accidentally widen the
    auth surface of an existing route.

  * :func:`token_auth_middleware` runs OUTERMOST (installed last in
    ``web_server.py``). For a token route it fully owns the auth decision:
    authenticate via the stacked token providers, attach the verified
    :class:`~plobi_cli.dashboard_auth.base.TokenPrincipal` to
    ``request.state.token_principal`` + set ``request.state.token_authenticated``,
    and pass through; otherwise reject (401 unauthenticated, or 503 when a
    provider's backing store was unreachable). The downstream cookie/session
    gates honour ``token_authenticated`` and skip enforcement, so a
    token-authed service request is never bounced to ``/login``.

  * Fails closed: a token route with no registered token provider, no token,
    or an unrecognised token gets 401 — never an open pass-through.

Provider stacking mirrors ``verify_session``: each ``supports_token`` provider
is consulted in registration order until one returns a principal. A provider
that doesn't recognise the token returns ``None`` and the seam moves on; a
provider whose backing store is unreachable raises ``ProviderError``, which the
seam remembers and surfaces as 503 only if NO provider accepts the token.
"""
from __future__ import annotations

import logging
import re
import threading
from typing import Awaitable, Callable, Iterable, Optional, Tuple

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from plobi_cli.dashboard_auth import list_token_providers
from plobi_cli.dashboard_auth.audit import AuditEvent, audit_log
from plobi_cli.dashboard_auth.base import ProviderError, TokenPrincipal
from plobi_cli.dashboard_auth.headers import SESSION_HEADER_NAME

_log = logging.getLogger(__name__)

# Paths that accept non-interactive bearer-token auth, mapped to the HTTP
# methods the token may use on them (``None`` = all methods). A route registers
# itself here at import/startup; the seam only acts on registered paths.
#
# The method half exists because a path is not one right: ``/api/agenda`` is a
# read under ``GET`` and a board write under ``POST``. A caller registered for
# the read must not reach the write just because they share a URL.
_token_routes: dict[str, Optional[frozenset[str]]] = {}
# Parameterised families (e.g. ``/api/agenda/{id}/confirm``) can't be listed as
# exact paths. Patterns are anchored with ``fullmatch`` so a prefix can never
# quietly widen the auth surface — ``/api/agenda/[^/]+/confirm`` matches that one
# shape and nothing else.
_token_route_patterns: dict[re.Pattern[str], Optional[frozenset[str]]] = {}
_lock = threading.Lock()


def _normalise_methods(methods: Optional[Iterable[str]]) -> Optional[frozenset[str]]:
    """Iterable of verbs → uppercase frozenset, or ``None`` for "all methods"."""
    if not methods:
        return None
    return frozenset(m.strip().upper() for m in methods if m and m.strip())


def register_token_route(
    path: str, *, methods: Optional[Iterable[str]] = None
) -> None:
    """Mark ``path`` (exact match) as token-authable.

    Idempotent. Call at module import / app setup so the seam knows which
    routes to guard. Registering a route does NOT make it public — it makes
    it authenticate by token instead of by session cookie.

    ``methods`` narrows which verbs the token is good for; leave it out (the
    default, used by single-purpose routes like ``/api/gateway/drain``) to
    accept any method. Re-registering a path replaces its method set —
    registration happens once at startup, from a single writer.
    """
    with _lock:
        _token_routes[path] = _normalise_methods(methods)


def register_token_route_pattern(
    pattern: str, *, methods: Optional[Iterable[str]] = None
) -> None:
    """Mark a family of paths token-authable by anchored regex (fullmatch).

    Use this only when a route genuinely has a path parameter; prefer
    :func:`register_token_route`. The pattern is compiled and anchored —
    ``/api/x/[^/]+/confirm`` will not match ``/api/x/1/confirm/extra``.
    An invalid pattern is rejected rather than silently registered, so a typo
    can never widen the auth surface. ``methods`` behaves as in
    :func:`register_token_route`.
    """
    compiled = re.compile(pattern)
    allowed = _normalise_methods(methods)
    with _lock:
        _token_route_patterns[compiled] = allowed


def is_token_route(path: str, method: Optional[str] = None) -> bool:
    """True if ``path`` was registered as token-authable (exact or pattern).

    Pass ``method`` to check the verb too; omit it to ask only whether the path
    is token-authable at all.
    """
    verb = method.strip().upper() if method and method.strip() else None
    with _lock:
        if path in _token_routes:
            allowed = _token_routes[path]
            return allowed is None or verb is None or verb in allowed
        for pattern, allowed in _token_route_patterns.items():
            if pattern.fullmatch(path):
                return allowed is None or verb is None or verb in allowed
    return False


def clear_token_routes() -> None:
    """Test-only: drop all registered token routes and patterns."""
    with _lock:
        _token_routes.clear()
        _token_route_patterns.clear()


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else ""


def extract_bearer_token(request: Request) -> str:
    """Return the bearer token from the ``Authorization`` header, or "".

    Accepts ``<scheme> <token>`` where scheme is "bearer" (case-insensitive).
    Returns an empty string for a missing/malformed header or a non-bearer
    scheme — the caller treats "" as "no token presented".
    """
    auth = request.headers.get("authorization", "")
    parts = auth.split(" ", 1)
    if len(parts) == 2 and parts[0].strip().lower() == "bearer":
        return parts[1].strip()
    return ""


def authenticate_token(
    request: Request,
) -> Tuple[Optional[TokenPrincipal], Optional[str]]:
    """Try every token provider against the request's presented token.

    The token may arrive as ``Authorization: Bearer <token>`` (the seam's
    canonical wire) or in the dedicated :data:`SESSION_HEADER_NAME` header — the
    latter is what the desktop shell and the paired handset already send, and it
    exists so a reverse proxy owning ``Authorization`` does not collide with us.

    Returns ``(principal, unreachable_provider_name)``:
      * ``(TokenPrincipal, None)`` — a provider recognised and accepted the token.
      * ``(None, None)`` — no token, or no provider recognised it (reject 401).
      * ``(None, name)`` — no provider accepted it AND at least one provider's
        backing store was unreachable (the caller surfaces 503, not 401, so a
        transient outage doesn't read as "bad credentials").

    Never raises: a provider ``ProviderError`` is caught and remembered.
    """
    token = extract_bearer_token(request) or request.headers.get(
        SESSION_HEADER_NAME, ""
    ).strip()
    if not token:
        return None, None
    unreachable: Optional[str] = None
    for provider in list_token_providers():
        try:
            principal = provider.verify_token(token=token)
        except ProviderError as e:
            _log.warning(
                "dashboard-auth: token provider %r unreachable during verify: %s",
                provider.name, e,
            )
            if unreachable is None:
                unreachable = provider.name
            continue
        except Exception as e:  # noqa: BLE001 — a buggy provider must not 500 the gate
            _log.warning(
                "dashboard-auth: token provider %r raised during verify: %s",
                provider.name, e,
            )
            continue
        if principal is not None:
            return principal, None
    return None, unreachable


async def token_auth_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Outermost auth seam for token-authable routes.

    No-op pass-through for any (path, method) not registered via
    :func:`register_token_route`. For a registered one, token auth is the only
    accepted scheme:

      * valid token  → attach principal + ``token_authenticated`` flag, pass through.
      * unreachable  → 503 (provider backing store down; not "bad credentials").
      * otherwise    → 401 unauthenticated.

    A route registered for specific methods leaves the other methods to the
    downstream gates — that is how one path can be a read for a device and a
    write for a human session without the device credential reaching the write.

    Runs before the cookie/session gates (installed last in ``web_server.py``).
    The cookie gates honour ``request.state.token_authenticated`` and skip
    enforcement, so a token-authed request is never redirected to ``/login``.
    """
    path = request.url.path
    if not is_token_route(path, request.method):
        return await call_next(request)

    principal, unreachable = authenticate_token(request)
    if principal is not None:
        request.state.token_principal = principal
        request.state.token_authenticated = True
        return await call_next(request)

    if unreachable:
        audit_log(
            AuditEvent.TOKEN_AUTH_FAILURE,
            provider=unreachable,
            reason="provider_unreachable",
            path=path,
            ip=_client_ip(request),
        )
        return JSONResponse(
            {"detail": f"Auth provider {unreachable!r} unreachable"},
            status_code=503,
        )

    audit_log(
        AuditEvent.TOKEN_AUTH_FAILURE,
        reason="no_provider_recognises_token",
        path=path,
        ip=_client_ip(request),
    )
    return JSONResponse(
        {"error": "unauthenticated", "detail": "Unauthorized"},
        status_code=401,
    )
