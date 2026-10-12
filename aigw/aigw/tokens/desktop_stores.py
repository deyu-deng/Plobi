r"""Read credentials directly from each desktop app's local storage.

VERIFIED paths / keys (as of research 2026-07; may drift — verify with MITM tool):

Cursor (macOS):
  ~/Library/Application Support/Cursor/User/globalStorage/state.vscdb   (SQLite)
    table ItemTable, key='cursorAuth/accessToken'  -> JWT bearer
    table ItemTable, key='cursorAuth/refreshToken' -> refresh credential
    table ItemTable, key='cursorAuth/cachedEmail'  -> account email
  Refresh: POST https://api2.cursor.sh/oauth/token
    {"grant_type":"refresh_token","client_id":"KbZUR41cY7W6zRSdpSUJ7I7mLYBKOCmB",
     "refresh_token": "..."}  -> {access_token, id_token, shouldLogout}
  NOTE: Cursor's *chat* traffic is gRPC/Connect protobuf to aiserver.v1.*, NOT
  plain REST. The bearer above authenticates it, but the wire format needs the
  Connect adapter (see providers/cursor.py) or MITM-captured protobuf schemas.

Antigravity:
  ~/.antigravity/db.sqlite  (SQLite)  table 'auth' -> refresh_token
  Refresh: Google OAuth  POST https://oauth2.googleapis.com/token
  Upstream chat: daily-cloudcode-pa.sandbox.googleapis.com (Cloud Code wrapping of
  Gemini generateContent).

Windows/Linux Cursor path differs:
  Win:   %APPDATA%\Cursor\User\globalStorage\state.vscdb
  Linux: ~/.config/Cursor/User/globalStorage/state.vscdb
"""

from __future__ import annotations

import base64
import json
import os
import platform
import re
import sqlite3
import subprocess
import time
from datetime import datetime
from pathlib import Path

# Google Code Assist OAuth client (the exact public client the Antigravity desktop
# app and the reference antigravity-proxy use). A refresh token minted against this
# client is exchangeable at oauth2.googleapis.com/token for a `ya29.` access token
# that works against cloudcode-pa.googleapis.com.
GOOGLE_CC_CLIENT_ID = os.environ.get("GOOGLE_CC_CLIENT_ID")
GOOGLE_CC_CLIENT_SECRET = os.environ.get("GOOGLE_CC_CLIENT_SECRET")
# Antigravity OAuth client credentials are not hardcoded in this repository.
# Set GOOGLE_CC_CLIENT_ID and GOOGLE_CC_CLIENT_SECRET in the environment
# before using this module.


# ---------------------------------------------------------------------------
# path resolution
# ---------------------------------------------------------------------------
def cursor_state_db() -> Path | None:
    sys = platform.system()
    home = Path.home()
    if sys == "Darwin":
        p = home / "Library/Application Support/Cursor/User/globalStorage/state.vscdb"
    elif sys == "Windows":
        p = Path(os.environ.get("APPDATA", "")) / "Cursor/User/globalStorage/state.vscdb"
    else:
        p = home / ".config/Cursor/User/globalStorage/state.vscdb"
    return p if p.exists() else None


def cursor_storage_json() -> Path | None:
    """Cursor's VS Code-style storage.json holds the device ids used to build
    the `x-cursor-checksum` anti-abuse header. Path mirrors state.vscdb."""
    sys = platform.system()
    home = Path.home()
    if sys == "Darwin":
        p = home / "Library/Application Support/Cursor/User/globalStorage/storage.json"
    elif sys == "Windows":
        p = Path(os.environ.get("APPDATA", "")) / "Cursor/User/globalStorage/storage.json"
    else:
        p = home / ".config/Cursor/User/globalStorage/storage.json"
    return p if p.exists() else None


def antigravity_db() -> Path | None:
    p = Path.home() / ".antigravity/db.sqlite"
    return p if p.exists() else None


# ---------------------------------------------------------------------------
# raw SQLite readers (open read-only, never write to the app's DB)
# ---------------------------------------------------------------------------
def _read_item_table(db_path: Path, keys: list[str]) -> dict[str, str]:
    """Cursor stores auth in a VS Code-style ItemTable(key TEXT, value BLOB)."""
    uri = f"file:{db_path}?mode=ro"
    out: dict[str, str] = {}
    con = sqlite3.connect(uri, uri=True, timeout=3.0)
    try:
        cur = con.cursor()
        qmarks = ",".join("?" * len(keys))
        for k, v in cur.execute(f"SELECT key, value FROM ItemTable WHERE key IN ({qmarks})", keys):
            out[k] = v.decode() if isinstance(v, (bytes, bytearray)) else str(v)
    finally:
        con.close()
    return out


