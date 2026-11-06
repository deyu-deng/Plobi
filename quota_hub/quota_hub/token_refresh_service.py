"""Background token-renewal worker (sub2api style).

Sweeps the registry's accounts on a timer and asks each provider to renew leases that
are inside the skew window. The refresh path is the same JIT path the scheduler hits
before a turn (`Provider.ensure_fresh`), so there is exactly one place that talks to the
measured endpoints — this worker just makes renewal proactive instead of reactive.

Why a separate worker (per the 2026-11-03 research plan):

* a JIT-only refresh leaves renewal to whichever request happens to arrive near expiry;
  the worker renews while the lease is still comfortably valid (default skew 24 h);
* one worker, one lock per account: two simultaneous turns cannot both spend the same
  refresh token;
* failures back off exponentially and, after a hard cap, mark the account REAUTH so the
  card asks the user to sign in again instead of hammering a dead token forever.

Semantics kept from the plan:

* 401 / `reauth`  →  mark REAUTH (needs user re-login), stop retrying this sweep;
* 429            →  cool down by the upstream's `Retry-After` (`UpstreamError.cooldown`);
* the free-bucket 429 that OpenCode answers on *inference* turns never reaches this
  worker (it only calls `ensure_fresh`, no chat traffic), so it is not counted as a
  refresh failure by construction.

The worker is inert unless the hub is started with `QUOTA_HUB_TOKEN_REFRESH=1` (or
`token_refresh.enabled: true` in the config); see `quota_hub/main.py`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping

from .providers.base import Account, AccountState, Provider, UpstreamError

log = logging.getLogger("quota_hub.refresh")

#: Consecutive refresh failures before an account is parked as REAUTH. A single network
#: blip must not burn a working login, hence the cap rather than fail-fast.
_MAX_CONSECUTIVE_FAILS = 3
#: Base backoff between attempts (doubles each failure, capped).
_BASE_BACKOFF_S = 30.0
_MAX_BACKOFF_S = 4 * 3600.0


class TokenRefreshService:
    """Timer-driven sweeper over all registered providers' accounts."""

    def __init__(
        self,
        providers: Mapping[str, Provider],
        *,
        interval_seconds: float = 300.0,
        skew_seconds: float = 24 * 3600,
    ):
        self._providers = providers
        self._interval = interval_seconds
        self._skew = skew_seconds
        self._locks: dict[str, asyncio.Lock] = {}
        self._fails: dict[str, int] = {}
        self._cool_until: dict[str, float] = {}

    # --- public entry -------------------------------------------------------
    async def run(self) -> None:
        while True:
            try:
                await self._sweep()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # never let one sweep kill the worker
                log.warning("token refresh sweep failed: %s: %s", type(exc).__name__, exc)
            await asyncio.sleep(self._interval)

    # --- internals ----------------------------------------------------------
    def _due(self, acc: Account) -> bool:
        return acc.cred.expires_at != 0 and time.time() >= (acc.cred.expires_at - self._skew)

    async def _sweep(self) -> None:
        for name, prov in self._providers.items():
            for acc in prov.accounts:
                if acc.state in (AccountState.REAUTH, AccountState.DISABLED):
                    continue
                if not self._due(acc):
                    continue
                await self._refresh(name, acc)

    async def _refresh(self, name: str, acc: Account) -> None:
        lock = self._locks.setdefault(acc.id, asyncio.Lock())
        async with lock:
            if time.time() < self._cool_until.get(acc.id, 0.0):
                return  # upstream asked us to wait (Retry-After)
            if not self._due(acc):
                return  # another path (JIT turn) already renewed
            try:
                await self._providers[name].ensure_fresh(acc)
            except UpstreamError as exc:
                if exc.status == 429 and exc.cooldown > 0:
                    self._cool_until[acc.id] = time.time() + exc.cooldown
                    log.info(
                        "[refresh] %s/%s cooled down for %.0fs (Retry-After)",
                        name, acc.id, exc.cooldown,
                    )
                    return
                self._bump(name, acc, exc)
            except Exception as exc:  # noqa: BLE001 - a refresh must never crash the loop
                self._bump(name, acc, exc)
            else:
                if self._fails.pop(acc.id, 0):
                    log.info("[refresh] %s/%s recovered after refresh failure", name, acc.id)
                self._cool_until.pop(acc.id, None)

    def _bump(self, name: str, acc: Account, exc: Exception) -> None:
        fails = self._fails.get(acc.id, 0) + 1
        self._fails[acc.id] = fails
        if fails >= _MAX_CONSECUTIVE_FAILS:
            acc.state = AccountState.REAUTH
            log.warning(
                "[refresh] %s/%s parked as REAUTH after %d consecutive refresh failures: %s",
                name, acc.id, fails, exc,
            )
            return
        backoff = min(_BASE_BACKOFF_S * (2 ** (fails - 1)), _MAX_BACKOFF_S)
        acc.cooldown_until = time.time() + backoff
        log.warning(
            "[refresh] %s/%s refresh failed (%d/%d), backing off %.0fs: %s",
            name, acc.id, fails, _MAX_CONSECUTIVE_FAILS, backoff, exc,
        )
