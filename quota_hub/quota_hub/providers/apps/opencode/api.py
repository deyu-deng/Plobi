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

**Measured 2026-10-28, and it changed this leg's contract**: the Free pool is refused over
HTTP — one tiny completion with a `tools` array came back
`429 {"type":"error","error":{"type":"FreeUsageLimitError", "message":"Free usage limit
reached. Free models work in OpenCode ... subscribe to OpenCode Go"}}`. So an OAuth
credential from the store is **not** evidence that a turn can be served here: Free exists
only inside the OpenCode client (that is what the ACP leg is for). This leg therefore
serves models only when the account carries a paid plan or the operator hands us a key
(`OPENCODE_ZEN_API_KEY` / config `api_key`); otherwise it reports `no_subscription` and
the box stays empty — which is the honest answer to "你有 Zen 吗".
"""

from __future__ import annotations

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
    uri = f"file:{path.as_posix()}?mode=ro&immutable=1"
    try:
        con = sqlite3.connect(uri, uri=True)
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


class OpencodeApiProvider(Provider):
    name = "opencode_api"
    display_name = "OpenCode Zen"
    default_capabilities = Capabilities(stream=True, tools=True)

    def __init__(self, config: dict, http) -> None:
        super().__init__(config, http)
        self.base_url = str(config.get("base_url") or "https://opencode.ai/zen/v1").rstrip("/")
        self.timeout = float(config.get("timeout", 120.0))
        self.served_models = ()
        self._credential: dict[str, Any] | None = None
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
            "reason": "Free 池在 HTTP 上不给用；要 OpenCode Go 订阅或一个 API key（OPENCODE_ZEN_API_KEY）",
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
        # Measured: the Free pool refuses HTTP turns (429 FreeUsageLimitError). The
        # credential is real; the entitlement is not. Saying "ok" here is what put 44
        # unusable ids in the model box on 2026-10-28.
        return False, "no_subscription", data

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
                f"{self.base_url}/models",
                headers={"Authorization": f"Bearer {token}"},
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
    def _body(self, oai_req: dict) -> dict[str, Any]:
        body = {k: v for k, v in oai_req.items() if k in {
            "messages", "temperature", "top_p", "max_tokens", "stop", "tools", "tool_choice", "parallel_tool_calls",
        }}
        model = str(oai_req.get("model") or "")
        body["model"] = model.split("/", 1)[1] if "/" in model else model
        return body

    async def chat(self, acc: Account, oai_req: dict) -> dict:
        res = await self.http.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {acc.cred.access_token}"},
            json=self._body(oai_req),
            timeout=self.timeout,
        )
        if res.status_code != 200:
            raise UpstreamError(res.status_code, res.text[:400])
        try:
            return res.json()
        except ValueError as exc:
            raise UpstreamError(502, f"opencode answered a non-JSON body: {exc}") from exc

    async def chat_stream(self, acc: Account, oai_req: dict) -> AsyncIterator[dict]:
        # v1 folds the answer into one chunk: correct, just not incremental. The
        # incremental path needs SSE proxying and its own tests; the turn is what matters.
        result = await self.chat(acc, oai_req)
        choice = (result.get("choices") or [{}])[0] if isinstance(result, dict) else {}
        message = choice.get("message") or {}
        cid = str(result.get("id") or f"opencode-{int(time.time())}")
        yield {
            "choices": [{"delta": message, "finish_reason": choice.get("finish_reason") or "stop", "index": 0}],
            "created": int(result.get("created") or time.time()),
            "id": cid,
            "model": str(oai_req.get("model") or ""),
            "object": "chat.completion.chunk",
        }
