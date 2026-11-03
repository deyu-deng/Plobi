"""Cline's own OpenAI-compatible door, preferred over the ACP session.

Measured 2026-10-31 against this account (no secret printed, credential read only):

  * ``GET https://api.cline.bot/api/v1/models`` → 200, 439 ids.
  * three real turns came back ``finish_reason="tool_calls"`` in 0.7-7.1 s
    (``moonshotai/kimi-k3.1-pro``, ``deepseek/deepseek-v4.1-flash-1017``, ``z-ai/glm-5.3-prime``)
    ⇒ the tool call returns into OUR loop, which the ACP session cannot do (it runs the
    tools on Cline's side and hands back a finished answer).
  * the answer is NOT a top-level OpenAI object: the completion sits inside
    ``{"data": {"choices": [...]}}`` ⇒ unwrap before handing it to the caller.
  * ``cline-free/*`` ids are refused on the raw API: 403 "only available via Cline product
    surfaces" (same family as Google's ``UNSUPPORTED_CLIENT``) ⇒ the free pool never enters
    our catalog.
  * the CLI's own ``providers.json`` stores ``expiresAt`` in **milliseconds** (raw
    ``1793443652000`` = 31 minutes out). Read it as seconds and the lease looks good for
    57,000 years, the verdict says "alive", and the leg goes silent half an hour later -
    exactly the breakage this file exists to avoid.

Two credential sources, in order: a key he minted (config ``api_key`` or
``CLINE_API_KEY``/``CLINEPASS_API_KEY``), then the access token Cline's own app keeps
refreshing. With neither, the leg raises ``UpstreamError(401, reauth=True)`` on a turn
and the card asks him to run Cline's own login - there is no ACP spawn to fall back on.
"""
from __future__ import annotations

import json
import os
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx

from ...base import Account, UpstreamError

#: A turn must not start on a token that dies mid-flight.
_LEASE_SKEW = 120.0

#: The only ids this account was measured refusing on the raw API.
_FREE_POOL_PREFIX = "cline-free/"


def providers_json_path(config: dict) -> Path:
    """Where Cline keeps its own auth - overridable so a test never reads his file."""
    explicit = str(config.get("providers_json") or os.environ.get("CLINE_PROVIDERS_JSON") or "").strip()
    return Path(explicit) if explicit else Path.home() / ".cline" / "data" / "settings" / "providers.json"


