"""Qoder's own device-code sign-in, internalized.

Measured on this machine 2026-10-31 with the CLI's shipped client id:

* the consent page is `https://qoder.com.cn/device/selectAccounts` and takes
  `challenge` (S256 of a locally generated verifier), `challenge_method`, `nonce`,
  `machine_id`, `client_id`;
* the poll is `https://openapi.qoder.com.cn/api/v1/deviceToken/poll?nonce=&verifier=&challenge_method=S256`
  and, once he has picked the account in the browser, answers 200 with
  `token` / `refresh_token` / `user_id` / `expires_at` (measured lease: 30 days);
* no node is needed - Qoder's own edge is reachable from here directly, unlike the Google
  legs, so this login does not depend on Settings → VPN.

The CLI keeps no readable session file (`~/.qoder-cn/.auth` holds a machine id and cached
endpoint lists, not a token), so this flow is the only way the hub can be signed in at all
- and it means the leg never reads or writes another product's credential file: what the
poll returns goes into **our** store under the Plobi home, owner-only, same rule as Gemini.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import httpx

log = logging.getLogger("quota_hub.qoder")

BASE = "https://qoder.com.cn"
OPENAPI = "https://openapi.qoder.com.cn"
#: Device-lease renewal (measured 2026-11-03, HTTP 200). Body field must be
#: `refresh_token`; the Go-side `refreshToken` spelling answers 400.
REFRESH_PATH = "/api/v1/deviceToken/refresh"
#: The production client id shipped inside the installed CLI bundle.
CLIENT_ID = "e883ade2-e6e3-4d6d-adf7-f92ceff5fdcb"

#: The CLI's verifier alphabet (URL-safe set, 64 characters).
_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"

_LOGIN_TIMEOUT = 300.0
_POLL_SECONDS = 5.0


def _root() -> Path:
    home = os.environ.get("PLOBI_HOME", "").strip()
    return (Path(home) / "plobi") if home else (Path.home() / ".plobi" / "plobi")


def store_path() -> Path:
    """Our own credential file for this leg - the CLI's directory is not ours to touch."""
    return _root() / "qoder_creds.json"


def read_credential() -> dict[str, Any] | None:
    path = store_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_credential(tokens: dict[str, Any]) -> Path:
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = read_credential() or {}
    data = {**existing, **tokens, "saved_at": int(time.time())}
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:  # a platform without the bit still keeps the file owner-private
        pass
    return path


def _expiry_epoch(value: object) -> float | None:
    """Seconds-since-epoch from whatever the poll returned.

    Measured 2026-10-31: the answer carries `expires_at` as an **ISO string**
    ("2026-11-30T10:50:46Z"), while other Qoder surfaces use epoch numbers in seconds or
    milliseconds. Reading only one of the three silently keeps a dead token "valid".
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        stamp = float(value)
        return stamp / 1000.0 if stamp > 1e11 else stamp
    text = str(value).strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).timestamp()
    except (ValueError, OverflowError):
        return None


def signed_in() -> tuple[bool, str]:
    data = read_credential()
    if not data or not (data.get("token") or data.get("access_token")):
        return False, "no_desktop_credential"
    stamp = _expiry_epoch(data.get("expires_at"))
    if stamp is not None and time.time() >= stamp:
        return False, "credential_expired"
    return True, "ok"


def refresh_credential() -> dict[str, Any] | None:
    """Renew the device lease via `deviceToken/refresh` (measured 2026-11-03, HTTP 200).

    Body field must be `refresh_token` (OAuth naming); the Go-side `refreshToken`
    spelling answers 400 DeviceRefreshTokenRequired. Rotation: the old `drt-` token
    dies on use. The new pair is persisted atomically through `write_credential` (the
    `device_token` field is folded into `token` so read paths keep working). Returns
    the persisted credential dict, or None on any failure — caller owns backoff/reauth.
    """
    data = read_credential()
    rt = (data or {}).get("refresh_token")
    if not rt:
        return None
    try:
        res = httpx.post(
            f"{OPENAPI}{REFRESH_PATH}",
            json={"refresh_token": rt},
            headers={"Content-Type": "application/json", "User-Agent": "plobi-quota-hub/1.0"},
            timeout=30,
        )
    except httpx.HTTPError as exc:
        log.warning("qoder refresh request failed: %s", type(exc).__name__)
        return None
    if res.status_code != 200:
        log.warning("qoder refresh -> HTTP %s", res.status_code)
        return None
    try:
        tokens = res.json()
    except ValueError:
        log.warning("qoder refresh answered a non-JSON body")
        return None
    if not isinstance(tokens, dict) or not (tokens.get("device_token") or tokens.get("token")):
        log.warning("qoder refresh body missing device_token")
        return None
    normalized = dict(tokens)
    if normalized.get("device_token") and not normalized.get("token"):
        normalized["token"] = normalized["device_token"]
    write_credential(normalized)
    return normalized


def machine_id() -> str:
    """The install id the CLI already uses, so a login here is not a new device."""
    for candidate in (".qoder-cn", ".qoder"):
        path = Path.home() / candidate / "installation_id"
        if path.is_file():
            value = path.read_text(encoding="utf-8").strip()
            if value:
                return value
    return str(uuid.uuid4())


def pkce_pair() -> tuple[str, str]:
    length = 43 + secrets.randbelow(86)
    verifier = "".join(_ALPHABET[b % len(_ALPHABET)] for b in os.urandom(length))
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).decode().rstrip("=")


def login_url(challenge: str, nonce: str) -> str:
    return (f"{BASE}/device/selectAccounts?challenge={challenge}&challenge_method=S256"
            f"&nonce={nonce}&machine_id={machine_id()}&client_id={CLIENT_ID}")


def run_login(on_url: Callable[[str], None] | None = None, *, timeout: float = _LOGIN_TIMEOUT) -> dict:
    """The card's click: consent in his browser, credential in our own store.

    Returns what landed (never the token itself) so the caller can say it in words.
    """
    verifier, challenge = pkce_pair()
    nonce = str(uuid.uuid4())
    url = login_url(challenge, nonce)
    if on_url is not None:
        on_url(url)
    else:
        import webbrowser

        webbrowser.open(url)

    deadline = time.time() + timeout
    last = ""
    with httpx.Client(timeout=20.0) as client:
        while time.time() < deadline:
            try:
                res = client.get(f"{OPENAPI}/api/v1/deviceToken/poll",
                                 params={"nonce": nonce, "verifier": verifier, "challenge_method": "S256"})
            except httpx.HTTPError as exc:
                last = f"{type(exc).__name__}"
                time.sleep(_POLL_SECONDS)
                continue
            if res.status_code == 200:
                try:
                    tokens = res.json()
                except ValueError:
                    raise RuntimeError("Qoder 的登录回话不是 JSON，这次登录没认出来") from None
                if not isinstance(tokens, dict) or not (tokens.get("token") or tokens.get("access_token")):
                    raise RuntimeError("Qoder 回了话，但里面没有令牌")
                write_credential(tokens)
                log.info("qoder: device-code login stored (token not printed) keys=%s", sorted(tokens))
                return {"user_id": tokens.get("user_id") or "", "expires_at": tokens.get("expires_at")}
            last = f"HTTP {res.status_code}"
            time.sleep(_POLL_SECONDS)
    raise RuntimeError(f"浏览器里那一步没在 {int(timeout)} 秒内完成（最后一次：{last}）")
