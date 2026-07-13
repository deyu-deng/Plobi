"""Account pool scheduler.

Responsibilities (borrowed from Sub2API's smart-scheduling design):
  - pick a schedulable account for a given provider (or a *list* of providers for
    cross-provider failover when routing rules map a virtual model to several apps)
  - sticky sessions: pin a conversation (session id) to one account so tool-call
    / multi-turn context does not break when the pool rotates
  - concurrency semaphores (global / per-account)
  - circuit breaker with exponential backoff on 429 / 5xx
  - automatic failover + bounded retries across accounts AND across providers
  - drive token refresh before each dispatch, and feed last_used/health to the
    TokenManager
"""
from __future__ import annotations

import time
import random
import asyncio
from typing import AsyncIterator, Callable, Optional

from .providers.base import Provider, Account, AccountState, UpstreamError
from .tokens.manager import TokenManager, Health


class NoAccountAvailable(Exception):
    pass


class Scheduler:
    def __init__(self, providers: dict[str, Provider], *,
                 max_retries: int = 3,
                 global_concurrency: int = 32,
                 per_account_concurrency: int = 4,
                 sticky_ttl: float = 1800.0,
                 strategy: str = "least_fail",
                 token_manager: Optional[TokenManager] = None):
        self.providers = providers
        self.max_retries = max_retries
        self.sticky_ttl = sticky_ttl
        self.strategy = strategy
        self.token_manager = token_manager
        self._global_sem = asyncio.Semaphore(global_concurrency)
        self._acc_sems: dict[str, asyncio.Semaphore] = {}
        self._per_account = per_account_concurrency
        self._sticky: dict[str, tuple[str, float]] = {}
        self._rr: dict[str, int] = {}  # round-robin counter per account id

    # ---- account selection ---------------------------------------------
    def _sem_for(self, acc: Account) -> asyncio.Semaphore:
        return self._acc_sems.setdefault(acc.id, asyncio.Semaphore(self._per_account))

    @staticmethod
    def _serves(prov: Provider, model: str) -> bool:
        if model in prov.served_models:
            return True
        # "provider/anything" is implicitly served if the provider is enabled
        return model.startswith(prov.name + "/")

    def _candidates(self, prov_list: list[str], tried: set[str], model: str) -> list[tuple[Account, str]]:
        out = []
        for p in prov_list:
            prov = self.providers.get(p)
            if not prov or not self._serves(prov, model):
                continue
            for a in prov.accounts:
                if a.schedulable() and a.id not in tried:
                    out.append((a, p))
        return out

    def _select(self, candidates: list[tuple[Account, str]],
                session_id: Optional[str]) -> tuple[Account, str]:
        # sticky: reuse the pinned account if still in the candidate set
        if session_id and session_id in self._sticky:
            acc_id = self._sticky[session_id][0]
            for a, p in candidates:
                if a.id == acc_id:
                    self._sticky[session_id] = (acc_id, time.time())
                    return a, p
        # strategy
        if self.strategy == "round_robin":
            best = min(candidates, key=lambda ap: self._rr.get(ap[0].id, 0))
            self._rr[best[0].id] = self._rr.get(best[0].id, 0) + 1
        else:  # least_fail (default): fewest failures, ties broken randomly
            best = min(candidates, key=lambda ap: (ap[0].fail_count, random.random()))
        if session_id:
            self._sticky[session_id] = (best[0].id, time.time())
        return best

    # ---- dispatch -------------------------------------------------------
    async def dispatch(self, provider, oai_req: dict,
                       session_id: Optional[str], stream: bool):
        """provider may be a single provider name or a list of names (cross-provider
        failover). Returns a dict (non-stream) or an async iterator (stream)."""
        prov_list = list(provider) if isinstance(provider, (list, tuple)) else [provider]
        prov_list = [p for p in prov_list if p in self.providers]
        if not prov_list:
            raise NoAccountAvailable(f"no such provider(s): {provider}")

        tried: set[str] = set()
        last_err: Optional[Exception] = None

        for attempt in range(self.max_retries):
            candidates = self._candidates(prov_list, tried, oai_req["model"])
            if not candidates:
                last_err = NoAccountAvailable(f"no more accounts for {prov_list}")
                break
            acc, chosen = self._select(candidates, session_id)
            tried.add(acc.id)

            try:
                await self.providers[chosen].ensure_fresh(acc)
            except UpstreamError as e:
                self._apply_error(acc, e, session_id)
                last_err = e
                continue

            try:
                if stream:
                    return await self._run_stream(self.providers[chosen], acc, oai_req)
                async with self._global_sem, self._sem_for(acc):
                    result = await self.providers[chosen].chat(acc, oai_req)
                acc.fail_count = max(0, acc.fail_count - 1)
                self._touch(acc)
                return result
            except UpstreamError as e:
                self._apply_error(acc, e, session_id)
                last_err = e
                if not e.retryable and not e.reauth:
                    raise
                continue

        raise last_err or NoAccountAvailable(f"exhausted accounts for {prov_list}")

    async def _run_stream(self, prov: Provider, acc: Account, oai_req: dict) -> AsyncIterator[dict]:
        """Acquire concurrency slots for the lifetime of the stream."""
        await self._global_sem.acquire()
        sem = self._sem_for(acc)
        await sem.acquire()

        async def gen():
            try:
                async for chunk in prov.chat_stream(acc, oai_req):
                    yield chunk
                acc.fail_count = max(0, acc.fail_count - 1)
                self._touch(acc)
            finally:
                sem.release()
                self._global_sem.release()
        return gen()

    # ---- bookkeeping hooks ---------------------------------------------
    def _touch(self, acc: Account) -> None:
        if self.token_manager:
            self.token_manager.mark_used(acc.id)

    def _apply_error(self, acc: Account, e: UpstreamError, session_id):
        if e.reauth:
            acc.state = AccountState.REAUTH
            health = Health.DEAD
        elif e.status == 403:
            acc.state = AccountState.DISABLED
            health = Health.DEAD
        elif e.cooldown:
            backoff = e.cooldown * (2 ** min(acc.fail_count, 5))
            acc.trip(backoff)
            health = Health.DEGRADED
        else:
            acc.fail_count += 1
            health = Health.DEGRADED
        if self.token_manager:
            self.token_manager.set_health(acc.id, health)
        # drop sticky binding if the pinned account died, so next call re-pins
        if session_id and self._sticky.get(session_id, (None,))[0] == acc.id:
            if acc.state != AccountState.ACTIVE:
                self._sticky.pop(session_id, None)
