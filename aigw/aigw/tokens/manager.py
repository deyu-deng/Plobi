r"""Token lifecycle manager.

Wraps the scheduler's provider account pools and adds:

  1. proactive expiry pre-check  — refresh a token *before* it expires (within
     `preempt_sec`), so an in-flight request never hits a 401 mid-stream.
  2. background refresh loop     — an asyncio task that periodically walks every
     account and refreshes soon-to-expire ones, updating health.
  3. per-app bookkeeping         — `last_used` timestamp + `health` string
     ("ok" / "degraded" / "dead") surfaced on /healthz and `aigw status`.
  4. encrypted persistence       — optionally dump refreshed creds to the Vault so
     a gateway restart does not immediately trip re-auth (where the upstream allows
     replaying the stored refresh token).

The manager never talks to upstreams itself; it delegates refresh to each provider's
`ensure_fresh`, exactly like the scheduler does on dispatch. That keeps a single
refresh code path (cursor / antigravity / workbuddy each know their own OAuth flow).
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

from ..providers.base import Account, Provider, UpstreamError, AccountState


class Health:
    OK = "ok"
    DEGRADED = "degraded"
    DEAD = "dead"
    UNKNOWN = "unknown"


class TokenManager:
    def __init__(self, providers: dict[str, Provider], *,
                 vault: Optional["object"] = None,
                 preempt_sec: float = 300.0,
                 refresh_interval: float = 120.0,
                 persist: bool = False):
        self.providers = providers
        self.vault = vault
        self.preempt_sec = preempt_sec
        self.refresh_interval = refresh_interval
        self.persist = persist and vault is not None
        self.last_used: dict[str, float] = {}
        self.health: dict[str, str] = {}
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()

    # --- enumeration -----------------------------------------------------
    def _iter(self):
        for prov in self.providers.values():
            for acc in prov.accounts:
                yield prov, acc

    # --- bookkeeping -----------------------------------------------------
    def mark_used(self, account_id: str) -> None:
        self.last_used[account_id] = time.time()

    def set_health(self, account_id: str, status: str) -> None:
        self.health[account_id] = status

    def ensure_health_init(self) -> None:
        for _, acc in self._iter():
            self.health.setdefault(acc.id, Health.UNKNOWN)

    # --- refresh ---------------------------------------------------------
    async def _refresh_one(self, provider: Provider, account: Account) -> None:
        try:
            await provider.ensure_fresh(account)
            self.health[account.id] = Health.OK
        except UpstreamError as e:
            if e.reauth:
                account.state = AccountState.REAUTH
                self.health[account.id] = Health.DEAD
            else:
                self.health[account.id] = Health.DEGRADED
        except Exception:
            self.health[account.id] = Health.DEGRADED

    async def _loop(self) -> None:
        while not self._stop.is_set():
            for provider, account in list(self._iter()):
                # a dead/disappeared account needs a user re-login, not a retry storm
                if account.state in (AccountState.REAUTH, AccountState.DISABLED):
                    self.health[account.id] = (Health.DEAD if account.state == AccountState.REAUTH
                                               else Health.DEGRADED)
                    continue
                cred = account.cred
                # refresh when: no token yet, expiry unknown (0), or expiring soon
                if (not cred.access_token or cred.expires_at == 0
                        or cred.is_expired(self.preempt_sec)):
                    await self._refresh_one(provider, account)
            if self.persist:
                self._persist()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.refresh_interval)
            except asyncio.TimeoutError:
                pass

    # --- lifecycle -------------------------------------------------------
    def start(self) -> None:
        if self._task is None:
            self.ensure_health_init()
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            self._task = loop.create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    # --- persistence -----------------------------------------------------
    def _persist(self) -> None:
        assert self.vault is not None
        data = {}
        for provider, account in self._iter():
            data[account.id] = {
                "provider": provider.name,
                "access_token": account.cred.access_token,
                "refresh_token": account.cred.refresh_token,
                "expires_at": account.cred.expires_at,
                "extra": account.cred.extra,
            }
        self.vault.save(data)

    def restore(self) -> None:
        if not (self.vault and self.persist):
            return
        data = self.vault.load()
        if not data:
            return
        for provider, account in self._iter():
            if account.id in data:
                d = data[account.id]
                account.cred.access_token = d.get("access_token", "")
                account.cred.refresh_token = d.get("refresh_token", "")
                account.cred.expires_at = d.get("expires_at", 0.0)
                account.cred.extra = d.get("extra", {})

    # --- introspection ---------------------------------------------------
    def status(self) -> dict:
        out = {}
        for provider, account in self._iter():
            exp = account.cred.expires_at
            out[account.id] = {
                "provider": provider.name,
                "state": account.state.value,
                "health": self.health.get(account.id, Health.UNKNOWN),
                "last_used": self.last_used.get(account.id),
                "expires_at": exp,
                "expires_in_sec": (int(exp - time.time()) if exp else None),
                "fail": account.fail_count,
            }
        return out
