"""One click → Google consent → the Gemini CLI's own credential file.

Why Plobi runs this flow instead of running `gemini`'s login (all measured on this
machine, 2026-10-28, with the installed 0.65.0):

  * `gemini --reauth` **does not exist** — it printed usage and exited 41. The hub used to
    spawn exactly that, then scraped the first URL out of the usage text, which is a
    YouTube link the CLI ships in its own `--yolo` help line. So a click on that card
    opened a video instead of a sign-in.
  * with no auth method configured the CLI refuses to start at all:
    `Please set an Auth method in ... settings.json or specify one of ... GEMINI_API_KEY,
    GOOGLE_GENAI_USE_VERTEXAI, GOOGLE_GENAI_USE_GCA` (exit 41),
  * and the CLIs that do have a login command (`cline auth`, `qwen --auth-type=...`)
    refuse to run it without a TTY, so nothing we can drive from a button is waiting on
    the other end of that pipe.

The Google flow underneath, though, is a stock OAuth 2.0 code flow with a loopback
redirect, and the client pair it uses is shipped **inside the CLI the user installed**.
So we discover that pair at run time from that bundle rather than hardcoding someone's
copy of it: nothing lands in git, and the consent screen he sees is Google's own, for
Gemini CLI's own client — the same one his terminal would have used.

Where the result goes: `~/.gemini/oauth_creds.json`, the file the CLI itself writes, in
the shape it itself reads (`access_token` / `refresh_token` / `expiry_date` in ms). One
credential, one owner-visible place, and the hub's leg reads exactly what the CLI would.
We print no token, ever.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import Callable

from ....egress import proxy_url
from .... import google_oauth
from . import api as _api

log = logging.getLogger("quota_hub")

CLIENT_ID_ENV = "PLOBI_GEMINI_OAUTH_CLIENT_ID"
CLIENT_SECRET_ENV = "PLOBI_GEMINI_OAUTH_CLIENT_SECRET"
#: Where the installed CLI keeps its own OAuth client pair, relative to the global
#: node_modules root that `which gemini` sits next to.
_BUNDLE_TAILS = (
    "node_modules/@google/gemini-cli/bundle",
    "lib/node_modules/@google/gemini-cli/bundle",
)
_CONST_RE = {
    "client_id": re.compile(r'var OAUTH_CLIENT_ID = "([^"]{6,200})"'),
    "client_secret": re.compile(r'var OAUTH_CLIENT_SECRET = "([^"]{6,200})"'),
}
#: Measured in the same bundle: `var OAUTH_SCOPE = [...]`. Requesting more than the
#: client is registered for is how a consent screen turns into an `invalid_scope` page.
_SCOPES_RE = re.compile(r"var OAUTH_SCOPE = \[(.*?)\]", re.S)
_DEFAULT_SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]
_DISCOVERED: tuple[str, str] | None = None
_SCOPES: list[str] | None = None


def _candidate_dirs() -> list[Path]:
    binary = shutil.which("gemini")
    if not binary:
        return []
    root = Path(binary).parent
    return [root / tail for tail in _BUNDLE_TAILS]


def _scan_bundle() -> dict[str, str]:
    """The OAuth constants inside the installed CLI's own bundle (empty when absent)."""
    values: dict[str, str] = {}
    for directory in _candidate_dirs():
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.js")):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for key, pattern in _CONST_RE.items():
                if key not in values:
                    found = pattern.search(text)
                    if found:
                        values[key] = found.group(1)
            if "scopes" not in values:
                found = _SCOPES_RE.search(text)
                if found:
                    values["scopes"] = found.group(1)
        if len(values) == 3:
            break
    return values


def discover_client() -> tuple[str, str] | None:
    """(client_id, client_secret) as shipped by the installed Gemini CLI.

    Env first, then the CLI's own bundle. Returns None when neither has it: that is a
    real state (a CLI that ships no OAuth client, or one we cannot find), and the caller
    must say so instead of inventing a pair.
    """
    global _DISCOVERED
    if _DISCOVERED is not None:
        return _DISCOVERED
    env_id = os.environ.get(CLIENT_ID_ENV, "").strip()
    env_secret = os.environ.get(CLIENT_SECRET_ENV, "").strip()
    if env_id and env_secret:
        _DISCOVERED = (env_id, env_secret)
        return _DISCOVERED
    values = _scan_bundle()
    if "client_id" not in values or "client_secret" not in values:
        return None
    _DISCOVERED = (values["client_id"], values["client_secret"])
    return _DISCOVERED


