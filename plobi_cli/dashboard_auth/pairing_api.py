"""HTTP surface for handset pairing (WP-APP-PAIR · 裁定 64).

Two routers, one per side of the pair:

* :data:`handset_router` — ``POST /redeem``, mounted at ``/api/handset/pair``.
  This is the device's door, and it is the **only** route in the whole
  ``/api/handset/*`` namespace that a caller may reach without a credential.
* :data:`desktop_router` — ``GET /devices``, ``POST /device/start``,
  ``POST /device/revoke``, mounted at ``/api/pairing`` next to the messaging
  platform pairing it resembles. Those three sit behind the ordinary dashboard
  gate: a browser session (gated bind) or the session token (loopback), never a
  device token and never an anonymous call.

Why ``/redeem`` carries the code in its **body** instead of as a bearer
credential on the token seam: the seam authenticates a caller before the handler
runs, and a wrong presentation never reaches the handler. The guess budget lives
in :class:`~plobi_cli.dashboard_auth.devices.DeviceStore`, so routing a code
through the seam would mean the 401s it produces are not counted — five guesses
become unlimited, which for a 6-digit code is the whole defence. Presenting the
code as input keeps counting where the rejecting happens. ``pair:redeem`` as a
*scope* therefore does not exist either: a pairing code is not a credential for
anything, and it certainly is not one that reads the agenda.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from plobi_cli.dashboard_auth.audit import AuditEvent, audit_log
from plobi_cli.dashboard_auth.devices import (
    CODE_TTL_SECONDS,
    PairingLocked,
    get_store,
)
from plobi_cli.dashboard_auth.pairing import HANDSET_API_PREFIX, HANDSET_SCOPES

# The mount prefixes live here rather than at the call sites so that the public
# allowlist entry, ``web_server.py``'s ``include_router``, the seam's registered
# routes and the tests all speak about one path. A drift between the allowlist and
# the mount is not a 404 — it is either a route nobody can reach or a route that
# is public while its handler believes it is gated.
HANDSET_PAIR_PREFIX = f"{HANDSET_API_PREFIX}/pair"
REDEEM_PATH = f"{HANDSET_PAIR_PREFIX}/redeem"
DESKTOP_PAIRING_PREFIX = "/api/pairing"

handset_router = APIRouter()
desktop_router = APIRouter()


class RedeemBody(BaseModel):
    """What a device sends to trade its pairing code for its own token."""

    code: str = Field(min_length=1, max_length=32)
    deviceName: str = Field(default="", max_length=64)


class DeviceIdBody(BaseModel):
    deviceId: str = Field(min_length=1, max_length=64)


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else ""


def _locked() -> HTTPException:
    """429 with the one thing the caller needs — how long to back off."""
    locked_until = get_store().pairing_locked_until()
    return HTTPException(
        status_code=429,
        detail="pairing is locked after repeated failed attempts",
        headers={"Retry-After": str(max(0, int(locked_until - time.time())))},
    )


# ---------------------------------------------------------------- device side


@handset_router.post("/redeem")
async def redeem_pairing_code(body: RedeemBody, request: Request) -> dict:
    """Turn a one-time pairing code into this device's own long-lived token.

    The token is returned exactly once and is never stored in plaintext, so a
    device that loses it re-pairs rather than recovering it.
    """
    store = get_store()
    try:
        minted = await run_in_threadpool(
            store.redeem, body.code.strip(), name=body.deviceName
        )
    except PairingLocked as exc:
        audit_log(
            AuditEvent.PAIRING_FAILURE,
            reason="locked",
            ip=_client_ip(request),
        )
        raise _locked() from exc

    if minted is None:
        # ``code`` is on the audit redact list, so a typo can never reach the log.
        audit_log(
            AuditEvent.PAIRING_FAILURE,
            reason="code_rejected",
            ip=_client_ip(request),
        )
        raise HTTPException(
            status_code=401,
            detail="pairing code is wrong, expired, or already used",
        )

    device_id, token = minted
    audit_log(
        AuditEvent.DEVICE_PAIRED,
        device_id=device_id,
        device_name=body.deviceName,
        ip=_client_ip(request),
    )
    return {
        "deviceId": device_id,
        "token": token,
        "scopes": list(HANDSET_SCOPES),
    }


# --------------------------------------------------------------- desktop side


@desktop_router.get("/devices")
async def list_paired_devices() -> dict:
    """Every device that holds a token here, plus the state of the pairing window."""
    store = get_store()
    return {
        "devices": [
            {
                "deviceId": device.device_id,
                "name": device.name,
                "pairedAt": device.paired_at,
                "lastSeenAt": device.last_seen_at,
            }
            for device in store.list_devices()
        ],
        "codeExpiresAt": store.pending_code_expires_at(),
        "pairingLockedUntil": store.pairing_locked_until(),
    }


@desktop_router.post("/device/start")
async def start_device_pairing(request: Request) -> dict:
    """Open the pairing window: one 6-digit code, 5 minutes, one use.

    Any code that was open before this call stops working — the desktop shows one
    code at a time, and an abandoned window on a screen nobody is reading is
    surface area with no user.
    """
    store = get_store()
    try:
        code, expires_at = await run_in_threadpool(store.issue_code)
    except PairingLocked as exc:
        raise _locked() from exc

    audit_log(
        AuditEvent.PAIRING_CODE_ISSUED,
        expires_at=expires_at,
        ip=_client_ip(request),
    )
    return {
        "code": code,
        "expiresAt": expires_at,
        "ttlSeconds": CODE_TTL_SECONDS,
    }


@desktop_router.post("/device/revoke")
async def revoke_device(body: DeviceIdBody, request: Request) -> dict:
    """End exactly one device's access — the point of per-device tokens."""
    store = get_store()
    if not await run_in_threadpool(store.revoke, body.deviceId):
        raise HTTPException(status_code=404, detail="no such paired device")
    audit_log(
        AuditEvent.DEVICE_REVOKED,
        device_id=body.deviceId,
        ip=_client_ip(request),
    )
    return {"ok": True}
