"""One-time OAuth bootstrap for Antigravity (Google Gemini Code Assist).

Why this exists
---------------
quota_hub needs a Google *refresh token* to mint live ``ya29.`` access tokens for the
Cloud Code API. Antigravity stores that token in its own Secure-Enclave-encrypted
Keychain entry, which cannot be extracted headlessly. So we perform a normal Google
OAuth consent in the user's browser (using Antigravity's own public OAuth client)
and capture the refresh token ourselves.

The mechanics — consent URL, loopback callback, code exchange, refresh — live in one
place now (`quota_hub.google_oauth`), because the Gemini CLI leg needs the identical
flow with a different client pair and a different file to write. What stays here is
this product's own business: where its client pair comes from (`account_store`, never
the repo) and what it does with the tokens.

Run it with::

    quota_hub auth antigravity
"""

from __future__ import annotations

import logging
import os
import secrets
import sys
from typing import Callable

import httpx

from .... import google_oauth
from ....egress import proxy_url
from . import account_store
from .network import LOGIN_ENDPOINTS, probe_network

#: Same knob the leg itself reads, so a login and the traffic it enables leave the same way.
PROXY_ENV = "QUOTA_HUB_ANTIGRAVITY_PROXY"

OAUTH_AUTH_URL = google_oauth.OAUTH_AUTH_URL
OAUTH_TOKEN_URL = google_oauth.OAUTH_TOKEN_URL
OAUTH_SCOPES = list(google_oauth.CODE_ASSIST_SCOPES)

#: The host used for loadCodeAssist project discovery during bootstrap.
LOAD_CODE_ASSIST_HOST = "https://daily-cloudcode-pa.googleapis.com"

#: The callback shape this client is registered with — learned by reading a working
#: implementation of the same flow (`router-for-me/CLIProxyAPI`,
#: `internal/auth/antigravity/{constants.go,auth.go}`, verified 2026-10-28), not guessed:
#: fixed loopback port, path `/oauth-callback`, and **no PKCE at all** — their consent URL
#: sends `access_type/client_id/prompt/redirect_uri/response_type/scope/state` only, and
#: the token exchange sends no `code_verifier`. Our own invented shape (random port +
#: S256 challenge) was one more way for this click to fail.
CALLBACK_PORT = 51121
CALLBACK_PATH = "/oauth-callback"


def client_credentials() -> tuple[str, str]:
    pair = account_store.client_credentials()
    if not pair[0] or not pair[1]:
        raise RuntimeError(
            "Antigravity 的登录客户端还没在这台机器上配好。它在别处已经登录过一次的话，"
            "那份配置会跟着过来；否则这一次点击走不下去。"
        )
    return pair


def build_auth_url(redirect_uri: str) -> tuple[str, str]:
    """Return (authorization_url, pkce_verifier)."""
    client_id, _ = client_credentials()
    return google_oauth.auth_url(client_id, redirect_uri, OAUTH_SCOPES)


def exchange_code(verifier: str, code: str, redirect_uri: str) -> dict:
    """Exchange an authorization code for tokens. Returns the JSON body."""
    client_id, client_secret = client_credentials()
    proxy, _reason = resolve_proxy()
    return google_oauth.exchange(client_id, client_secret, code, verifier, redirect_uri, proxy=proxy)


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
def resolve_proxy() -> tuple[str, str]:
    """The egress this flow must use — the same node the leg itself uses.

    Measured 2026-10-28: when the shared flow was extracted, this leg lost its proxy and
    the code exchange went out **direct** — on this network that never reaches Google, and
    the user did the whole browser step twice for a `401`. The consent URL, the loopback
    callback and the token exchange all have to leave through the same door the leg uses.
    """
    explicit = str(os.environ.get(PROXY_ENV) or "").strip()
    if explicit:
        return explicit, "ok"
    return proxy_url()


def run_bootstrap(*, print_token: bool = False, on_url: Callable[[str], None] | None = None) -> str:
    """Full bootstrap: start local callback, open browser, exchange code, store account."""
    network = probe_network(LOGIN_ENDPOINTS)
    if not network["ok"]:
        # What reaches the screen is the CLI's one-line `[quota_hub auth] …` sentence, because
        # the desktop uses this child's whole stderr as the error text (measured 2026-10-28:
        # the printed `UNEXPECTED_EOF_WHILE_READING` lines showed up in front of the user).
        # The per-endpoint detail is already logged by the periodic probe, so nothing here
        # repeats it.
        raise RuntimeError(network["advice"])

    client_id, client_secret = client_credentials()
    proxy, proxy_reason = resolve_proxy()
    if proxy_reason != "ok":
        raise RuntimeError(
            "登录要走 Google，得先在设置里那个 VPN 里有一个可用的节点"
            if proxy_reason == "no_proxy"
            else "设置里的 VPN 节点现在连不上，登录这一步过不去"
        )
    tokens = google_oauth.sign_in(
        client_id=client_id, client_secret=client_secret,
        scopes=OAUTH_SCOPES, proxy=proxy,
        use_pkce=False,
        redirect_host="localhost",
        redirect_path=CALLBACK_PATH,
        preferred_port=CALLBACK_PORT,
        on_url=on_url,
    )

    refresh = tokens.get("refresh_token")
    if not refresh:
        # A one-shot CLI run dies with a message; a click must not: an empty error string on
        # the settings card is the silent degradation he rules out. This state is Google
        # handing us an access token but no way to renew it, which means he has to consent
        # again — a sentence he can act on, not a stack trace.
        raise RuntimeError("Google 这一趟没有给可续用的登录凭据，需要再点一次并在浏览器里重新授权")

    access = tokens.get("access_token")
    logging.getLogger("quota_hub").info("antigravity: discovering the Code Assist project id")
    project = discover_project(access) if access else ""
    user = fetch_userinfo(access) if access else {}
    email = str(user.get("email") or "").strip()

    # Persist the token before printing anything: the capability is now ours,
    # not a value the user has to paste into a tracked config file.
    account_store.save_client(client_id, client_secret)
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
