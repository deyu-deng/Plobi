"""Gemini (Google account, via the Gemini CLI's own OAuth) as a tool-carrying leg.

The shape is the one that already works for CodeBuddy and Antigravity (裁定 204): read the
product's **own** local credential, call the vendor HTTP API that credential is valid for,
and hand back OpenAI `tool_calls` — the only shape an agent loop can drive.

Which API that credential is valid for is the whole lesson of this file. Measured 2026-10-28
in the installed 0.65.0 bundle: `oauth-personal` sets
`CODE_ASSIST_ENDPOINT = https://cloudcode-pa.googleapis.com` and
`CODE_ASSIST_API_VERSION = v1internal`. A Google-account token is a **Code Assist** token, so
the earlier version of this leg — `generativelanguage.googleapis.com/v1beta` — was the wrong
door: no matter how well the answer was translated, the call could not have succeeded. It now
shares one `CloudCodeClient` implementation with the Antigravity leg, and the only per-leg
inputs are the credential's location, the host, and the client identity the product reports.

`~/.gemini/` on this machine holds `config/`, `history/`, `installation_id`, `projects.json`
and **no credential file** — so the leg stays dark (no card claim of a login, no models)
until the owner signs in once from the settings card. The reader is tolerant about key names
because the shape can only be confirmed after that sign-in; anything it cannot parse is "no
credential", never "signed in".

**No TUN needed (裁定 206 follow-up)**: the hub's shared client runs `trust_env: false`
(2026-10-14: a dead local proxy had poisoned every upstream), so this leg resolves its own
egress — Settings → VPN's node, or `providers.gemini_api.proxy` / `QUOTA_HUB_GEMINI_PROXY` —
and uses it only for this leg. A dead node then breaks one leg with its own reason while the
rest of the hub stays direct.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx

from ....egress import proxy_url
from ....providers.base import Account, Capabilities, Credential, Provider, UpstreamError
from ....providers.cloudcode import CloudCodeClient

log = logging.getLogger("quota_hub")

STORE_ENV = "QUOTA_HUB_GEMINI_STORE"
#: Measured in the installed CLI 2026-10-28 (`chunk-DKZARV6U.js`): the Code Assist endpoint
#: and api version it drives with an `oauth-personal` token. That token is a Code Assist
#: credential — it has no business at `generativelanguage.googleapis.com`, which is the
#: API-key door. This leg used to point there, which is why it could never have served a
#: turn however carefully the answer was translated.
CLOUDCODE_HOST = "https://cloudcode-pa.googleapis.com"
API_VERSION = "v1internal"
#: The one client identity measured from that bundle (`pluginType: "GEMINI"`). Everything
#: else about this product's envelope is configuration, not a guess: override with
#: `client_metadata` / `headers` / `user_agent` / `request_type` in config.yaml, and treat
#: the shape as unverified against a live token until the owner has signed in once.
CLIENT_METADATA = {"pluginType": "GEMINI"}
#: (There is no default model list on purpose: `fetchAvailableModels` is the only name
#: source — 裁定 204.5.)


def store_path() -> Path:
    explicit = os.environ.get(STORE_ENV, "").strip()
    if explicit:
        return Path(explicit)
    return Path.home() / ".gemini" / "oauth_creds.json"


def read_credential() -> dict[str, Any] | None:
    path = store_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("gemini credential file unreadable (%s); treating it as absent", type(exc).__name__)
        return None
    if not isinstance(data, dict):
        return None
    token = str(data.get("access_token") or data.get("access") or "").strip()
    if not token:
        return None
    # `expiry_date` is milliseconds since the epoch (the CLI's own field); accept
    # seconds too so a hand-written file does not read as expired forever.
    raw = data.get("expiry_date") or data.get("expires_at") or data.get("expires") or 0
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 0.0
    expires_at = value / 1000.0 if value > 1e11 else value
    return {
        "access_token": token,
        "refresh_token": str(data.get("refresh_token") or ""),
        "expires_at": expires_at,
    }


def _refreshable(data: dict[str, Any]) -> bool:
    """Can we mint a new access token ourselves? Needs both halves of the CLI's own flow."""
    if not str(data.get("refresh_token") or ""):
        return False
    from . import oauth as _oauth

    return _oauth.discover_client() is not None


def egress_proxy() -> tuple[str, str]:
    """Kept as the name this leg's tests use; the resolver lives in `quota_hub/egress.py`."""
    return proxy_url()