def read_cursor() -> dict | None:
    db = cursor_state_db()
    if not db:
        return None
    rows = _read_item_table(
        db,
        [
            "cursorAuth/accessToken",
            "cursorAuth/refreshToken",
            "cursorAuth/cachedEmail",
            "cursorAuth/stripeMembershipType",
        ],
    )
    if not rows.get("cursorAuth/accessToken"):
        return None
    out = {
        "access_token": rows.get("cursorAuth/accessToken", ""),
        "refresh_token": rows.get("cursorAuth/refreshToken", ""),
        "email": rows.get("cursorAuth/cachedEmail", ""),
        "tier": rows.get("cursorAuth/stripeMembershipType", ""),
        # Device ids for x-cursor-checksum (best-effort; absent is OK — the
        # checksum just omits the machine-id suffix in that case).
        "machine_id": "",
        "mac_machine_id": "",
    }
    sj = cursor_storage_json()
    if sj:
        try:
            with open(sj, "r", encoding="utf-8", errors="replace") as fh:
                data = json.load(fh)
            out["machine_id"] = data.get("telemetry.machineId", "") or ""
            out["mac_machine_id"] = data.get("telemetry.macMachineId", "") or ""
        except Exception:
            pass
    return out


def read_antigravity() -> dict | None:
    db = antigravity_db()
    if not db:
        return None
    uri = f"file:{db}?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=3.0)
    try:
        cur = con.cursor()
        # schema is not documented; probe the 'auth' table generically and grab
        # the first row containing a refresh_token-looking column.
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cur.fetchall()]
        if "auth" not in tables:
            return None
        cur.execute("SELECT * FROM auth LIMIT 5")
        cols = [d[0] for d in cur.description]
        for row in cur.fetchall():
            rec = dict(zip(cols, row))
            rt = rec.get("refresh_token") or rec.get("refreshToken")
            if rt:
                return {"refresh_token": rt, "raw": rec}
    finally:
        con.close()
    return None


def parse_composite_refresh(refresh: str) -> tuple[str, str, str]:
    """The reference proxy stores refresh tokens as a composite
    ``refreshToken|projectId|managedProjectId``. Split it back out.

    Returns (refresh_token, project_id, managed_project_id). Missing parts -> "".
    """
    parts = (refresh or "").split("|")
    return (parts[0], parts[1] if len(parts) > 1 else "", parts[2] if len(parts) > 2 else "")


_YA29_RE = re.compile(r"ya29\.[A-Za-z0-9._-]+")

# The Antigravity desktop CLI on Windows persists its OAuth token in the current
# user's Credential Manager under this target name. Measured 2026-10-11 (this
# machine): blob = 1,687 bytes of UTF-8 JSON, keys `auth_method` / `id_token` /
# `token{access_token, refresh_token, expiry, token_type}`, and — unlike the
# macOS Keychain entry below — it IS written back when the app refreshes
# (`LastWritten` moved to the same minute `agy models` renewed the token).
# Nothing here ever prints or persists a token value.
WIN_ANTIGRAVITY_TARGET = "gemini:antigravity"


def _win_credential_text(target: str) -> str | None:
    """A generic-password blob as text, or None (not Windows / absent / unreadable).

    ctypes rather than `win32cred`: pywin32 reads this entry fine here, but it is
    not a declared dependency of aigw, and an undeclared import is a feature that
    works on one machine and ImportErrors on every other one.
    """
    if os.name != "nt":
        return None
    import ctypes
    import ctypes.wintypes as wt

    class _Attribute(ctypes.Structure):
        _fields_ = [("Keyword", wt.LPWSTR), ("Flags", wt.DWORD), ("Value", wt.LPWSTR)]

    class _Credential(ctypes.Structure):
        _fields_ = [
            ("Flags", wt.DWORD),
            ("Type", wt.DWORD),
            ("TargetName", wt.LPWSTR),
            ("Comment", wt.LPWSTR),
            ("LastWritten", wt.FILETIME),
            ("CredentialBlobLength", wt.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", wt.DWORD),
            ("AttributeCount", wt.DWORD),
            ("Attributes", ctypes.POINTER(_Attribute)),
            ("TargetAliasName", wt.LPWSTR),
            ("UserName", wt.LPWSTR),
            ("UnspecifiedName", wt.LPWSTR),
        ]

    try:
        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        advapi.CredReadW.argtypes = [
            wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.POINTER(ctypes.POINTER(_Credential))
        ]
        advapi.CredReadW.restype = wt.BOOL
        advapi.CredFree.argtypes = [ctypes.POINTER(_Credential)]
        advapi.CredFree.restype = None
        handle = ctypes.POINTER(_Credential)()
        if not advapi.CredReadW(target, 1, 0, ctypes.byref(handle)):  # 1 = CRED_TYPE_GENERIC
            return None
        try:
            cred = handle.contents
            raw = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobLength)
        finally:
            advapi.CredFree(handle)
        return raw.decode("utf-8", "replace")
    except Exception:
        return None


