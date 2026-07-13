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

import os
import re
import json
import time
import base64
import sqlite3
import os
import subprocess
import platform
from pathlib import Path
from typing import Optional


# Google Code Assist OAuth client (the exact public client the Antigravity desktop
# app and the reference antigravity-proxy use). A refresh token minted against this
# client is exchangeable at oauth2.googleapis.com/token for a `ya29.` access token
# that works against cloudcode-pa.googleapis.com.
GOOGLE_CC_CLIENT_ID = "REDACTED_GOOGLE_PUBLIC_CLIENT_ID"
GOOGLE_CC_CLIENT_SECRET = os.environ.get("GOOGLE_CC_CLIENT_SECRET", "")
# Antigravity public OAuth client secret — supplied via GOOGLE_CC_CLIENT_SECRET env var
# at runtime, never committed to the repo.


# ---------------------------------------------------------------------------
# path resolution
# ---------------------------------------------------------------------------
def cursor_state_db() -> Optional[Path]:
    sys = platform.system()
    home = Path.home()
    if sys == "Darwin":
        p = home / "Library/Application Support/Cursor/User/globalStorage/state.vscdb"
    elif sys == "Windows":
        p = Path(os.environ.get("APPDATA", "")) / "Cursor/User/globalStorage/state.vscdb"
    else:
        p = home / ".config/Cursor/User/globalStorage/state.vscdb"
    return p if p.exists() else None


def antigravity_db() -> Optional[Path]:
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
        for k, v in cur.execute(
            f"SELECT key, value FROM ItemTable WHERE key IN ({qmarks})", keys
        ):
            out[k] = v.decode() if isinstance(v, (bytes, bytearray)) else str(v)
    finally:
        con.close()
    return out


def read_cursor() -> Optional[dict]:
    db = cursor_state_db()
    if not db:
        return None
    rows = _read_item_table(db, [
        "cursorAuth/accessToken",
        "cursorAuth/refreshToken",
        "cursorAuth/cachedEmail",
        "cursorAuth/stripeMembershipType",
    ])
    if not rows.get("cursorAuth/accessToken"):
        return None
    return {
        "access_token": rows.get("cursorAuth/accessToken", ""),
        "refresh_token": rows.get("cursorAuth/refreshToken", ""),
        "email": rows.get("cursorAuth/cachedEmail", ""),
        "tier": rows.get("cursorAuth/stripeMembershipType", ""),
    }


def read_antigravity() -> Optional[dict]:
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
    return (parts[0], parts[1] if len(parts) > 1 else "",
            parts[2] if len(parts) > 2 else "")


def read_antigravity_access_token() -> Optional[str]:
    """Read the live access token from the macOS Keychain entry
    svce="gemini", acct="antigravity" (a go-keyring-base64 blob that wraps a
    ``ya29.`` token).

    NOTE: on a headless session this command prompts for the Keychain password and
    will return None. It is provided for environments where the login Keychain is
    already unlocked (e.g. an interactive desktop session). Antigravity normally
    does NOT write a refreshed token back here, so this is mostly useful for a
    freshly-authenticated session.
    """
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", "gemini", "-a", "antigravity", "-w"],
            capture_output=True, text=True, timeout=8)
    except Exception:
        return None
    raw = (out.stdout or "").strip()
    if not raw:
        return None
    if raw.startswith("go-keyring-base64:"):
        try:
            raw = base64.b64decode(raw[len("go-keyring-base64:"):]).decode("latin1")
        except Exception:
            pass
    m = re.search(r"ya29\.[A-Za-z0-9._-]+", raw)
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
    return {"access_token": data.get("access_token", ""),
            "expires_at": _jwt_exp(data.get("access_token", "")) or time.time() + 3000}


async def refresh_antigravity(http, refresh_token: str,
                              client_id: str = GOOGLE_CC_CLIENT_ID,
                              client_secret: str = GOOGLE_CC_CLIENT_SECRET) -> dict:
    body = {"grant_type": "refresh_token", "refresh_token": refresh_token,
            "client_id": client_id}
    if client_secret:
        body["client_secret"] = client_secret
    r = await http.post("https://oauth2.googleapis.com/token", data=body)
    r.raise_for_status()
    d = r.json()
    return {"access_token": d["access_token"],
            "expires_at": time.time() + int(d.get("expires_in", 3000))}


def _jwt_exp(token: str) -> Optional[float]:
    try:
        import base64
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload)).get("exp"))
    except Exception:
        return None
