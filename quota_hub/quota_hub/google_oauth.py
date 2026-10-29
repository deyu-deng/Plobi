"""One Google OAuth code flow, shared by every leg that signs in with a Google account.

Why this is a module and not two copies: the Antigravity leg already ran its own PKCE +
loopback-callback flow (`providers/apps/antigravity/oauth.py`), and the Gemini leg needs
the same flow with a different client pair and a different place to put the result. The
mechanics — consent URL, single-request callback server, code exchange, refresh — are
identical, so they live here once.

What stays with each leg: **where the client pair comes from** and **what we persist**.
Neither is a decision this module may make.

Two boundaries this module keeps:
  * the token is never printed and never written into a tracked file — the caller stores
    it wherever its own product keeps it;
  * `on_url` is called with the consent URL before the browser is opened, so the caller
    can hand that URL to the desktop shell instead of relying on a browser that may not
    exist in a detached child process.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import socket
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable

import httpx

OAUTH_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"

#: Scopes the Code Assist / Gemini accounts consent to. `cloud-platform` is what makes
#: the API callable; the two userinfo scopes are what let us label the account row.
CODE_ASSIST_SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/cclog",
    "https://www.googleapis.com/auth/experimentsandconfigs",
]

Callback = Callable[[str], None]


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def pkce() -> tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def auth_url(client_id: str, redirect_uri: str, scopes: list[str], *, use_pkce: bool = True) -> tuple[str, str]:
    """(consent URL, pkce verifier) for one sign-in attempt.

    `use_pkce` is a real difference between products, not a knob for fun: the Antigravity
    client is driven with PKCE, while the Gemini CLI's own `authWithWeb` sends no code
    challenge at all (measured in its 0.65.0 bundle) and spends the installed client
    secret at exchange instead. A consent request that asks for a challenge the token
    exchange then has to remember is one more way for the click to fail.
    """
    verifier, challenge = pkce()
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "access_type": "offline",
        "prompt": "consent",
        "state": secrets.token_hex(16),
    }
    if use_pkce:
        params["code_challenge"] = challenge
        params["code_challenge_method"] = "S256"
    return OAUTH_AUTH_URL + "?" + urllib.parse.urlencode(params), verifier


class _Callback(BaseHTTPRequestHandler):
    """One redirect in, then done. Class-level state is set fresh before each run."""

    code: str | None = None
    error: str | None = None
    callback_path: str = "/callback"

    def log_message(self, format: str, *args) -> None:
        pass

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != _Callback.callback_path:
            self.send_error(404)
            return
        qs = urllib.parse.parse_qs(parsed.query)
        if qs.get("error"):
            _Callback.error = qs["error"][0]
            html = _page("Authorization failed", _Callback.error, bad=True)
        elif qs.get("code"):
            _Callback.code = qs["code"][0]
            html = _page("Authorization successful", "You may close this tab.", bad=False)
        else:
            _Callback.error = "no_code_in_callback"
            html = _page("Authorization failed", "No authorization code was returned.", bad=True)
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _page(title: str, line: str, *, bad: bool) -> str:
    from html import escape

    bg, fg = ("#fef2f2", "#991b1b") if bad else ("#f0fdf4", "#166534")
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<style>body{margin:0;min-height:100vh;display:flex;align-items:center;"
        f"justify-content:center;background:{bg};color:{fg};font-family:sans-serif}}"
        ".box{text-align:center;padding:2.5rem;border-radius:14px;max-width:32rem}"
        "h1{font-size:1.3rem;margin:0 0 .6rem}p{margin:0;font-size:.85rem;opacity:.75}"
        "</style><title>" + escape(title) + "</title></head><body><div class='box'>"
        "<h1>" + escape(title) + "</h1><p>" + escape(line) + "</p></div></body></html>"
    )


def wait_for_code(port: int, timeout: float = 300.0, *, path: str = "/callback") -> str:
    """Block until the browser hands us one code. Raises on error or timeout."""
    import time

    _Callback.code = None
    _Callback.error = None
    _Callback.callback_path = path
    server = HTTPServer(("127.0.0.1", port), _Callback)
    server.timeout = 1
    deadline = time.time() + timeout
    try:
        while time.time() < deadline:
            server.handle_request()
            if _Callback.code is not None:
                return _Callback.code
            if _Callback.error is not None:
                raise RuntimeError(f"Google returned “{_Callback.error}”")
    finally:
        server.server_close()
    raise TimeoutError(f"浏览器里没有完成这次登录（{timeout:.0f}s 内没等到回调）")


def _token_error(res: httpx.Response) -> str:
    """Name the failure the way it can be acted on, without echoing anything secret.

    A bare `HTTP 401` told the user nothing yesterday: it is equally a bad client pair
    (Google answers with `error=invalid_client`) and a *local* answer — a proxy or a PAC
    interception replying with HTML, in which case the client pair is fine and the door is
    the problem. Those two need opposite fixes, so the message says which one it saw.
    """
    code = ""
    try:
        body = res.json()
        if isinstance(body, dict):
            code = str(body.get("error") or "")
    except ValueError:
        code = ""
    if code:
        return f"Google 在这一步回了 {code}（HTTP {res.status_code}）"
    return f"这一步的答复不是 Google 给的（HTTP {res.status_code}）：先检查设置里那个 VPN 节点"


def exchange(client_id: str, client_secret: str, code: str, verifier: str,
             redirect_uri: str, *, proxy: str = "", timeout: float = 30.0) -> dict:
    body = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }
    if verifier:
        body["code_verifier"] = verifier
    with httpx.Client(proxy=proxy or None, timeout=timeout, trust_env=False) as client:
        res = client.post(OAUTH_TOKEN_URL, data=body)
    if not res.is_success:
        raise RuntimeError(_token_error(res))
    return res.json()


def refresh(client_id: str, client_secret: str, refresh_token: str,
            *, proxy: str = "", timeout: float = 30.0) -> dict:
    body = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    with httpx.Client(proxy=proxy or None, timeout=timeout, trust_env=False) as client:
        res = client.post(OAUTH_TOKEN_URL, data=body)
    if not res.is_success:
        raise RuntimeError(_token_error(res))
    return res.json()


def _reserve_port(preferred: int) -> int:
    """Take the product's registered callback port when it is free, else any free port.

    A fixed port is what the vendor's own client registration expects
    (`…/oauth-callback` on 51121 for Antigravity, learned from CLIProxyAPI's
    implementation, not invented here). Something else may already hold it — that is not a
    reason to fail the click, because Google matches loopback origins by host.
    """
    if not preferred:
        return free_port()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", preferred))
        except OSError:
            return free_port()
    return preferred


def sign_in(*, client_id: str, client_secret: str, scopes: list[str],
            proxy: str = "", timeout: float = 300.0, use_pkce: bool = True,
            redirect_host: str = "127.0.0.1", redirect_path: str = "/callback",
            preferred_port: int = 0, on_url: Callback | None = None) -> dict:
    """One full consent: URL → browser → loopback code → tokens. Returns Google's JSON.

    The caller persists whatever it needs; this module holds nothing after it returns.
    `use_pkce` / `redirect_host` / `redirect_path` / `preferred_port` exist because the
    products differ on all four and none of them is our invention — each leg states the
    shape its own CLI or client registration uses.
    """
    port = _reserve_port(preferred_port)
    redirect_uri = f"http://{redirect_host}:{port}{redirect_path}"
    url, verifier = auth_url(client_id, redirect_uri, scopes, use_pkce=use_pkce)
    # The hub opens the URL itself — measured 2026-10-29: when it only handed the URL to
    # the desktop (`on_url`) and left the opening to the shell, a click often produced **no
    # browser at all** (the desktop's catalog poll had to carry the URL over, and that
    # hand-off is not reliable). A second tab is a smaller problem than no tab.
    if on_url is not None:
        on_url(url)
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001 — the URL is handed to `on_url` above for a reason
        pass
    code = wait_for_code(port, timeout=timeout, path=redirect_path)
    return exchange(client_id, client_secret, code, verifier, redirect_uri, proxy=proxy)
