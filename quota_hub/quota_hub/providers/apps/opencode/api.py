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

* The credential is **read-only by default** and stays in this process: the SQLite file is
  opened `mode=ro`, and no token is logged, copied or persisted here. The single exception
  is the measured renewal path (2026-11-03, HTTP 200): `refresh_credential()` +
  `persist_credential()` re-open the store `mode=rw` only when a refresh succeeded and
  the worker is enabled (`QUOTA_HUB_TOKEN_REFRESH=1`), with an optimistic compare against
  the row's refresh token so the CLI's own renewal is never overwritten.
* No refresh flow in v1 was the old boundary; it is now JIT: `ensure_fresh` renews while
  the lease still has >24 h left (skew), and a failed refresh still degrades to
  `credential_expired` → the card keeps its `+` (the user signs in again with
  `opencode auth login`).
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
import math
import os
import sqlite3
import time
from collections.abc import AsyncIterator

import httpx
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


def refresh_credential(cred: dict[str, Any] | None) -> dict[str, Any] | None:
    """Renew the OAuth pair via /console/auth/device/token (measured HTTP 200).

    Body is `{"grant_type":"refresh_token","refresh_token":<rt_...>,
    "client_id":"opencode-cli"}`; the server comes from the credential's own metadata.
    Rotation: the old refresh token dies on use, so the caller must persist the new
    value (or re-login). Returns the merged new credential on success, None otherwise —
    the caller owns backoff / reauth. Never logs token values.
    """
    if not cred:
        return None
    rt = cred.get("refresh") or cred.get("refresh_token")
    if not rt:
        return None
    server = str((cred.get("metadata") or {}).get("server") or "").rstrip("/")
    url = (server + "/auth/device/token") if server else REFRESH_PATH
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": rt,
        "client_id": REFRESH_CLIENT_ID,
    }
    try:
        res = httpx.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json", "User-Agent": REFRESH_UA},
            timeout=30,
        )
    except httpx.HTTPError as exc:
        log.warning("[opencode_api] refresh request failed: %s", type(exc).__name__)
        return None
    if res.status_code != 200:
        log.warning("[opencode_api] refresh -> HTTP %s", res.status_code)
        return None
    try:
        body = res.json()
    except ValueError:
        log.warning("[opencode_api] refresh answered a non-JSON body")
        return None
    if not isinstance(body, dict) or not body.get("access_token") or not body.get("refresh_token"):
        log.warning("[opencode_api] refresh body missing access_token/refresh_token")
        return None
    expires_in = body.get("expires_in")
    if isinstance(expires_in, (int, float)) and expires_in > 0:
        new_expires = int(time.time() * 1000) + int(expires_in * 1000)
    else:
        new_expires = int(time.time() * 1000) + 30 * 24 * 3600 * 1000
    return {
        **cred,
        "access": str(body["access_token"]),
        "refresh": str(body["refresh_token"]),
        "expires": new_expires,
    }


def persist_credential(cred: dict[str, Any]) -> bool:
    """Controlled write-back to opencode.db (mode=rw), optimistic against CLI rewrites.

    The CLI rewrites this row when it renews, so before committing we compare the stored
    refresh token with the one we just produced; a mismatch means the CLI won and we
    drop our write rather than clobber a newer pair. Returns False when the write was
    abandoned (or failed) — the caller keeps serving the store's value.
    """
    path = store_path()
    if not path.is_file():
        return False
    uri = f"file:{path.as_posix()}?mode=rw"
    try:
        con = sqlite3.connect(uri, uri=True, timeout=0.5)
    except sqlite3.Error as exc:
        log.warning("opencode store at %s cannot be opened for write: %s", path, exc)
        return False
    try:
        row = con.execute(
            "select value from credential where integration_id='opencode' and active=1 "
            "order by time_updated desc limit 1"
        ).fetchone()
        if row and row[0]:
            try:
                old = json.loads(row[0])
            except ValueError:
                old = None
            if isinstance(old, dict) and (old.get("refresh") or "") != (cred.get("refresh") or ""):
                log.info("[opencode_api] store moved on (CLI refreshed); skipping our write-back")
                return False
        con.execute(
            "update credential set value=?, time_updated=? "
            "where integration_id='opencode' and active=1",
            (json.dumps(cred), int(time.time() * 1000)),
        )
        con.commit()
        return True
    except sqlite3.Error as exc:
        log.warning("[opencode_api] credential write-back failed: %s", exc)
        return False
    finally:
        con.close()


