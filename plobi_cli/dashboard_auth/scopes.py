"""Scope enforcement for the device-facing (token-authenticated) surfaces.

The seam proves *that* a bearer credential was recognised; it never decided what
that credential may do. Without this module the handset's scope list would be
decoration — a device paired years ago would keep the widest grant the schema
allows, and there would be nothing to narrow it against.

One rule per surface, checked after the seam has attached
``request.state.token_principal`` (``web_server.py`` attaches this dependency to
the ``/api/handset/*`` mounts only):

  * ``POST /api/handset/agenda/<id>/confirm|dismiss`` → ``agenda:confirm``
  * anything else under ``/api/handset/agenda``      → ``agenda:read``
  * ``/api/handset/tasks`` / ``/api/handset/events``  → ``task:read``

A request that reached a handset route *without* a token principal is refused
here too. That is the second half of the split: the seam already turns a browser
session away from a registered token route, but routes that are not token-registered
(``POST /api/handset/agenda``) would otherwise be served to whoever holds a cookie,
which is not what a device-only namespace means.
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from plobi_cli.dashboard_auth.pairing import (
    HANDSET_API_PREFIX,
    SCOPE_AGENDA_CONFIRM,
    SCOPE_AGENDA_READ,
    SCOPE_TASK_READ,
)


def required_scope(path: str, method: str) -> str:
    """The single scope that opens this request."""
    if path.startswith(f"{HANDSET_API_PREFIX}/tasks") or path.startswith(
        f"{HANDSET_API_PREFIX}/events"
    ):
        return SCOPE_TASK_READ
    if method.upper() == "POST" and path.startswith(f"{HANDSET_API_PREFIX}/agenda/"):
        return SCOPE_AGENDA_CONFIRM
    return SCOPE_AGENDA_READ


def enforce(request: Request) -> None:
    """FastAPI dependency: refuse any caller whose scopes don't cover this route."""
    principal = getattr(request.state, "token_principal", None)
    if principal is None:
        raise HTTPException(status_code=401, detail="paired device token required")
    scope = required_scope(request.url.path, request.method)
    if scope and scope not in (principal.scopes or ()):
        raise HTTPException(
            status_code=403,
            detail=f"paired device is not granted {scope!r} for this endpoint",
        )
