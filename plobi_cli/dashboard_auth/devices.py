"""Paired-device credentials for the handset surface (WP-APP-PAIR, 裁定 60 / 63 / 64).

Why a per-device token instead of the shared ``$PLOBI_HOME/plobi/app_token``: with
one token for every client, "revoke the handset that got lost" means rotating the
credential on every other device too, and the desktop can't even tell which device
is knocking. Here each device carries its own secret, the desktop lists them by
name, and revoking one ends exactly one device.

What is reused from :mod:`gateway.pairing` (and what is not): the *code machinery*
is — salted SHA-256 so no code is ever stored in plaintext, one-time consumption,
expiry, and lockout after repeated wrong attempts. What is not reused is the
platform allow-list mirror (``{platform}-approved.json`` and
``*_ALLOWED_USERS``): a device is not a messaging-platform user, and mirroring it
into that union would silently hand a handset a chat-operator identity.

Security posture, deliberately tighter than the platform flow because the code is
typed on a shared LAN over plain HTTP:

  * 6 numeric digits, generated with :mod:`secrets`
  * 5-minute TTL, single use, and **one open pairing window at a time** — starting
    a new one invalidates the code that was on screen before it
  * 5 failed redemption attempts lock pairing for 1 hour
  * only salted hashes of codes *and* device tokens are persisted, files at 0600

Two consequences of the one-window rule are worth naming, because they are the
reason this store (not an auth middleware) owns the failure counter:

  * The code is presented *as input* to ``POST /api/handset/pair/redeem``, a route
    that is reachable without a credential — knowing the code is the credential.
    Every wrong guess must therefore be counted where it is rejected, otherwise
    the guess budget is unbounded and a 6-digit code is brute-forceable.
  * Pairing can be locked out by anyone on the LAN who guesses five times. That is
    a nuisance (no new devices until the hour is up), never an escalation:
    already-paired devices keep their tokens and nothing else is affected.

Transit is still cleartext on the LAN — the same as every other credential this
backend accepts over ``http://<LAN-IP>:8787`` — which is why remote reachability is
gated behind the user's own tunnel (裁定 56.3), not solved here.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from gateway.pairing import _secure_write
from plobi_constants import get_plobi_home

CODE_DIGITS = 6
CODE_TTL_SECONDS = 300                  # 5 minutes — typed by a human, then used once
MAX_FAILED_REDEEM = 5                   # then pairing locks up
LOCKOUT_SECONDS = 3600
TOKEN_BYTES = 32

_DEVICES_FILENAME = "devices.json"
_CODES_FILENAME = "pairing_codes.json"


class PairingLocked(Exception):
    """Pairing is temporarily locked after too many wrong redemption attempts."""


def _hash(secret: str, salt: bytes) -> str:
    return hashlib.sha256(salt + secret.encode("utf-8")).hexdigest()


def _digest(secret: str, salt_hex: str) -> Optional[str]:
    """Hash ``secret`` against a stored record's salt; ``None`` if that record is junk.

    A hand-edited or half-written file can hold a salt that isn't hex. In an auth
    path that must read as "this record matches nothing" (401), never as a 500 —
    the same rule the non-ASCII token case is tested against.
    """
    try:
        salt = bytes.fromhex(salt_hex)
    except (TypeError, ValueError):
        return None
    return _hash(secret, salt)


def _new_code() -> str:
    return "".join(secrets.choice("0123456789") for _ in range(CODE_DIGITS))


@dataclass(frozen=True)
class Device:
    device_id: str
    name: str
    paired_at: float
    last_seen_at: float


class DeviceStore:
    """File-backed store: one pending pairing code + the issued device tokens."""

    def __init__(self, home: Optional[Path] = None):
        root = (home if home is not None else get_plobi_home()) / "plobi"
        self._dir = root
        self._devices_path = root / _DEVICES_FILENAME
        self._codes_path = root / _CODES_FILENAME
        self._lock = threading.RLock()

    # ----- storage -----

    def _load(self, path: Path) -> dict:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # A torn or foreign-owned file must fail closed (no device is trusted),
            # never fail open by treating the store as empty-but-usable.
            return {}

    def _save(self, path: Path, data: dict) -> None:
        _secure_write(path, json.dumps(data, indent=2, ensure_ascii=False))

    # ----- pairing codes -----
    #
    # One file, two top-level keys: ``active`` (the code that is on screen, or
    # ``{}``) and ``failed`` (the guess budget). They are separate keys rather
    # than sentinel entries inside one dict because expiry sweeping walks the
    # code records — a sweep that also saw the counter as "an entry without an
    # expires_at" would delete it, and the lockout would never accumulate.

    def _codes(self) -> tuple[dict, dict]:
        data = self._load(self._codes_path)
        active = data.get("active")
        failed = data.get("failed")
        return (
            active if isinstance(active, dict) else {},
            failed if isinstance(failed, dict) else {},
        )

    def _save_codes(self, active: dict, failed: dict) -> None:
        self._save(self._codes_path, {"active": active, "failed": failed})

    def _live_code(self) -> dict:
        """The open pairing code record, or ``{}`` — sweeping it if expired."""
        active, failed = self._codes()
        if active and float(active.get("expires_at", 0)) <= time.time():
            self._save_codes({}, failed)
            return {}
        return active

    def issue_code(self) -> tuple[str, float]:
        """Open the pairing window. Returns ``(code, expires_at)``.

        Any code that was open before this one stops working: the desktop shows
        exactly one code, and a stale one on a screen nobody is looking at is
        surface area with no user.

        Codes are salted and hashed before they touch the disk; the plaintext is
        handed to the caller once, to be shown on the desktop and typed on the
        device.
        """
        with self._lock:
            locked_until = self.pairing_locked_until()
            if locked_until:
                raise PairingLocked(
                    f"pairing is locked until {int(locked_until)} after repeated "
                    "failed attempts"
                )
            code = _new_code()
            salt = os.urandom(16)
            expires_at = time.time() + CODE_TTL_SECONDS
            failed = self._codes()[1]
            self._save_codes(
                {
                    "hash": _hash(code, salt),
                    "salt": salt.hex(),
                    "created_at": time.time(),
                    "expires_at": expires_at,
                },
                failed,
            )
            return code, expires_at

    def pending_code_expires_at(self) -> float:
        """When the open pairing window closes, or ``0.0`` when none is open."""
        return float(self._live_code().get("expires_at", 0) or 0.0)

    def _failed_attempts(self) -> tuple[int, float]:
        failed = self._codes()[1]
        return int(failed.get("count", 0) or 0), float(failed.get("locked_until", 0) or 0.0)

    def _record_failure(self) -> bool:
        """Charge one wrong guess. Returns whether that was the last one allowed."""
        with self._lock:
            count, locked_until = self._failed_attempts()
            count += 1
            if count >= MAX_FAILED_REDEEM:
                locked_until = time.time() + LOCKOUT_SECONDS
                count = 0
            self._save_codes(self._live_code(), {"count": count, "locked_until": locked_until})
            return locked_until > time.time()

    def pairing_locked_until(self) -> float:
        _count, locked_until = self._failed_attempts()
        return locked_until if locked_until > time.time() else 0.0

    # ----- redemption -----

    def redeem(self, code: str, *, name: str) -> Optional[tuple[str, str]]:
        """Consume the pairing code and mint the device that redeems it.

        Returns ``(device_id, token)`` — the plaintext token exists here once and
        is never written down. ``None`` means nothing was minted.

        The two flavours of ``None`` are charged differently, on purpose: a wrong
        guess **while a window is open** counts against the lockout (that budget is
        the only thing keeping a 6-digit code out of reach of a LAN brute force),
        while a call against no open window — expired, used, never started — is
        refused but not charged, because a device fumbling after its code lapsed
        is the operator's annoyance, not an attack signal, and charging it would
        let stale retries lock pairing before anyone ever saw a code.

        Raises :class:`PairingLocked` when the guess budget is spent.
        """
        presented = (code or "").strip()
        if not presented:
            return None
        with self._lock:
            if self.pairing_locked_until():
                raise PairingLocked("pairing is locked after repeated failures")
            active = self._live_code()
            if not active:
                return None
            candidate = _digest(presented, active.get("salt", ""))
            if candidate is None or not secrets.compare_digest(candidate, active.get("hash", "")):
                # The fifth wrong guess is itself refused with the lockout, not
                # silently answered with "wrong" — the caller shows "pairing
                # locked" instead of a 401 the operator has to interpret.
                if self._record_failure():
                    raise PairingLocked("pairing is locked after repeated failures")
                return None
            self._save_codes({}, {"count": 0, "locked_until": 0.0})

            device_id = "dev-" + secrets.token_hex(6)
            token = secrets.token_urlsafe(TOKEN_BYTES)
            salt = os.urandom(16)
            now = time.time()
            devices = self._load(self._devices_path)
            devices[device_id] = {
                "name": (name or "").strip()[:64] or device_id,
                "hash": _hash(token, salt),
                "salt": salt.hex(),
                "paired_at": now,
                "last_seen_at": now,
            }
            self._save(self._devices_path, devices)
            return device_id, token

    # ----- device tokens -----

    def verify_token(self, token: str) -> Optional[Device]:
        """Return the device whose token this is, or ``None``.

        Every stored record is hashed with its own salt, so the lookup is a
        constant-time digest comparison per device — the population is a handful of
        handsets, and never learning *which* slot matched is the point.
        """
        presented = (token or "").strip()
        if not presented:
            return None
        with self._lock:
            devices = self._load(self._devices_path)
            for device_id, record in devices.items():
                candidate = _digest(presented, record.get("salt", ""))
                if candidate is None or not secrets.compare_digest(
                    candidate, record.get("hash", "")
                ):
                    continue
                now = time.time()
                # Last-seen is observability, not an audit trail: only write it when
                # it moves by more than a minute, or every request hits the disk.
                if now - float(record.get("last_seen_at", 0)) > 60:
                    record["last_seen_at"] = now
                    self._save(self._devices_path, devices)
                return Device(
                    device_id=device_id,
                    name=record.get("name", device_id),
                    paired_at=float(record.get("paired_at", 0.0)),
                    last_seen_at=float(record.get("last_seen_at", 0.0)),
                )
            return None

    def list_devices(self) -> list[Device]:
        devices = self._load(self._devices_path)
        return [
            Device(
                device_id=device_id,
                name=record.get("name", device_id),
                paired_at=float(record.get("paired_at", 0.0)),
                last_seen_at=float(record.get("last_seen_at", 0.0)),
            )
            for device_id, record in sorted(
                devices.items(), key=lambda kv: float(kv[1].get("paired_at", 0.0))
            )
        ]

    def revoke(self, device_id: str) -> bool:
        """Drop one device. Its token stops verifying on the next request."""
        with self._lock:
            devices = self._load(self._devices_path)
            if device_id not in devices:
                return False
            del devices[device_id]
            self._save(self._devices_path, devices)
            return True


_DEFAULT: Optional[DeviceStore] = None
_default_lock = threading.Lock()


def get_store() -> DeviceStore:
    """Process-wide store. Tests swap :data:`_DEFAULT` (or pass their own)."""
    global _DEFAULT
    with _default_lock:
        if _DEFAULT is None:
            _DEFAULT = DeviceStore()
        return _DEFAULT


def reset_store_for_tests(home: Optional[Path] = None) -> DeviceStore:
    """Point the module-level store at a throwaway home (never the real one)."""
    global _DEFAULT
    with _default_lock:
        _DEFAULT = DeviceStore(home)
        return _DEFAULT