def read_stored_auth(config: dict) -> tuple[str, float]:
    """(access_token, expires_at_seconds) from Cline's own file - no write, ever.

    Returns ("", 0.0) when there is no file, no auth block, or it holds no token. A
    ``expiresAt`` bigger than a plausible seconds-epoch is taken as milliseconds.
    """
    try:
        data = json.loads(providers_json_path(config).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "", 0.0
    providers = data.get("providers")
    if not isinstance(providers, dict):
        return "", 0.0
    wanted = str(data.get("lastUsedProvider") or "")
    order = ([wanted] if wanted in providers else []) + [k for k in providers if k != wanted]
    for key in order:
        settings = (providers.get(key) or {}).get("settings") or {}
        auth = settings.get("auth") or {}
        token = str(auth.get("accessToken") or "")
        if not token:
            continue
        raw = auth.get("expiresAt")
        exp = float(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool) else 0.0
        if exp > 1e11:  # milliseconds
            exp /= 1000.0
        return token, exp
    return "", 0.0


class ClineHttpMixin:
    """Serves the ``cline`` leg over ``api.cline.bot`` when a credential is available."""

    #: The namespace of our published ids: always the card's own registry name, so
    #: `provider/<id>` in `/v1/models` and the id itself agree (see `cli.py`).
    def _prefix(self) -> str:
        return str(getattr(self, "model_prefix", "") or self.name)

    #: What the caller asked for, verbatim - Cline's ids are already vendor-qualified.
    def _upstream_model(self, model: str) -> str:
        prefix = f"{self._prefix()}/"
        return model[len(prefix):] if model.startswith(prefix) else model

    def _static_key(self) -> str:
        return str(self.config.get("api_key") or os.environ.get("CLINE_API_KEY")
                   or os.environ.get("CLINEPASS_API_KEY") or "").strip()

    def _live_credential(self) -> str:
        """The token a turn can be sent with right now, or "" for none."""
        if self._static_key():
            return self._static_key()
        token, exp = read_stored_auth(self.config)
        if not token:
            return ""
        if exp and time.time() >= exp - _LEASE_SKEW:
            return ""
        return token

    # --- verdicts --------------------------------------------------------
    def signed_in(self) -> tuple[bool, str]:
        """The credential question only: does Cline have a session we can ride?"""
        if self._static_key():
            return True, "ok"
        token, _ = read_stored_auth(self.config)
        return (True, "ok") if token else (False, "no_desktop_credential")

    def credential_health(self) -> tuple[bool, str]:
        if self._static_key():
            return True, "ok"
        token, exp = read_stored_auth(self.config)
        if not token:
            return False, "no_desktop_credential"
        if exp and time.time() >= exp - _LEASE_SKEW:
            # The app renews this itself; the screen already says "打开那个应用一次就能续上".
            return False, "credential_expired"
        return True, "ok"

    # --- accounts --------------------------------------------------------
    async def discover_accounts(self) -> list[Account]:
        """The credential question only: no credential, no seat.

        There is no ACP spawn to fall back on - a Cline session exists exactly when a
        token does. The config may still name seats (`accounts`); each gets the live
        token so the rest of the hub treats this leg like the pure-HTTP door it is.
        """
        token = self._live_credential()
        if not token:
            return []
        raw = self.config.get("accounts") or [{}]
        accounts = [
            Account(id=str(a.get("id") or f"{self.name}-default"),
                    provider=self.name,
                    label=str(a.get("label") or self.display_name or ""))
            for a in raw
        ]
        for acc in accounts:
            acc.cred.access_token = token
        return accounts

    async def ensure_fresh(self, acc: Account) -> None:
        """Follow Cline's own renewal instead of doing one of our own.

        Rotating his refresh token from here would log the Cline app out; the file it
        writes is the cheaper source of truth, so a stale lease is re-read, not re-minted.
        """
        token = self._live_credential()
        if not token:
            if not self._static_key():
                raise UpstreamError(401, "cline credential expired", reauth=True)
            return
        acc.cred.access_token = token

    # --- request path ----------------------------------------------------
    @property
    def _base(self) -> str:
        return str(self.config.get("base_url") or "https://api.cline.bot/api/v1").rstrip("/")

    def _headers(self, acc: Account) -> dict[str, str]:
        return {"Authorization": f"Bearer {acc.cred.access_token}", "Content-Type": "application/json"}

    @staticmethod
    def _unwrap(payload: Any) -> Any:
        """Cline wraps the OpenAI object in ``{"data": ...}``; the caller must not see that."""
        if isinstance(payload, dict) and "choices" not in payload and isinstance(payload.get("data"), dict):
            inner = payload["data"]
            if "choices" in inner or "usage" in inner:
                inner.setdefault("model", payload.get("model"))
                return inner
        return payload

    async def chat(self, acc: Account, oai_req: dict) -> dict:
        token = self._live_credential()
        if not token:
            raise UpstreamError(401, "cline: no credential, run `cline auth`", reauth=True)
        acc.cred.access_token = token
        body = {**oai_req, "model": self._upstream_model(oai_req.get("model", "")), "stream": False}
        res = await self.http.post(f"{self._base}/chat/completions", headers=self._headers(acc), json=body)
        if res.status_code != 200:
            raise self._http_error(res)
        out = self._unwrap(res.json())
        if isinstance(out, dict):
            out["model"] = oai_req.get("model")  # the caller's namespace, not the vendor's
        return out

    async def chat_stream(self, acc: Account, oai_req: dict) -> AsyncIterator[dict]:
        token = self._live_credential()
        if not token:
            raise UpstreamError(401, "cline: no credential, run `cline auth`", reauth=True)
        acc.cred.access_token = token
        body = {**oai_req, "model": self._upstream_model(oai_req.get("model", "")), "stream": True}
        async with self.http.stream("POST", f"{self._base}/chat/completions",
                                    headers=self._headers(acc), json=body) as res:
            if res.status_code != 200:
                await res.aread()
                raise self._http_error(res)
            async for line in res.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    return
                try:
                    chunk = self._unwrap(json.loads(payload))
                except ValueError:
                    continue
                if isinstance(chunk, dict):
                    chunk["model"] = oai_req.get("model")
                    yield chunk

    async def refresh_models(self, acc: Account) -> list[str] | None:
        token = self._live_credential()
        if not token:
            return None
        acc.cred.access_token = token
        try:
            res = await self.http.get(f"{self._base}/models", headers=self._headers(acc))
            if res.status_code != 200:
                return None
            ids = [str(row.get("id")) for row in res.json().get("data") or [] if row.get("id")]
        except (httpx.HTTPError, ValueError):
            return None
        short = [m for m in ids if m and not m.startswith(_FREE_POOL_PREFIX)]
        if not short:
            return None
        self.served_models = tuple(f"{self._prefix()}/{m}" for m in short)
        return short

    def _http_error(self, res: httpx.Response) -> UpstreamError:
        detail = (res.text or "")[:300]
        return UpstreamError(res.status_code, f"cline: {detail}",
                             reauth=res.status_code == 401,
                             retryable=res.status_code in (429, 500, 502, 503),
                             cooldown=30.0 if res.status_code == 429 else 0.0)
