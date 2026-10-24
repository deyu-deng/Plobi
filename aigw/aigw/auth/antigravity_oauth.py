"""One-time OAuth bootstrap for Antigravity (Google Gemini Code Assist).

Why this exists
---------------
aigw needs a Google *refresh token* to mint live ``ya29.`` access tokens for the
Cloud Code API. Antigravity stores that token in its own Secure-Enclave-encrypted
Keychain entry, which cannot be extracted headlessly. So we perform a normal Google
OAuth consent in the user's browser (using Antigravity's own public OAuth client)
and capture the refresh token ourselves.

Run it with::

    aigw auth antigravity

It prints a composite ``refreshToken|projectId|managedProjectId`` string you drop
into config.yaml (or export as ``ANTIGRAVITY_REFRESH_TOKEN``). The project id is
discovered automatically via loadCodeAssist so the adapter needs zero extra config.

This uses PKCE, ``access_type=offline``, and a **local HTTP callback server**
(``http://127.0.0.1:<port>/callback``) to capture the authorization code
automatically — no copy/paste needed.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import socket
import sys
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import httpx

from . import account_store

# The Antigravity (Google Gemini Code Assist) OAuth client credentials are not
# hardcoded in this repository. `client_credentials()` reads them from the
# environment, or from the owner-only file written by a previous bootstrap.
OAUTH_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"

# scopes required by Code Assist (cloud-platform is what enables the API)
OAUTH_SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/cclog",
    "https://www.googleapis.com/auth/experimentsandconfigs",
]

# The host used for loadCodeAssist project discovery during bootstrap.
LOAD_CODE_ASSIST_HOST = "https://daily-cloudcode-pa.googleapis.com"


# --------------------------------------------------------------------------
# PKCE + URL building
# --------------------------------------------------------------------------
def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _pkce() -> tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def _find_free_port() -> int:
    """Bind to port 0 to let OS assign a free port, then release it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def client_credentials() -> tuple[str, str]:
    pair = account_store.client_credentials()
    if not pair[0] or not pair[1]:
        raise RuntimeError(
            "Antigravity OAuth client pair is missing. Set GOOGLE_CC_CLIENT_ID and "
            "GOOGLE_CC_CLIENT_SECRET once, then run `aigw auth antigravity` again. "
            "The pair is stored in an owner-only file outside git after the first run."
        )
    return pair


def build_auth_url(redirect_uri: str) -> tuple[str, str]:
    """Return (authorization_url, pkce_verifier)."""
    verifier, challenge = _pkce()
    client_id, _client_secret = client_credentials()
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(OAUTH_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": secrets.token_hex(16),
    }
    url = OAUTH_AUTH_URL + "?" + urllib.parse.urlencode(params)
    return url, verifier


# --------------------------------------------------------------------------
# Local HTTP callback server (captures auth code automatically)
# --------------------------------------------------------------------------
class _CallbackHandler(BaseHTTPRequestHandler):
    """Single-request handler: extracts ?code=... from the redirect, stores it,
    replies with an HTML page, and shuts down the server."""

    # class-level shared state (set before server starts)
    auth_code: str | None = None
    error: str | None = None

    def log_message(self, format: str, *args) -> None:
        pass  # suppress request logging noise

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/callback":
            self.send_error(404)
            return
        qs = parse_qs(parsed.query)
        codes = qs.get("code", [])
        errors = qs.get("error", [])

        if errors:
            _CallbackHandler.error = errors[0]
            html = self._error_page(errors[0])
        elif codes:
            _CallbackHandler.auth_code = codes[0]
            html = self._success_page()
        else:
            _CallbackHandler.error = "no_code_or_error_in_callback"
            html = self._error_page("No authorization code returned")

        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _success_page() -> str:
        return """<!doctype html>
<html><head><title>✅ Auth Success</title>
<style>body{font-family:-apple-system,sans-serif;display:flex;justify-content:center;
align-items:center;height:100vh;margin:0;background:#f0fdf4;color:#166534}
.box{text-align:center;padding:3rem;border-radius:16px;box-shadow:0 8px 30px rgba(0,0,0,.08)}
h1{margin:0 0 .5rem;font-size:1.6rem}p{color:#4ade80;margin:.5rem 0}</style></head>
<body><div class="box">
<h1>&#x2705; Authorization successful</h1>
<p>You may close this tab/window.</p>
<p style="font-size:.85rem;color:#86efac">The gateway has received your code &#x2014; returning to terminal...</p>
</div></body></html>"""

    @staticmethod
    def _error_page(err: str) -> str:
        escaped = err.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return f"""<!doctype html>
<html><head><title>&#x274C; Auth Error</title>
<style>body{{font-family:-apple-system,sans-serif;display:flex;justify-content:center;
align-items:center;height:100vh;margin:0;background:#fef2f2;color:#991b1b}}
.box{{text-align:center;padding:3rem;border-radius:16px;box-shadow:0 8px 30px rgba(0,0,0,.08)}}
h1{{margin:0 0 .5rem;font-size:1.6rem}}p{{color:#fca5a5;margin:.5rem 0}}</style></head>
<body><div class="box">
<h1>&#x274C; Authorization failed</h1>
<p>Error: {escaped}</p>
<p style="font-size:.85rem;color:#fecaca">Check the terminal for details.</p>
</div></body></html>"""


