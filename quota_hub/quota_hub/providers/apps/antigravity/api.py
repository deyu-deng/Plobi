"""Antigravity (Google Gemini Code Assist) provider adapter.

Verified protocol (reverse-engineered from Antigravity's own ``language_server.log``
and cross-checked against the reference lucasliet/antigravity-proxy + Google gemini-cli
sources). This is NOT guesswork — every field below is what the real client sends.

Base hosts
----------
  daily : https://daily-cloudcode-pa.googleapis.com   (the app's default / sandbox)
  prod  : https://cloudcode-pa.googleapis.com

Endpoints (api_version ``v1internal``)
--------------------------------------
  POST /v1internal:loadCodeAssist          -> returns ``cloudaicompanionProject`` (project id)
  POST /v1internal:fetchAvailableModels
  POST /v1internal:generateContent
  POST /v1internal:streamGenerateContent?alt=sse

Auth
----
  Bearer OAuth2 access token; refreshed from a Google refresh token via
  oauth2.googleapis.com/token (the refresh token is the one the user obtains through
  the ``quota_hub auth antigravity`` bootstrap, or via env ANTIGRAVITY_REFRESH_TOKEN).

Request envelope (the "Cloud Code" wrapper the API actually expects)
--------------------------------------------------------------------
  {
    "project":    "<projectId | ''>",
    "model":      "<short model id, e.g. gemini-3-pro>",
    "request":    { "contents":[...], "systemInstruction":{...},
                    "generationConfig":{...}, "tools":[...], "sessionId":"..." },
    "userAgent":  "antigravity",
    "requestType":"agent",
    "requestId":  "agent-<uuid>"
  }

Key headers: Authorization, Content-Type, X-Client-Name, X-Client-Version,
x-goog-api-client (Google validates this), X-Machine-Session-Id, and for streaming
Accept: text/event-stream.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from collections.abc import AsyncIterator

from ....egress import proxy_url
from ....providers.cloudcode import CloudCodeClient
from . import account_store
from ....tokens import desktop_stores as ds
from ...base import Account, AccountState, Capabilities, Credential, Provider, UpstreamError

logger = logging.getLogger("quota_hub.antigravity")

# Antigravity's own client constants (from the reference proxy / gemini-cli).
DEFAULT_HOST_DAILY = "https://daily-cloudcode-pa.googleapis.com"
DEFAULT_HOST_PROD = "https://cloudcode-pa.googleapis.com"

# Quota summary (weekly + 5h buckets) — the capability we absorbed from
# Antigravity-Manager instead of depending on its GUI/API gateway.
QUOTA_SUMMARY_ENDPOINTS = (
    "https://daily-cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary",
    "https://daily-cloudcode-pa.sandbox.googleapis.com/v1internal:retrieveUserQuotaSummary",
    "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary",
)

# Headers the real Antigravity client sends. Google rejects the call without a
# believable x-goog-api-client, so we mirror the binary exactly.
ANTIGRAVITY_HEADERS = {
    "X-Client-Name": "antigravity",
    "X-Client-Version": "1.107.0",
    "x-goog-api-client": "gl-node/18.18.2 fire/0.8.6 grpc/1.10.x",
    "User-Agent": "antigravity/1.107.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}

# loadCodeAssist body metadata (ideType 9 = Antigravity, pluginType 2 = Gemini).
CLIENT_METADATA = {
    "ideType": 9,
    "pluginType": 2,
    "platform": 3,  # 3 = macOS in the proxy's platform enum
}


# Re-exported so the leg's own callers and tests keep their import path; the code now
# lives in `providers/cloudcode_wire.py`.
from ...cloudcode_wire import (  # noqa: F401
    _describe_google_error,
    _as_text,
    _to_gemini_tools,
    _extract_gemini_text,
    _extract_gemini_function_call,
    _gemini_to_oai,
    _chunk,
    _tool_chunk,
    _parse_antigravity_models,
)


class AntigravityProvider(Provider):
    name = "antigravity"
    display_name = "Antigravity"

    def login_plan(self) -> dict:
        """Google OAuth with a local callback — the flow this adapter already owns.

        `run_bootstrap` starts the loopback server, opens the browser itself, and
        stores the refresh token under PLOBI_HOME without printing it, which is the
        same boundary the desktop's existing Antigravity path relies on
        (`electron/main.ts` → `aigw auth antigravity`). Handing the callable to the
        hub's login manager keeps one click path for every leg while leaving the
        PKCE details here, where they belong.
        """
        from . import oauth as _oauth

        return {
            "kind": "oauth",
            "name": self.display_name,
            "runner": lambda on_url=None: _oauth.run_bootstrap(print_token=False, on_url=on_url),
        }

    # Reverse / research path: tools+stream work when token is live.
    default_capabilities = Capabilities(
        stream=True,
        tools=True,
        vision=False,
        embeddings=False,
        sessionful=False,
        compliance="research",
    )
    # Seed catalog — used ONLY until a live fetchAvailableModels succeeds. Kept
    # as a sensible default so routing works before the first refresh (and as a
    # fallback if the live fetch ever fails). The gateway REPLACES this with the
    # real Antigravity model list at startup (see refresh_models()), so the
    # frontend never needs to hardcode model names. The ids below mirror the
    # real Google Cloud Code Assist catalog (verified 2026-07) so the fallback
    # is accurate even if the live fetch can't reach Google.
    _SEED_MODELS = (
        "antigravity/gemini-3-flash",
        "antigravity/gemini-3-pro-high",
        "antigravity/gemini-3-pro-low",
        "antigravity/gemini-3.1-pro-high",
        "antigravity/gemini-3.1-pro-low",
        "antigravity/claude-opus-4-6-thinking",
        "antigravity/claude-opus-4-5-thinking",
        "antigravity/claude-sonnet-4-6",
    )
    _SEED_MODEL_MAP = {m: m.split("/", 1)[1] for m in _SEED_MODELS}

    def __init__(self, config, http):
        super().__init__(config, http)
        self.host = config.get("host", DEFAULT_HOST_DAILY).rstrip("/")
        self.api_version = config.get("api_version", "v1internal")
        self.thinking = config.get("thinking", False)
        self.client_metadata = config.get("client_metadata", CLIENT_METADATA)
        self.extra_headers = config.get("extra_headers", {})
        # Allow forcing a project id even if loadCodeAssist cannot discover one.
        self.fallback_project = config.get("project_id", "")
        # Mutable per-instance catalog. Starts from the seed and is overwritten
        # by the live Antigravity model list once refresh_models() succeeds.
        self.served_models = list(self._SEED_MODELS)
        self.MODEL_MAP = dict(self._SEED_MODEL_MAP)
        self.models_refreshed = False
        # The node from Settings → VPN (裁定 206 / 用户 10-28): the hub's shared client
        # runs with trust_env=false, so a Google leg can only reach the network through an
        # explicit proxy. One node, shared by every leg that needs it.
        explicit = str(config.get("proxy") or os.environ.get("QUOTA_HUB_ANTIGRAVITY_PROXY") or "").strip()
        self.proxy, self.proxy_reason = (explicit, "ok") if explicit else proxy_url()
        if self.proxy:
            import httpx as _httpx

            self.http = _httpx.AsyncClient(proxy=self.proxy, timeout=_httpx.Timeout(120.0, connect=15.0))
        # One envelope implementation for every leg that holds a Google-account (Code
        # Assist) token: this leg and the Gemini CLI leg both run on it, so a format fix
        # lands once. `self.http` is already the proxied client when a node is configured.
        self.cloudcode = CloudCodeClient(
            self.http,
            host=self.host,
            api_version=self.api_version,
            headers=dict(ANTIGRAVITY_HEADERS),
            client_metadata=self.client_metadata,
            extra_headers=self.extra_headers,
            thinking=self.thinking,
            label="antigravity",
        )

    # --- discovery -------------------------------------------------------
    async def discover_accounts(self) -> list[Account]:
        accs: list[Account] = []
        stored_id, stored_secret = account_store.client_credentials()
        cid = self.config.get("client_id") or stored_id or ds.GOOGLE_CC_CLIENT_ID
        csec = self.config.get("client_secret") or stored_secret or ds.GOOGLE_CC_CLIENT_SECRET

        # 1) config-declared accounts (refresh token may be a composite)
        for i, a in enumerate(self.config.get("accounts", [])):
            acc = self._make_account(
                id=a.get("id", f"antigravity-cfg-{i}"),
                label=a.get("label", ""),
                refresh_token=a.get("refresh_token", ""),
                access_token=a.get("access_token", ""),
                project_id=a.get("project_id", ""),
                client_id=a.get("client_id") or cid,
                client_secret=a.get("client_secret") or csec,
            )
            if acc is not None:
                accs.append(acc)

        # 2) env refresh token (the proxy's documented, headless-safe method)
        env_rt = os.environ.get("ANTIGRAVITY_REFRESH_TOKEN", "")
        if env_rt:
            acc = self._make_account(
                id="antigravity-env-refresh",
                label="env ANTIGRAVITY_REFRESH_TOKEN",
                refresh_token=env_rt,
                client_id=cid,
                client_secret=csec,
            )
            if acc is not None:
                accs.append(acc)

        # 3) env captured access token (quick live test without a refresh token)
        env_at = os.environ.get("ANTIGRAVITY_ACCESS_TOKEN", "")
        if env_at:
            accs.append(self._access_only_account("antigravity-env-access", env_at, cid, csec))

        # 3b) accounts created by `quota_hub auth antigravity` (owner-only file).
        #     This is the internalized refresh-token path: no config paste, no
        #     Manager GUI, no token printed to a terminal.
        for row in account_store.load_accounts():
            acc = self._make_account(
                id=str(row.get("id") or "antigravity-stored"),
                label=str(row.get("email") or row.get("name") or "stored account"),
                refresh_token=str(row.get("refresh_token") or ""),
                project_id=str(row.get("project_id") or ""),
                client_id=cid,
                client_secret=csec,
            )
            if acc is not None:
                accs.append(acc)

        # 4) keychain gemini/antigravity access token (go-keyring blob). Only useful
        #    when a *fresh* token is present; normally the app does not write one back.
        #    Skip it when a refresh token is already available (env/config) — the
        #    keychain token is almost always stale and would just waste a 401 round-trip.
        if not env_rt:
            kc_at = ds.read_antigravity_access_token()
            if kc_at:
                accs.append(self._access_only_account("antigravity-keychain", kc_at, cid, csec,
                                                      desktop_store=True))

        # 5) sqlite ~/.antigravity/db.sqlite (compatibility with other setups)
        local = ds.read_antigravity()
        if local and local.get("refresh_token"):
            acc = self._make_account(
                id="antigravity-local",
                label="local session (db.sqlite)",
                refresh_token=local["refresh_token"],
                client_id=cid,
                client_secret=csec,
            )
            if acc is not None:
                accs.append(acc)

        self.accounts = accs
        return accs

    def _make_account(
        self, *, id, label, refresh_token, access_token="", project_id="", client_id, client_secret
    ) -> Account | None:
        if not refresh_token and not access_token:
            return None
        rt, proj, _ = (
            ds.parse_composite_refresh(refresh_token)
            if refresh_token
            else (refresh_token, project_id, "")
        )
        cred = Credential(
            access_token=access_token,
            refresh_token=rt,
            extra={
                "client_id": client_id,
                "client_secret": client_secret,
                "project_id": proj or project_id or self.fallback_project,
                "session_id": uuid.uuid4().hex + str(int(time.time())),
            },
        )
        return Account(id=id, provider=self.name, label=label, cred=cred)

    def _access_only_account(
        self, id, access_token, client_id, client_secret, desktop_store: bool = False
    ) -> Account:
        return Account(
            id=id,
            provider=self.name,
            label=id,
            cred=Credential(
                access_token=access_token,
                extra={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "project_id": self.fallback_project,
                    "session_id": uuid.uuid4().hex + str(int(time.time())),
                    "no_refresh": True,
                    # whose copy this came from decides whether ensure_fresh may go
                    # back to the desktop app for a newer one.
                    "desktop_store": bool(desktop_store),
                },
            ),
        )

    # --- refresh ---------------------------------------------------------
    async def ensure_fresh(self, acc: Account) -> None:
        cred = acc.cred
        if cred.refresh_token:
            if cred.access_token and not cred.is_expired():
                return
            async with acc.lock:
                if cred.access_token and not cred.is_expired():
                    return
                try:
                    res = await ds.refresh_antigravity(
                        self.http,
                        cred.refresh_token,
                        cred.extra["client_id"],
                        cred.extra.get("client_secret", ""),
                    )
                except Exception as e:
                    raise UpstreamError(
                        401,
                        f"antigravity refresh failed: {type(e).__name__}: {e}",
                        reauth=True,
                    )
                cred.access_token = res["access_token"]
                cred.expires_at = res["expires_at"]
                if not cred.extra.get("project_id") and res.get("project_id"):
                    cred.extra["project_id"] = res["project_id"]
            return
        # access-token-only account: cannot refresh on its own
        if cred.extra.get("desktop_store"):
            # We have no OAuth client pair for this app, so the only renewal we can
            # ever get is the one the desktop app performs and writes back. Measured
            # 2026-10-11: agy's Credential Manager entry moved `LastWritten` the same
            # minute its stored `expiry` moved. Re-reading it is a local CredRead, so
            # do it before each dispatch instead of trusting the startup copy — that
            # copy is stale by definition within about an hour.
            status = ds.antigravity_desktop_token_status()
            if status.get("present") and status.get("expires_at"):
                cred.expires_at = status["expires_at"]  # trust the app's own stamp
            live = status.get("present") and (
                status.get("expires_in_seconds") is None
                or status["expires_in_seconds"] > 0
            )
            fresh = ds.read_antigravity_access_token() if live else None
            if fresh and fresh != cred.access_token:
                cred.access_token = fresh
                acc.state = AccountState.ACTIVE
        if not cred.access_token:
            raise UpstreamError(
                401, "antigravity: no access token and no refresh token", reauth=True
            )
        if cred.is_expired() and cred.extra.get("no_refresh"):
            acc.state = AccountState.REAUTH
            raise UpstreamError(
                401,
                "antigravity: access token expired and we cannot refresh it "
                "(the desktop app renews this entry — open it once, then retry)",
                reauth=True,
            )

    def signed_in(self) -> tuple[bool, str]:
        """Credential only: does the desktop app's own record hold a token for us?

        The node question is deliberately absent here. Measured 2026-10-28: with this
        folded into one verdict, a signed-in user whose egress node was down saw the card
        fall back to `+` — a button that says "you have not logged in", which is false, on
        the screen he was looking at right after logging in.
        """
        st = ds.antigravity_desktop_token_status()
        if not st.get("present"):
            return False, "no_desktop_credential"
        left = st.get("expires_in_seconds")
        if left is not None and left <= 0:
            return False, "credential_expired"
        return True, "ok"

    def credential_health(self) -> tuple[bool, str]:
        """Local-only: what the desktop app's own record says about this token.

        We cannot renew it ourselves (no OAuth client pair), so "expired" really
        means "open agy once" — which is why it is a visibility problem, not a
        retry problem.
        """
        live, reason = self.signed_in()
        if not live:
            return False, reason
        # Signed in, but Google is unreachable without the node — say which of the two
        # it is, so the card's tooltip points at Settings → VPN instead of at a login.
        if getattr(self, "proxy_reason", "ok") != "ok":
            return False, self.proxy_reason
        return True, "ok"

    # --- quota aggregation (internalized from Antigravity-Manager) ------
    async def refresh_quota(self, acc: Account) -> None:
        """Poll Google's own quota summary and keep it on the account.

        This is the capability we internalized: no GUI, no local gateway and no
        second process — the same account credential that serves chat is used
        to read the weekly / 5h buckets directly. Failure is data, not an
        exception: the previous snapshot stays visible and is not overwritten
        with zeros.
        """
        try:
            await self.ensure_fresh(acc)
            project = await self._project_id(acc)
            groups = await self.fetch_quota_summary(acc, project=project)
            models = await self.fetch_model_quotas(acc, project=project)
        except UpstreamError as exc:
            logger.warning("antigravity quota refresh skipped for %s: %s", acc.id, exc)
            return
        except Exception as exc:
            logger.warning(
                "antigravity quota refresh failed for %s: %s: %s",
                acc.id, type(exc).__name__, exc,
            )
            return

        if groups is None and not models:
            return
        previous = dict(acc.quota or {})
        if groups is not None:
            previous["groups"] = groups
        if models:
            previous["models"] = models
        previous["checked_at"] = int(time.time())
        previous["source"] = "antigravity:retrieveUserQuotaSummary+fetchAvailableModels"
        acc.quota = previous

    async def fetch_quota_summary(
        self, acc: Account, *, project: str = ""
    ) -> list[dict] | None:
        """``retrieveUserQuotaSummary`` → group/bucket list, or ``None`` on failure."""
        payload = {"project": project} if project else {}
        data = await self._quota_post(acc, QUOTA_SUMMARY_ENDPOINTS, payload, "retrieveUserQuotaSummary")
        if data is None:
            return None
        groups = data.get("groups") if isinstance(data, dict) else None
        if not isinstance(groups, list):
            return []
        out: list[dict] = []
        for group in groups:
            if not isinstance(group, dict):
                continue
            buckets: list[dict] = []
            for bucket in group.get("buckets") or []:
                if not isinstance(bucket, dict):
                    continue
                fraction = _as_fraction(bucket.get("remainingFraction"))
                if fraction is None:
                    continue
                buckets.append({
                    "bucket_id": str(bucket.get("bucketId") or ""),
                    "description": bucket.get("description"),
                    "display_name": bucket.get("displayName"),
                    "remaining_fraction": fraction,
                    "reset_time": str(bucket.get("resetTime") or ""),
                    "window": str(bucket.get("window") or ""),
                })
            out.append({
                "buckets": buckets,
                "description": group.get("description"),
                "display_name": str(group.get("displayName") or ""),
            })
        return out

    async def fetch_model_quotas(self, acc: Account, *, project: str = "") -> list[dict]:
        """Model-level quota (``fetchAvailableModels`` → quotaInfo) from the same call."""
        payload = {"project": project} if project else {}
        url = f"{self.host}/{self.api_version}:fetchAvailableModels"
        try:
            r = await self.http.post(
                url, headers=self._headers(acc.cred.access_token), json=payload, timeout=20.0
            )
        except Exception:
            return []
        if r.status_code != 200:
            return []
        try:
            data = r.json()
        except Exception:
            return []
        return _parse_model_quotas(data)

    async def _quota_post(
        self, acc: Account, endpoints: tuple[str, ...], payload: dict, label: str
    ) -> dict | None:
        """Try the official endpoint fallback chain; keep failures out of routing."""
        for url in endpoints:
            try:
                r = await self.http.post(
                    url, headers=self._headers(acc.cred.access_token),
                    json=payload, timeout=15.0,
                )
            except Exception:
                continue
            if r.status_code != 200:
                continue
            try:
                data = r.json()
            except Exception:
                continue
            if isinstance(data, dict):
                return data
            logger.warning("antigravity %s returned a non-object payload", label)
        return None

    # --- live model catalog (replaces hardcoded names) ------------------
    async def refresh_models(self, acc: Account) -> list[str] | None:
        """Fetch the live Antigravity model catalog and replace our served list.

        Returns the new short model ids on success, or ``None`` if the fetch
        failed (the caller keeps the seed catalog). This method never raises —
        model-list refresh must not break chat routing, so any transport /
        auth / parse error is logged and swallowed.
        """
        try:
            await self.ensure_fresh(acc)
            short_ids = await self.fetch_available_models(acc)
        except UpstreamError as e:
            logger.warning("antigravity model refresh skipped: %s", e)
            return None
        except Exception as e:
            logger.warning(
                "antigravity model refresh failed: %s: %s", type(e).__name__, e
            )
            return None
        if not short_ids:
            logger.warning("antigravity model refresh returned an empty list; keeping seed")
            return None
        self.served_models = tuple(f"antigravity/{m}" for m in short_ids)
        self.MODEL_MAP = {f"antigravity/{m}": m for m in short_ids}
        self.models_refreshed = True
        logger.info("antigravity: live model catalog = %s", self.served_models)
        return short_ids

    async def fetch_available_models(self, acc: Account) -> list[str]:
        """The account's own entitlement list — the transport raises, we do not guess."""
        return await self.cloudcode.available_models(acc.cred.access_token, await self._project_id(acc))

    # --- project discovery ------------------------------------------------
    async def _project_id(self, acc: Account) -> str:
        if acc.cred.extra.get("project_id"):
            return acc.cred.extra["project_id"]
        pid = await self._load_code_assist(acc)
        if pid:
            acc.cred.extra["project_id"] = pid
            return pid
        return self.fallback_project

    async def _load_code_assist(self, acc: Account) -> str:
        return await self.cloudcode.load_project(acc.cred.access_token, acc.cred.extra.get("session_id", ""))

    # --- request path (delegated to `providers/cloudcode.py`) -------------
    # Kept as named methods because the leg's own tests and `refresh_models` reach for
    # them, and because a name that says `_url(False)` is worth more here than an inline call.
    def _url(self, stream: bool) -> str:
        return self.cloudcode.url(stream)

    def _headers(
        self, token: str, session_id: str = "", model: str = "", accept: str = "application/json"
    ) -> dict:
        return self.cloudcode.headers_for(token, session_id, model, accept)

    def _short_model(self, oai_model: str) -> str:
        return self.MODEL_MAP.get(oai_model, oai_model.split("/")[-1])

    def _wrap(self, oai_req: dict, project: str, model: str, session_id: str) -> dict:
        return self.cloudcode.wrap(oai_req, project, model, session_id)

    async def chat(self, acc: Account, oai_req: dict) -> dict:
        return await self.cloudcode.chat(
            token=acc.cred.access_token,
            project=await self._project_id(acc),
            model=self._short_model(oai_req["model"]),
            oai_req=oai_req,
            session_id=acc.cred.extra.get("session_id", ""),
            on_response=lambda r: self._note_quota(acc, r),
        )

    async def chat_stream(self, acc: Account, oai_req: dict) -> AsyncIterator[dict]:
        async for chunk in self.cloudcode.chat_stream(
            token=acc.cred.access_token,
            project=await self._project_id(acc),
            model=self._short_model(oai_req["model"]),
            oai_req=oai_req,
            session_id=acc.cred.extra.get("session_id", ""),
            on_response=lambda r: self._note_quota(acc, r),
        ):
            yield chunk

    def _note_quota(self, acc, resp):
        rem = resp.headers.get("x-ratelimit-remaining")
        if rem is not None:
            acc.quota["remaining"] = rem


# --- translation helpers ---------------------------------------------------
def _as_fraction(value: object) -> float | None:
    """Remaining fraction 0..1; booleans and out-of-range values are not numbers."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not (0.0 <= number <= 1.0):
        return None
    return number


def _parse_model_quotas(data: object) -> list[dict]:
    """Model-level quota rows from a ``fetchAvailableModels`` record payload."""
    if not isinstance(data, dict):
        return []
    raw = data.get("models") or data.get("availableModels") or data.get("available_models")
    if not isinstance(raw, dict):
        return []
    out: list[dict] = []
    for name, info in raw.items():
        if not isinstance(info, dict):
            continue
        quota = info.get("quotaInfo") if isinstance(info.get("quotaInfo"), dict) else {}
        fraction = _as_fraction(quota.get("remainingFraction"))
        if fraction is None:
            continue
        short = str(name).split("/")[-1]
        out.append({
            "display_name": info.get("displayName"),
            "name": short,
            "remaining_fraction": fraction,
            "reset_time": str(quota.get("resetTime") or ""),
        })
    return out