class GeminiApiProvider(Provider):
    name = "gemini_api"
    display_name = "Gemini CLI"
    default_capabilities = Capabilities(stream=True, tools=True)

    def __init__(self, config: dict, http) -> None:
        super().__init__(config, http)
        self.host = str(config.get("host") or CLOUDCODE_HOST).rstrip("/")
        self.api_version = str(config.get("api_version") or API_VERSION)
        self.client_metadata = dict(config.get("client_metadata") or CLIENT_METADATA)
        self.thinking = bool(config.get("thinking", False))
        self.project_id = str(config.get("project_id") or "")
        self.MODEL_MAP = dict(config.get("model_map") or {})
        self.timeout = float(config.get("timeout", 180.0))
        self.served_models: tuple[str, ...] = tuple(config.get("models") or ())
        # Per-leg proxy: only Google traffic goes through it (see the module header).
        explicit = str(config.get("proxy") or os.environ.get("QUOTA_HUB_GEMINI_PROXY") or "").strip()
        self.proxy_reason = "ok"
        if explicit:
            self.proxy = explicit
        else:
            # 裁定 206：设置里那个 VPN 节点就是全 Plobi 的出口，腿不用再单独填一次。
            shared, self.proxy_reason = egress_proxy()
            self.proxy = shared
        # One client for this leg, pointed at the node — and only when a node is in play.
        # Without one the leg rides the hub's shared client, so a dead proxy breaks this leg
        # with its own reason instead of every upstream at once.
        self.client = http or httpx.AsyncClient(timeout=httpx.Timeout(self.timeout, connect=15.0))
        # Bookkeeping for `_rebind_node` (see the antigravity leg for the same shape):
        # was the node pinned by hand, which client did we create, where are we bound.
        self._explicit_proxy = bool(explicit)
        self._bound_proxy = self.proxy
        self._own_client = bool(self.proxy)
        self._proxy_ttl = 30.0
        if self.proxy:
            self.client = httpx.AsyncClient(
                proxy=self.proxy, timeout=httpx.Timeout(self.timeout, connect=15.0)
            )
            log.info("[gemini_api] using an explicit proxy for this leg only")
        self.cloudcode = CloudCodeClient(
            self.client,
            host=self.host,
            api_version=self.api_version,
            headers=dict(config.get("headers") or {}),
            client_metadata=self.client_metadata,
            user_agent=str(config.get("user_agent") or "GeminiCLI"),
            request_type=str(config.get("request_type") or "chat_assistant"),
            extra_headers=dict(config.get("extra_headers") or {}),
            thinking=self.thinking,
            timeout=self.timeout,
            label="gemini_api",
        )

    # --- presence / credential -------------------------------------------
    def present(self) -> bool:
        """This leg is on the machine when its CLI is — a credential is the *next* step.

        Conflating the two hid the card entirely on a machine with `gemini` installed and
        nobody signed in yet (`present()` gated the settings list, `credential_health()`
        was already the thing that said "not signed in"). The scan result decides what
        appears; only a verdict the user can act on decides what stays out of the picker.
        """
        if read_credential() is not None:
            return True
        return shutil.which(str(self.config.get("binary", "gemini"))) is not None

    def login_plan(self) -> dict:
        # Plobi runs the CLI's own Google consent, because the CLI's own login cannot be
        # driven from a button (its argv is measured dead in `oauth.py`'s module header).
        from . import oauth as _oauth

        return {
            "kind": "oauth",
            "name": self.display_name,
            "runner": lambda on_url=None: _oauth.run_login(on_url=on_url),
        }

    def signed_in(self) -> tuple[bool, str]:
        """Credential only — the node is a separate question (see `Provider.signed_in`)."""
        data = read_credential()
        if data is None:
            return False, "no_desktop_credential"
        expires = float(data.get("expires_at") or 0.0)
        if expires and expires <= time.time() and not _refreshable(data):
            # A short-lived access token is not a dead leg: the CLI's own credential
            # carries a refresh token, and `ensure_fresh()` mints a new access token with
            # it. Saying "expired" here would drop a working model out of the picker an
            # hour after he signed in — the exact thing he ruled out for the next restart.
            return False, "credential_expired"
        return True, "ok"

    def credential_health(self) -> tuple[bool, str]:
        live, reason = self.signed_in()
        if not live:
            return False, reason
        # Once a sign-in exists, "no node / node down" is the reason a turn cannot run —
        # and it must not be reported as a missing login.
        if self.proxy_reason != "ok":
            return False, self.proxy_reason
        return True, "ok"

    # --- accounts / models ------------------------------------------------
    async def _discovered_models(self, token: str) -> list[str]:
        """The names the account is entitled to, from Code Assist itself (裁定 204.5).

        No default table: this leg used to read `generativelanguage /models`, which is a
        different product's door — a Google-account (Code Assist) token does not work there,
        so the leg could never have served a turn no matter how carefully the answer was
        translated. `fetchAvailableModels` is the one name source that matches the token.
        """
        project = ""
        try:
            project = await self.cloudcode.load_project(token)
        except Exception:  # noqa: BLE001 — project is best-effort, the list is not
            project = ""
        self.project_id = project
        try:
            short = await self.cloudcode.available_models(token, project)
        except UpstreamError as exc:
            log.warning("[gemini_api] model list refused: %s", exc)
            return []
        return [f"{self.name}/{m}" for m in short]

    async def _rebind_node(self) -> None:
        """Follow the Settings → VPN node when it comes up after boot.

        Both the verdict and the HTTP client were frozen at construction, so starting the
        node *after* the hub was up left this leg reading `proxy_unreachable` — and, worse,
        sending turns direct even once the card said ok. Re-resolved before a turn and
        after a sign-in (the two moments it matters), cached for `_proxy_ttl`.
        """
        if self._explicit_proxy:
            return
        now = time.time()
        cached = getattr(self, "_proxy_seen", None)
        if cached and now - cached[0] < self._proxy_ttl:
            return
        shared, reason = egress_proxy()
        self._proxy_seen = (now, reason)
        self.proxy_reason = reason
        if shared == self._bound_proxy:
            return
        old = self.client
        self.client = httpx.AsyncClient(proxy=shared or None, timeout=httpx.Timeout(self.timeout, connect=15.0))
        self.proxy = shared
        self._bound_proxy = shared
        self.cloudcode.http = self.client
        if self._own_client:
            try:
                await old.aclose()
            except Exception:  # noqa: BLE001 — a closed client is not a failed rebind
                pass
        self._own_client = True
        log.info("[gemini_api] egress node changed; HTTP client rebound (reason=%s)", reason)

    async def discover_accounts(self) -> list[Account]:
        # Runs at startup and again right after a sign-in — the two moments the node may
        # have changed under us (he starts the VPN, then clicks).
        await self._rebind_node()
        live, _reason = self.signed_in()
        data = read_credential()
        if not live or data is None:
            self.accounts = []
            self.served_models = ()
            return []
        token = str(data.get("access_token") or "")
        self.accounts = [
            Account(
                id=f"{self.name}-default",
                provider=self.name,
                label=self.display_name,
                cred=Credential(
                    access_token=token,
                    refresh_token=str(data.get("refresh_token") or ""),
                    expires_at=float(data.get("expires_at") or 0.0),
                    extra={"session_id": f"plobi-{self.name}"},
                ),
            )
        ]
        self.served_models = tuple(await self._discovered_models(token)) if self.credential_health()[0] else ()
        return self.accounts

    async def ensure_fresh(self, acc: Account) -> None:
        # Before a turn is the moment the node matters: if it came up (or went away) since
        # the last check, the client is rebound here instead of sending the turn direct.
        await self._rebind_node()
        if not acc.cred.is_expired():
            return
        from . import oauth as _oauth

        if _oauth.refresh_credential():
            data = read_credential() or {}
            acc.cred.access_token = str(data.get("access_token") or acc.cred.access_token)
            acc.cred.expires_at = float(data.get("expires_at") or 0.0)
            if data.get("refresh_token"):
                acc.cred.refresh_token = str(data["refresh_token"])
            return
        raise UpstreamError(401, "gemini 这条腿的登录已经用不动了，需要在设置里重新点一次", reauth=True)

    # --- the turn ----------------------------------------------------------
    def _short_model(self, oai_model: str) -> str:
        return self.MODEL_MAP.get(oai_model) or (oai_model.split("/", 1)[1] if "/" in oai_model else oai_model)

    async def chat(self, acc: Account, oai_req: dict) -> dict:
        project = acc.cred.extra.get("project_id") or self.project_id or await self.cloudcode.load_project(
            acc.cred.access_token
        )
        return await self.cloudcode.chat(
            token=acc.cred.access_token,
            project=project,
            model=self._short_model(str(oai_req.get("model") or "")),
            oai_req=oai_req,
            session_id=str(acc.cred.extra.get("session_id") or ""),
        )

    async def chat_stream(self, acc: Account, oai_req: AsyncIterator | dict) -> AsyncIterator[dict]:
        project = self.project_id or await self.cloudcode.load_project(acc.cred.access_token)
        async for chunk in self.cloudcode.chat_stream(
            token=acc.cred.access_token,
            project=project,
            model=self._short_model(str(oai_req.get("model") or "")),
            oai_req=oai_req,
            session_id=str(acc.cred.extra.get("session_id") or ""),
        ):
            yield chunk