def _run_callback_server(port: int, timeout: int = 120) -> str | None:
    """Start local HTTP server, wait for exactly one callback, return code or None."""
    _CallbackHandler.auth_code = None
    _CallbackHandler.error = None
    srv = HTTPServer(("127.0.0.1", port), _CallbackHandler)
    srv.timeout = 1  # for periodic check below

    print(f"  Callback listening on http://127.0.0.1:{port}/callback")
    print(f"  Waiting up to {timeout}s for you to complete sign-in...\n")

    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        srv.handle_request()  # blocks up to 1s per call
        if _CallbackHandler.auth_code is not None:
            print("  [callback] authorization code received!")
            srv.server_close()
            return _CallbackHandler.auth_code
        if _CallbackHandler.error is not None:
            print(f"  [callback] error: {_CallbackHandler.error}", file=sys.stderr)
            srv.server_close()
            return None

    srv.server_close()
    print("  [callback] timed out waiting for authorization", file=sys.stderr)
    return None


# --------------------------------------------------------------------------
# token exchange + project discovery
# --------------------------------------------------------------------------
def exchange_code(verifier: str, code: str, redirect_uri: str) -> dict:
    """Exchange an authorization code for tokens. Returns the JSON body."""
    client_id, client_secret = client_credentials()
    body = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "code_verifier": verifier,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }
    r = httpx.post(OAUTH_TOKEN_URL, data=body, timeout=30.0)
    if not r.is_success:
        detail = r.text
        print(f"  [token-exchange:error] Google returned {r.status_code}: {detail}", file=sys.stderr)
    r.raise_for_status()
    return r.json()


def discover_project(access_token: str) -> str:
    """Call loadCodeAssist to learn the user's Code Assist project id."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "x-goog-api-client": "gl-node/18.18.2 fire/0.8.6 grpc/1.10.x",
        "X-Client-Name": "antigravity",
        "X-Client-Version": "1.107.0",
    }
    body = {"metadata": {"ideType": 9, "pluginType": 2, "platform": 3}, "mode": 1}
    try:
        r = httpx.post(
            f"{LOAD_CODE_ASSIST_HOST}/v1internal:loadCodeAssist",
            headers=headers,
            json=body,
            timeout=20.0,
        )
    except Exception:
        return ""
    if r.status_code != 200:
        return ""
    try:
        data = r.json()
    except Exception:
        return ""
    proj = data.get("cloudaicompanionProject")
    if isinstance(proj, dict):
        proj = proj.get("id", "")
    return proj or ""


def fetch_userinfo(access_token: str) -> dict:
    """Best-effort e-mail/name for the account row; never needed for the token itself."""
    try:
        r = httpx.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15.0,
        )
    except Exception:
        return {}
    if r.status_code != 200:
        return {}
    try:
        data = r.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


# --------------------------------------------------------------------------
# end-to-end bootstrap
# --------------------------------------------------------------------------
def run_bootstrap(*, print_token: bool = False) -> str:
    """Full bootstrap: start local callback, open browser, exchange code."""
    port = _find_free_port()
    redirect_uri = f"http://127.0.0.1:{port}/callback"

    url, verifier = build_auth_url(redirect_uri)

    print("\n" + "=" * 68)
    print("  Antigravity / Gemini Code Assist OAuth Setup")
    print("=" * 68)
    print("\nOpening browser for Google sign-in...")
    print("(If it doesn't open, paste this URL manually)\n")
    print(f"  {url}\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass  # URL already printed above

    # Start callback server to capture the code automatically
    code = _run_callback_server(port)
    if not code:
        msg = _CallbackHandler.error or "timed out"
        print(f"\n[FAILED] No authorization code captured: {msg}", file=sys.stderr)
        raise SystemExit(1)

    # Exchange code for tokens
    print("\nExchanging authorization code for tokens...")
    try:
        tokens = exchange_code(verifier, code, redirect_uri)
    except Exception as e:
        print(f"[FAILED] Token exchange failed: {e}", file=sys.stderr)
        raise SystemExit(1)

    refresh = tokens.get("refresh_token")
    if not refresh:
        print("[FAILED] Google did not return a refresh_token.", file=sys.stderr)
        print(
            "  Make sure consent was fresh (prompt=consent + access_type=offline).", file=sys.stderr
        )
        raise SystemExit(1)

    access = tokens.get("access_token")
    print("Discovering Cloud Code project id...")
    project = discover_project(access) if access else ""
    user = fetch_userinfo(access) if access else {}
    email = str(user.get("email") or "").strip()

    # Persist the token before printing anything: the capability is now ours,
    # not a value the user has to paste into a tracked config file.
    account_store.save_client(*client_credentials())
    account = account_store.upsert_account({
        "added_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "email": email,
        "id": f"antigravity-{secrets.token_hex(6)}",
        "name": str(user.get("name") or ""),
        "project_id": project,
        "refresh_token": refresh,
    })
    composite = f"{refresh}|{project}|"
    print("\n" + "=" * 68)
    print("  SUCCESS — Antigravity account stored")
    print("=" * 68)
    print(f"\n  account: {email or account['id']}")
    print(f"  project: {project or '(discovered at runtime)'}")
    print(f"  file:    {account_store.accounts_path()}")
    if print_token:
        print(f"\n  composite token (explicit --print-token):\n  {composite}\n")
    else:
        print("\n  Token is not printed. The gateway will read it from the file above.")
    return composite
