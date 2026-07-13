"""Provider abstraction layer.

Every desktop app we aggregate (Cursor / Antigravity / Workbuddy) is modeled as a
`Provider`. A Provider owns one or more `Account`s (upstream credentials) and knows
how to:

  1. load / refresh credentials from the desktop app's local storage
  2. translate an OpenAI ChatCompletion request into the upstream's native protocol
  3. translate the upstream response (incl. streaming) back into OpenAI shape
  4. read remaining quota / rate-limit signals

The contract is deliberately narrow so the scheduler (aigw/scheduler.py) can treat
all providers uniformly. This mirrors Sub2API's `internal/gateway` adapter idea and
antigravity-proxy's format-conversion modules.
"""
from __future__ import annotations

import time
import enum
import asyncio
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

import httpx


class AccountState(str, enum.Enum):
    ACTIVE = "active"          # schedulable
    COOLDOWN = "cooldown"      # 429 / temporary backoff, will recover
    REAUTH = "reauth"          # 401 / refresh token dead, needs user re-login
    DISABLED = "disabled"      # 403 entitlement / manually disabled


@dataclass
class Credential:
    """Normalized credential blob. Providers fill what they have."""
    access_token: str = ""
    refresh_token: str = ""
    expires_at: float = 0.0            # unix ts; 0 = unknown
    extra: dict = field(default_factory=dict)  # provider-specific (checksum, session id, client_id...)

    def is_expired(self, skew: float = 60.0) -> bool:
        return self.expires_at != 0 and time.time() >= (self.expires_at - skew)


@dataclass
class Account:
    """A single upstream seat inside a provider's pool."""
    id: str
    provider: str
    label: str = ""
    cred: Credential = field(default_factory=Credential)
    state: AccountState = AccountState.ACTIVE
    cooldown_until: float = 0.0
    fail_count: int = 0
    # passive quota view, refreshed from upstream rate-limit headers (Sub2API style)
    quota: dict = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def schedulable(self) -> bool:
        if self.state == AccountState.ACTIVE:
            return True
        if self.state == AccountState.COOLDOWN and time.time() >= self.cooldown_until:
            self.state = AccountState.ACTIVE
            self.fail_count = 0
            return True
        return False

    def trip(self, seconds: float):
        """Circuit-break this account for `seconds` (exponential caller-side)."""
        self.state = AccountState.COOLDOWN
        self.cooldown_until = time.time() + seconds
        self.fail_count += 1


class UpstreamError(Exception):
    """Raised by adapters so the scheduler can decide retry vs. fail-fast."""
    def __init__(self, status: int, message: str, retryable: bool = False,
                 reauth: bool = False, cooldown: float = 0.0):
        super().__init__(message)
        self.status = status
        self.retryable = retryable
        self.reauth = reauth
        self.cooldown = cooldown


class Provider:
    """Base class. Subclasses implement the abstract methods."""

    name: str = "base"
    # model ids this provider serves in the unified namespace, e.g. "cursor/gpt-4o"
    served_models: tuple[str, ...] = ()

    def __init__(self, config: dict, http: httpx.AsyncClient):
        self.config = config
        self.http = http
        self.accounts: list[Account] = []

    # --- lifecycle -------------------------------------------------------
    async def discover_accounts(self) -> list[Account]:
        """Load credentials from the desktop app's local storage. Return accounts."""
        raise NotImplementedError

    async def ensure_fresh(self, acc: Account) -> None:
        """Refresh access_token in place if expired. Raise UpstreamError(reauth=True)
        when the refresh token itself is dead."""
        raise NotImplementedError

    # --- request path ----------------------------------------------------
    async def chat(self, acc: Account, oai_req: dict) -> dict:
        """Non-streaming: return an OpenAI ChatCompletion object."""
        raise NotImplementedError

    async def chat_stream(self, acc: Account, oai_req: dict) -> AsyncIterator[dict]:
        """Streaming: yield OpenAI chat.completion.chunk dicts."""
        raise NotImplementedError
        yield  # pragma: no cover  (make this an async generator)

    # --- embeddings ----------------------------------------------------
    async def embeddings(self, acc: Account, oai_req: dict) -> dict:
        """OpenAI /v1/embeddings. Default: unsupported (real adapters add this)."""
        raise UpstreamError(501, "embeddings not supported by this provider",
                            retryable=False)

    # --- introspection ---------------------------------------------------
    async def refresh_quota(self, acc: Account) -> None:
        """Optionally poll upstream for remaining quota. No-op by default."""
        return None

    # --- helpers shared by subclasses -----------------------------------
    @staticmethod
    def _classify_http(status: int) -> Optional[UpstreamError]:
        """Map upstream HTTP status to our retry/cooldown/reauth policy.
        Mirrors Sub2API: 401->reauth, 403->disable, 429->cooldown."""
        if status == 401:
            return UpstreamError(401, "unauthorized", reauth=True)
        if status == 403:
            return UpstreamError(403, "entitlement/forbidden", retryable=False)
        if status == 429:
            return UpstreamError(429, "rate limited", retryable=True, cooldown=30.0)
        if status >= 500:
            return UpstreamError(status, "upstream 5xx", retryable=True, cooldown=5.0)
        return None