def _unwrap_go_keyring(raw: str) -> str:
    if raw.startswith("go-keyring-base64:"):
        try:
            return base64.b64decode(raw[len("go-keyring-base64:") :]).decode("latin1")
        except Exception:
            pass
    return raw


def _iso_to_ts(value: str) -> float | None:
    """Unix seconds for a stored expiry, or None when we cannot tell.

    Tolerant by hand because Google's Go side writes 7-digit fractions and
    sometimes `Z`, neither of which `datetime.fromisoformat` accepts on Python
    3.10 (the floor aigw declares). A naive stamp returns None: inventing a
    timezone here would decide "expired" by coin flip, so the caller keeps the
    token and lets the upstream answer instead.
    """
    s = (value or "").strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    m = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(.*)$", s)
    if not m:
        return None
    frac = (m.group(2) or "")[:6]
    try:
        dt = datetime.fromisoformat(m.group(1) + (".%s" % frac if frac else "") + m.group(3))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.timestamp()


def _win_token_and_expiry(raw: str | None) -> tuple[str | None, float | None]:
    """(access_token, expires_at) from a Windows credential blob.

    An *expired* token is still returned with its timestamp — the caller decides.
    `read_antigravity_access_token()` drops it (a guaranteed 401 is not worth a
    round-trip), while `antigravity_desktop_token_status()` needs it to be able
    to say "expired 12 minutes ago", which is the one thing an operator can act
    on (run agy once; it renews and writes the entry back).
    """
    if not raw:
        return None, None
    raw = _unwrap_go_keyring(raw)
    token, expiry = None, None
    try:
        data = json.loads(raw)
    except Exception:
        data = None
    if isinstance(data, dict):
        inner = data.get("token")
        if isinstance(inner, dict):
            token, expiry = inner.get("access_token"), inner.get("expiry")
        elif isinstance(inner, str):
            token = inner
        else:
            token, expiry = data.get("access_token"), data.get("expiry")
    if not token:
        m = _YA29_RE.search(raw)
        token = m.group(0) if m else None
    return (token or None), _iso_to_ts(expiry or "")


def _read_antigravity_access_token_windows() -> str | None:
    token, ts = _win_token_and_expiry(_win_credential_text(WIN_ANTIGRAVITY_TARGET))
    if not token:
        return None
    if ts is not None and ts <= time.time():
        # Known-stale: hand this over and it is a guaranteed 401 round-trip.
        return None
    return token


def antigravity_desktop_token_status() -> dict:
    """Lifetime of the desktop-store token, WITHOUT the token itself.

    For the doctor's one-line self-check. Never put a token, a length, or a
    fingerprint-derived value in here that could end up in a log: this returns
    presence and timestamps only.
    """
    out = {"source": "none", "present": False, "expires_at": None,
           "expires_in_seconds": None}
    if os.name == "nt":
        out["source"] = "windows-credential-manager"
        token, ts = _win_token_and_expiry(_win_credential_text(WIN_ANTIGRAVITY_TARGET))
    else:
        out["source"] = "macos-keychain"
        token = read_antigravity_access_token()
        ts = None
    if token:
        out["present"] = True
        out["expires_at"] = ts
        out["expires_in_seconds"] = (ts - time.time()) if ts else None
    return out


def read_antigravity_access_token() -> str | None:
    """Read the live access token from whichever desktop store this OS uses
    (Windows: Credential Manager target `gemini:antigravity`; macOS: Keychain
    entry svce="gemini", acct="antigravity", a `go-keyring-base64` blob wrapping a
    `ya29.` token). Returns None when there is no entry, it holds no token, or it
    says the token is already expired.

    NOTE: on macOS a headless session makes `security` prompt for the Keychain
    password and return nothing, so this is mostly useful for an interactive
    desktop session — and Antigravity does not normally write a refreshed token
    back to the Keychain, only to the Windows store (measured above).
    """
    if os.name == "nt":
        return _read_antigravity_access_token_windows()
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", "gemini", "-a", "antigravity", "-w"],
            capture_output=True,
            text=True,
            timeout=8,
        )
    except Exception:
        return None
    raw = (out.stdout or "").strip()
    if not raw:
        return None
    raw = _unwrap_go_keyring(raw)
    m = _YA29_RE.search(raw)
    return m.group(0) if m else None