def discover_scopes() -> list[str]:
    """What this CLI asks Google for. Its own list, not ours, so consent looks familiar."""
    global _SCOPES
    if _SCOPES is not None:
        return _SCOPES
    raw = _scan_bundle().get("scopes") or ""
    found = re.findall(r'"(https://www\.googleapis\.com/auth/[a-z_.-]+)"', raw)
    _SCOPES = found or list(_DEFAULT_SCOPES)
    return _SCOPES


def reset_discovery_cache() -> None:
    global _DISCOVERED, _SCOPES
    _DISCOVERED = None
    _SCOPES = None


def write_credential(tokens: dict, *, store: Path | None = None) -> Path:
    """Merge Google's answer into the CLI's own credential file.

    Unknown keys already in that file are kept: gemini-cli stores more than the token
    (project id, credits), and a login through Plobi must not cost him that.
    """
    path = store or _api.store_path()
    data: dict = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, ValueError):
            data = {}
    expires_in = float(tokens.get("expires_in") or 0.0)
    data.update({
        "access_token": str(tokens.get("access_token") or ""),
        "refresh_token": str(tokens.get("refresh_token") or data.get("refresh_token") or ""),
        "token_type": str(tokens.get("token_type") or "Bearer"),
        "scope": str(tokens.get("scope") or data.get("scope") or ""),
        "expiry_date": int((time.time() + expires_in) * 1000),
    })
    if tokens.get("id_token"):
        data["id_token"] = str(tokens["id_token"])
    if not data["access_token"]:
        raise RuntimeError("Google 没有回令牌")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def proxy_for_leg() -> tuple[str, str]:
    explicit = os.environ.get("QUOTA_HUB_GEMINI_PROXY", "").strip()
    if explicit:
        return explicit, "ok"
    return proxy_url()


def run_login(on_url: Callable[[str], None] | None = None) -> dict:
    """The click path: consent in his browser, credential in the CLI's own file."""
    pair = discover_client()
    if pair is None:
        raise RuntimeError("这台机器上的 Gemini CLI 没有给出它的登录客户端，这条路走不通")
    proxy, reason = proxy_for_leg()
    if reason != "ok":
        raise RuntimeError(
            "登录要走 Google，得先在设置里那个 VPN 里有一个可用的节点"
            if reason == "no_proxy"
            else "设置里的 VPN 节点现在连不上"
        )
    tokens = google_oauth.sign_in(
        client_id=pair[0],
        client_secret=pair[1],
        scopes=discover_scopes(),
        proxy=proxy,
        # No PKCE: the CLI's own `authWithWeb` sends none, so this request is the same
        # one his terminal would have sent.
        use_pkce=False,
        # The loopback path is the CLI's own — read out of the installed bundle
        # (`http://127.0.0.1:${port}/oauth2callback`, measured 2026-10-29 in
        # chunk-DKZARV6U.js). Google does **not** match loopback origins regardless of
        # path: with our own `/callback` the consent completed and the redirect landed
        # on an address nobody was listening on — the login only timed out
        # ("浏览器里没有完成这次登录（300s 内没等到回调）").
        redirect_path="/oauth2callback",
        on_url=on_url,
    )
    write_credential(tokens)
    log.info("gemini: signed in through the CLI's own Google client (token not printed)")
    return {"refresh_token": bool(tokens.get("refresh_token"))}


def refresh_credential() -> bool:
    """Mint a fresh access token into the same file. False when there is nothing to do."""
    current = _api.read_credential()
    if current is None:
        return False
    refresh_token = str(current.get("refresh_token") or "")
    if not refresh_token:
        return False
    pair = discover_client()
    if pair is None:
        return False
    proxy, _reason = proxy_for_leg()
    try:
        tokens = google_oauth.refresh(pair[0], pair[1], refresh_token, proxy=proxy)
    except Exception as exc:  # noqa: BLE001 — a failed refresh is a health verdict, not a crash
        log.warning("gemini: token refresh refused: %s", type(exc).__name__)
        return False
    tokens.setdefault("refresh_token", refresh_token)
    write_credential(tokens)
    return True