DEFAULT_CHAT_BASE = "https://opencode.ai/inference/openai/v1"
DEFAULT_MODELS_BASE = "https://opencode.ai/inference/v1"
#: Our own identity, per OpenCode's paid-lane instructions.
SELF_UA = "plobi-quota-hub/1.0"

#: Token renewal (measured 2026-11-03, HTTP 200): POST /console/auth/device/token with
#: grant_type=refresh_token returns access_token / refresh_token / expires_in (30 days)
#: and consumes the old refresh token (rotation). UA follows the official client shape;
#: the research plan pinned this exact string.
REFRESH_PATH = "https://opencode.ai/console/auth/device/token"
REFRESH_CLIENT_ID = "opencode-cli"
REFRESH_UA = "opencode/latest/2.0.25/cli"
#: Renew while >24 h of the lease remain (sub2api-style lead time).
_REFRESH_SKEW_S = 24 * 3600

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
        #: Set by an account-level `402 Insufficient account funds`; cleared by a served turn.
        self._no_wallet = False
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
        # An account-level "no funds" answer outranks "live" until a turn proves otherwise;
        # the wording already exists in all four languages (这条渠道要付费订阅或一个 API key).
        if self._no_wallet:
            return False, "no_subscription", data
        return True, "ok", data

    def credential_health(self) -> tuple[bool, str]:
        live, reason, _ = self._credential_state()
        return live, reason

    def quota_retry_after(self) -> int | None:
        """Seconds left in the free-pool cooldown, None when this leg is not cooling.

        Measured 2026-11-05: the free lane answers `429 FreeUsageLimitError` with
        `retry-after≈76331` (~21 h) and `_free_blocked_until` parks the leg for exactly
        that long. Until it expires every request is refused here in 4-96 ms without
        the upstream ever being asked — so the honest answer to a caller is "come back
        in N seconds", not "the gateway is unavailable" and not a silent retry storm.
        This is a duration, not a diagnosis: it stays None for legs and for windows
        that have already passed.
        """
        blocked = float(self._free_blocked_until or 0.0)
        remaining = blocked - time.time()
        if remaining <= 0:
            return None
        return max(1, math.ceil(remaining))

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
        # JIT renewal (endpoint measured 2026-11-03): renew while >24 h of the lease
        # remain. A failed refresh degrades to the old "sign in again" verdict.
        if not acc.cred.is_expired(skew=_REFRESH_SKEW_S):
            return
        data = read_credential() or {}
        new_cred = refresh_credential(data)
        if new_cred is None:
            raise UpstreamError(
                401, "opencode refresh failed — sign in again with `opencode auth login`", reauth=True
            )
        if persist_credential(new_cred):
            self._credential = new_cred
            acc.cred.access_token = str(new_cred.get("access") or "")
            acc.cred.refresh_token = str(new_cred.get("refresh") or "")
            acc.cred.expires_at = float(new_cred.get("expires", 0)) / 1000.0
        else:
            # The CLI won the write race; keep serving the store's freshest value.
            data = read_credential() or {}
            acc.cred.access_token = str(data.get("access") or acc.cred.access_token)
            acc.cred.refresh_token = str(data.get("refresh") or acc.cred.refresh_token)

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
        if res.status_code == 402:
            # Measured 2026-10-31 on his own account: every paid id answers
            # `402 {"type":"server_error","message":"Upstream request failed: Insufficient
            # account funds"}`. That answer is only reachable *after* the door accepts who
            # we are - so this leg is not a stranger; the account simply has no balance.
            # One such answer is an account-level fact, so it ends the whole leg's turn
            # here instead of letting 32 ids each burn two refusals to leave the box one by one.
            self._no_wallet = True
            return UpstreamError(res.status_code, text, retryable=False)
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
        self._no_wallet = False  # a served turn outranks any earlier "no funds"
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
            self._no_wallet = False  # a served stream is as good evidence as a served turn
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