# ---------------------------------------------------------------------------
# refresh flows
# ---------------------------------------------------------------------------
async def refresh_cursor(http, refresh_token: str) -> dict:
    """Returns {access_token, expires_at} or raises. shouldLogout=True => dead."""
    r = await http.post(
        "https://api2.cursor.sh/oauth/token",
        json={
            "grant_type": "refresh_token",
            "client_id": "KbZUR41cY7W6zRSdpSUJ7I7mLYBKOCmB",
            "refresh_token": refresh_token,
        },
        headers={"content-type": "application/json"},
    )
    r.raise_for_status()
    data = r.json()
    if data.get("shouldLogout"):
        raise PermissionError("cursor refresh token dead (shouldLogout=true)")
    # access token is a JWT; exp is inside. Default 50 min if not parsed.
    return {
        "access_token": data.get("access_token", ""),
        "expires_at": _jwt_exp(data.get("access_token", "")) or time.time() + 3000,
    }


async def refresh_antigravity(
    http,
    refresh_token: str,
    client_id: str = GOOGLE_CC_CLIENT_ID,
    client_secret: str = GOOGLE_CC_CLIENT_SECRET,
) -> dict:
    body = {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id}
    if client_secret:
        body["client_secret"] = client_secret
    r = await http.post("https://oauth2.googleapis.com/token", data=body)
    r.raise_for_status()
    d = r.json()
    return {
        "access_token": d["access_token"],
        "expires_at": time.time() + int(d.get("expires_in", 3000)),
    }


# ---------------------------------------------------------------------------
# CodeBuddy / WorkBuddy: the desktop extension's own auth record
# ---------------------------------------------------------------------------
# Measured 2026-10-12 on this machine: the record is PLAINTEXT JSON at
#   %LOCALAPPDATA%\CodeBuddyExtension\Data\Public\auth\Tencent-Cloud.coding-copilot.info
# holding `auth.accessToken` (JWT, 1361 chars, exp ≈ +28 days), `auth.refreshToken`
# (JWT, 700 chars, exp ≈ +30 days) and `tokenType: "Bearer"`. The sibling record
# `workbuddy-desktop.info` is encrypted (`{"$wbEncrypted":1,…}` + a `keyblob` whose
# slots are `static-v1`, protector under Electron `safeStorage`) — and we do not need
# it: the plaintext one is accepted by the vendor origin. `POST /v2/chat/completions`
# with `stream: true` returned `finish_reason=tool_calls` (usage 196/52/248), while
# `stream: false` was refused with `{"code":11101,"msg":"Non-stream chat request is
# currently not supported"}` — a 400 that also proves the token passed authentication.
# Nothing here ever logs, returns into a log line, or persists a token value.
CODEBUDDY_AUTH_RECORD = os.path.join(
    "CodeBuddyExtension", "Data", "Public", "auth", "Tencent-Cloud.coding-copilot.info"
)


def codebuddy_auth_record_path() -> "Path | None":
    """Where the desktop app's auth record lives on this machine, if it is there."""
    base = os.environ.get("LOCALAPPDATA") or ""
    if not base:
        return None
    p = Path(base) / CODEBUDDY_AUTH_RECORD
    return p if p.is_file() else None


def read_codebuddy_token() -> "tuple[str, str, float] | None":
    """Return (access_token, refresh_token, expires_at_unix), or None.

    `expires_at` prefers the JWT's own `exp` claim (via `_jwt_exp`, the same helper
    the cursor path uses) and falls back to the record's millisecond `expiresAt`.
    Unreadable / unparseable / no access token → None: the caller must report
    "the desktop app is not logged in", never guess a token.
    """
    p = codebuddy_auth_record_path()
    if p is None:
        return None
    try:
        auth = json.loads(p.read_text(encoding="utf-8")).get("auth") or {}
    except Exception:
        return None
    if not isinstance(auth, dict):
        return None
    access = str(auth.get("accessToken") or "")
    refresh = str(auth.get("refreshToken") or "")
    if not access:
        return None
    exp = _jwt_exp(access) or 0.0
    if not exp:
        try:
            exp = float(auth.get("expiresAt") or 0) / 1000.0
        except Exception:
            exp = 0.0
    return access, refresh, exp


def codebuddy_token_status() -> dict:
    """Presence and lifetime only — the surface the doctor's row reads.

    Never add a token, a length-derived value, or a fingerprint to this dict that
    could end up in a log: it must stay value-free (a test pins that).
    """
    out = {"source": "codebuddy-desktop-store", "present": False, "expires_at": None,
           "expires_in_seconds": None, "can_refresh": False}
    got = read_codebuddy_token()
    if not got:
        return out
    _access, refresh, exp = got
    out["present"] = True
    out["can_refresh"] = bool(refresh)
    if exp:
        out["expires_at"] = exp
        out["expires_in_seconds"] = exp - time.time()
    return out


def _jwt_exp(token: str) -> float | None:
    try:
        import base64

        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload)).get("exp"))
    except Exception:
        return None
