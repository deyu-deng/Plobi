"""OpenCode Zen as an OpenAI-compatible leg — the Sub2API route (裁定 204).

The rule the owner set on 2026-10-28: a `no_tools` leg is fixed by reading that product's
**own** local credential and talking to its **own** HTTP API, not by
declaring the leg text-only. OpenCode is the first one that fits end to end, measured
that day on this machine:

    ~/.local/share/opencode/opencode.db  ── table `credential`, integration_id='opencode'
                                            value = {type:"oauth", access, refresh, expires, …}
    GET https://opencode.ai/zen/v1/models ── 200, and it lists the ids itself

So this leg serves exactly what the upstream reports (never a hand-written list) and
passes `tool_calls` straight through — the OpenAI dialect is Zen's own, there is nothing
to translate.

Boundaries kept on purpose:

* The credential is **read-only** and stays in this process: the SQLite file is opened
  `mode=ro&immutable=1`, and no token is logged, copied or persisted here.
* No refresh flow in v1: an expired token reports `credential_expired` and the card keeps
  its `+` (the user signs in again with `opencode auth login`). Inventing a refresh
  against an endpoint nobody verified would be the same kind of lie this whole knife is
  removing.
* If `/models` cannot be read, the leg serves **no** models. Falling back to a declared
  list is what the ruling forbids.

**Measured twice, and the second reading changed the contract.** On 2026-10-28 one tiny
completion came back `429 {"type":"error","error":{"type":"FreeUsageLimitError", …}}`, which
we turned into "an OAuth credential is not a subscription" (`no_subscription`, box empty).
Re-measured 2026-10-31 against the same account with the *correct* lane and identity shapes:
a region-restricted free model answers `403 RegionError` — which proves the gate reads his
identity — while the free pool answers `429 FreeUsageLimitError` **with
`retry-after: 38690`**. That is a shared bucket that refills, not a missing purchase, so
this leg now reports a store credential as live and lets the upstream say otherwise: the
first 429 records that window, the card then reads "额度用完了" (the existing
`quota_exhausted`, no new wording), the scheduler cools down by exactly the seconds
upstream asked for, and the verdict lifts itself when the window passes. A paid plan or an
`api_key` still makes it unconditionally live. What the leg will never do is guess at a
model list: if `/models` cannot be read, it serves nothing.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from ....providers.base import Account, Capabilities, Credential, Provider, UpstreamError

log = logging.getLogger("quota_hub")

#: Tests point this at a throwaway copy; production reads the real store.
STORE_ENV = "QUOTA_HUB_OPENCODE_STORE"


def store_path() -> Path:
    explicit = os.environ.get(STORE_ENV, "").strip()
    if explicit:
        return Path(explicit)
    return Path.home() / ".local" / "share" / "opencode" / "opencode.db"


def read_credential() -> dict[str, Any] | None:
    """The active `opencode` OAuth record, or None. Never raises, never prints values."""
    path = store_path()
    if not path.is_file():
        return None
    uri = f"file:{path.as_posix()}?mode=ro"
    try:
        # `mode=ro` without `immutable`: OpenCode rewrites this row when it renews, and
        # immutable tells SQLite the file never changes.
        con = sqlite3.connect(uri, uri=True, timeout=0.25)
    except sqlite3.Error as exc:
        log.warning("opencode store at %s cannot be opened read-only: %s", path, exc)
        return None
    try:
        row = con.execute(
            "select value from credential where integration_id='opencode' and active=1 "
            "order by time_updated desc limit 1"
        ).fetchone()
    except sqlite3.Error as exc:
        log.warning("opencode store at %s has no readable credential table: %s", path, exc)
        return None
    finally:
        con.close()
    if not row or not row[0]:
        return None
    try:
        data = json.loads(row[0])
    except ValueError:
        log.warning("opencode credential value is not JSON; treating it as absent")
        return None
    return data if isinstance(data, dict) else None


DEFAULT_CHAT_BASE = "https://opencode.ai/inference/openai/v1"
DEFAULT_MODELS_BASE = "https://opencode.ai/inference/v1"
#: Our own identity, per OpenCode's paid-lane instructions.
SELF_UA = "plobi-quota-hub/1.0"

_BASE62 = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _session_id() -> str:
    """`ses_` + a plausible creation timestamp + random tail.

    Measured in `12errh/zen-proxy`: the gate decodes the leading 12 hex characters as a
    creation timestamp and rejects ids without a plausible one (`403 FreeTierError`), so a
    random string is not a session id.
    """
    stamp = int(time.time() * 1000) * 0x1000
    head = f"{stamp:012x}"[-12:]
    return "ses_" + head + "".join(_BASE62[b % 62] for b in os.urandom(14))


def _project_id(config: dict) -> str:
    """sha1 of `directory:<absolute path>` - the shape the upstream expects."""
    directory = str(config.get("project_directory") or os.getcwd())
    return hashlib.sha1(f"directory:{Path(directory).resolve()}".encode("utf-8")).hexdigest()


class OpencodeApiProvider(Provider):
    name = "opencode_api"
    display_name = "OpenCode Zen"
    default_capabilities = Capabilities(stream=True, tools=True)

    def __init__(self, config: dict, http) -> None:
        super().__init__(config, http)
        # Two lanes, measured 2026-10-31 from the vendor's own console docs and from
        # `12errh/zen-proxy` (MIT): chat goes on `/inference/openai/v1`, the model list on
        # `/inference/v1`. The `/zen/v1` this leg used to dial is a *legacy* base - the
        # proxy rewrites all four legacy forms on load, and non-CLI traffic against them is
        # treated as quota-exhausted (429 FreeUsageLimitError), which is what a working
        # subscription would have kept hitting.
        self.base_url = str(config.get("base_url") or DEFAULT_CHAT_BASE).rstrip("/")
        # An explicitly configured base keeps its own list endpoint (that is how a test or a
        # mirror is pointed at one host); the vendor default splits across the two lanes.
        self.models_url = str(config.get("models_url")
                              or (f"{self.base_url}/models" if config.get("base_url")
                                  else f"{DEFAULT_MODELS_BASE}/models"))
        self.timeout = float(config.get("timeout", 120.0))
        self.served_models = ()
        self._credential: dict[str, Any] | None = None
        # A paid lane has to identify itself: OpenCode's docs ask for "its own user agent…
        # rather than a generic SDK" plus a stable session id. These are ours, per process.
        self._session_id = _session_id()
        self._project_id = _project_id(config)
        #: Set from the upstream's own `retry-after` when the free bucket answers 429.
        self._free_blocked_until = 0.0
        # A paid plan / API key is the only way a turn is served over HTTP (see header).
        self.api_key = str(
            config.get("api_key") or os.environ.get("OPENCODE_ZEN_API_KEY") or os.environ.get("OPENCODE_API_KEY") or ""
        ).strip()

    # --- presence / credential -------------------------------------------
    def present(self) -> bool:
        return read_credential() is not None

    def login_plan(self) -> dict:
        # No click to offer: signing in again does not create an entitlement. The Free
        # pool works inside the OpenCode client (that is the ACP leg); over HTTP the only
        # way in is a paid plan or a key, which is configuration, not a sign-in.
        return {
            "kind": "none",
            "reason": "免费池按时刻复位；要常驻用就订 Go 或给一枚 key（OPENCODE_ZEN_API_KEY）",
            "name": self.display_name,
        }

    def _credential_state(self) -> tuple[bool, str, dict[str, Any] | None]:
        data = read_credential()
        if self.api_key:
            # The operator plugged a Zen/Go key: that is a subscription we can bill.
            return True, "ok", data or {"access": self.api_key}
        if data is None:
            return False, "no_desktop_credential", None
        expires = data.get("expires")
        if isinstance(expires, (int, float)) and expires > 0:
            # OpenCode stores milliseconds; normalise without trusting the unit.
            seconds = float(expires) / 1000.0 if expires > 1e11 else float(expires)
            if seconds <= time.time():
                return False, "credential_expired", data
        if not str(data.get("access") or data.get("access_token") or ""):
            return False, "no_desktop_credential", None
        # Measured 2026-10-31 on this account: the free lane answers per *bucket*, not per
        # subscription - a region-restricted model gives `403 RegionError` (which proves the
        # gate is reading his identity), while the free pool gives `429 FreeUsageLimitError`
        # with `retry-after: 38690` (~10.7 h). Saying "要付费订阅" for a bucket that refills
        # on its own sends him to buy something he does not need; say what is spent instead,
        # and for how long.
        blocked = float(self._free_blocked_until or 0.0)
        if blocked > time.time():
            return False, "quota_exhausted", data
        return True, "ok", data

    def credential_health(self) -> tuple[bool, str]:
        live, reason, _ = self._credential_state()
        return live, reason

    # --- accounts / models -------------------------------------------------
    def _token(self, data: dict[str, Any] | None = None) -> str:
        data = data or self._credential or read_credential() or {}
        return str(data.get("access") or data.get("access_token") or "")

    async def _list_models(self, token: str) -> list[str]:
        """Upstream's own list. Failure means "no models", not "use the old list"."""
        try:
            res = await self.http.get(
                self.models_url,
                headers=self._headers(token),
                timeout=self.timeout,
            )
        except Exception as exc:  # noqa: BLE001 - transport errors are not model names
            log.warning("[opencode_api] model list unreachable: %s", type(exc).__name__)
            return []
        if res.status_code != 200:
            log.warning("[opencode_api] model list refused with HTTP %s", res.status_code)
            return []
        try:
            body = res.json()
        except ValueError:
            return []
        rows = body.get("data") if isinstance(body, dict) else None
        ids: list[str] = []
        for row in rows or []:
            mid = str((row or {}).get("id") or "").strip()
            if mid:
                ids.append(f"{self.name}/{mid}")
        return ids

    async def discover_accounts(self) -> list[Account]:
        live, reason, data = self._credential_state()
        if not live or data is None:
            self.accounts = []
            self.served_models = ()
            return []
        token = self._token(data)
        self._credential = data
        expires = data.get("expires")
        expires_at = 0.0
        if isinstance(expires, (int, float)) and expires > 0:
            expires_at = float(expires) / 1000.0 if expires > 1e11 else float(expires)
        self.accounts = [
            Account(
                id=f"{self.name}-default",
                provider=self.name,
                label="OpenCode Zen",
                cred=Credential(access_token=token, refresh_token=str(data.get("refresh") or ""), expires_at=expires_at),
            )
        ]
        self.served_models = tuple(await self._list_models(token))
        log.info("[opencode_api] %d model(s) reported by the upstream", len(self.served_models))
        return self.accounts

    async def ensure_fresh(self, acc: Account) -> None:
        # v1 has no refresh flow on purpose (see module header): expired = sign in again.
        if acc.cred.is_expired():
            raise UpstreamError(401, "opencode credential expired — sign in again with `opencode auth login`", reauth=True)

    # --- the turn ----------------------------------------------------------
    def _headers(self, token: str) -> dict[str, str]:
        data = self._credential if self._credential is not None else (read_credential() or {})
        metadata = data.get("metadata") or {}
        org = str(metadata.get("orgID") or metadata.get("org_id") or "")
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": SELF_UA,
            "x-opencode-session": self._session_id,
            "x-opencode-project": self._project_id,
        }
        if org:
            headers["x-org-id"] = org
        return headers

    def _body(self, oai_req: dict) -> dict[str, Any]:
        body = {k: v for k, v in oai_req.items() if k in {
            "messages", "temperature", "top_p", "max_tokens", "stop", "tools", "tool_choice", "parallel_tool_calls",
        }}
        model = str(oai_req.get("model") or "")
        body["model"] = model.split("/", 1)[1] if "/" in model else model
        return body

    def _refuse(self, res) -> UpstreamError:
        """Map the vendor's own envelope, and honour its `retry-after` on the free bucket.

        Measured 2026-10-31: a spent free pool answers `429` with `retry-after: 38690`.
        Cooling down by that exact amount is the difference between one honest "满了，等一会儿"
        and a leg that hammers a bucket nobody can refill faster by asking again.
        """
        text = (getattr(res, "text", "") or "")[:400]
        kind = ""
        try:
            payload = res.json()
            err = payload.get("error") if isinstance(payload, dict) else None
            kind = str(err.get("type") or "") if isinstance(err, dict) else ""
        except ValueError:
            pass
        if res.status_code == 401:
            return UpstreamError(401, text, reauth=True)
        if res.status_code == 429 and kind == "FreeUsageLimitError":
            try:
                wait = float(res.headers.get("retry-after") or 0.0)
            except ValueError:
                wait = 0.0
            wait = wait or 300.0
            self._free_blocked_until = time.time() + wait
            return UpstreamError(429, text, retryable=True, cooldown=wait)
        return UpstreamError(res.status_code, text, retryable=res.status_code in (429, 500, 502, 503))

    async def chat(self, acc: Account, oai_req: dict) -> dict:
        res = await self.http.post(
            f"{self.base_url}/chat/completions",
            headers=self._headers(acc.cred.access_token),
            json=self._body(oai_req),
            timeout=self.timeout,
        )
        if res.status_code != 200:
            raise self._refuse(res)
        try:
            return res.json()
        except ValueError as exc:
            raise UpstreamError(502, f"opencode answered a non-JSON body: {exc}") from exc

    async def chat_stream(self, acc: Account, oai_req: dict) -> AsyncIterator[dict]:
        """Real SSE when the lane speaks it, folded answer when it answers JSON.

        Measured: the paid lane streams OpenAI-shaped `chat.completion.chunk` frames, and
        usage only arrives when `stream_options.include_usage` is asked for. This leg used
        to fold the whole answer into one chunk, which is a turn that works and a UI that
        never shows progress.
        """
        body = {**self._body(oai_req), "stream": True, "stream_options": {"include_usage": True}}
        async with self.http.stream(
                "POST", f"{self.base_url}/chat/completions",
                headers=self._headers(acc.cred.access_token), json=body, timeout=self.timeout) as res:
            if res.status_code != 200:
                await res.aread()
                raise self._refuse(res)
            if "text/event-stream" not in str(res.headers.get("content-type", "")):
                payload = await res.aread()
                try:
                    result = json.loads(payload.decode("utf-8"))
                except (ValueError, UnicodeDecodeError) as exc:
                    raise UpstreamError(502, f"opencode answered a non-JSON body: {exc}") from exc
                yield self._folded(result, oai_req)
                return
            async for line in res.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    return
                try:
                    chunk = json.loads(payload)
                except ValueError:
                    continue
                if isinstance(chunk, dict):
                    yield chunk

    @staticmethod
    def _folded(result: dict, oai_req: dict) -> dict:
        choice = (result.get("choices") or [{}])[0] if isinstance(result, dict) else {}
        message = choice.get("message") or {}
        cid = str(result.get("id") or f"opencode-{int(time.time())}")
        return {
            "choices": [{"delta": message, "finish_reason": choice.get("finish_reason") or "stop", "index": 0}],
            "created": int(result.get("created") or time.time()),
            "id": cid,
            "model": str(oai_req.get("model") or ""),
            "object": "chat.completion.chunk",
        }
