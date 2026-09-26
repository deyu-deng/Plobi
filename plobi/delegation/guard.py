"""Global concurrency guard for L3 delegation (B4).

Core ``delegate_task`` already caps a *single call* with
``max_concurrent_children`` (its ThreadPoolExecutor queues the overflow). What
core has no notion of is a process-wide ceiling: how many L3 children may run
at once across every L2 and every call. That is what this guard adds, plus a
bounded FIFO wait so overflow queues instead of failing outright.

Why the gate lives on ``pre_tool_call`` and not ``subagent_start``:
``delegate_tool`` builds every child on the main thread *before* submitting any
of them to its pool (see the "Build all child agents on the main thread"
comment). Blocking inside ``subagent_start`` would therefore wait for slots
that only a *running* child can release — a guaranteed self-deadlock.
``pre_tool_call`` fires before the call runs at all, so waiting there is safe.

Deliberately NOT a count limit: a request for more children than the cap is
never rejected for being large (L2 delegation count stays uncapped by ruling).
It takes every slot the cap allows and lets core's own batch queue absorb the
remainder.
"""

from __future__ import annotations

import itertools
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Dict, List, Optional

# Wake periodically even while waiting: expiry and interrupt responsiveness
# both need the waiter to re-evaluate, and notify_all alone can miss a waiter
# that is only eligible after a lease TTL lapses.
_WAIT_SLICE = 0.25


@dataclass(frozen=True)
class Admission:
    granted: bool
    reason: str
    slots: int
    waited: float
    active: int
    cap: int
    lease_id: Optional[str]


@dataclass
class Lease:
    id: str
    session_id: str
    turn_id: str
    slots: int
    created_at: float
    label: str = ""


@dataclass
class _Waiter:
    ticket: int
    want: int


class DelegationGuard:
    """Slot-based admission control with a bounded, FIFO-ish wait."""

    def __init__(
        self,
        *,
        cap: int = 6,
        queue_timeout: float = 30.0,
        lease_ttl: float = 900.0,
        clock=time.monotonic,
    ) -> None:
        self._cap = max(1, int(cap))
        self._queue_timeout = max(0.0, float(queue_timeout))
        self._lease_ttl = max(1.0, float(lease_ttl))
        self._clock = clock
        self._cv = threading.Condition()
        self._tickets = itertools.count()
        self._leases: Dict[str, Lease] = {}
        self._waiters: Dict[int, _Waiter] = {}

    # -- introspection -------------------------------------------------

    @property
    def cap(self) -> int:
        return self._cap

    @property
    def active(self) -> int:
        """Slots currently held. Derived from leases so it cannot drift."""
        with self._cv:
            return self._active_locked()

    def _active_locked(self) -> int:
        return sum(lease.slots for lease in self._leases.values())

    def leases(self) -> List[Lease]:
        with self._cv:
            return list(self._leases.values())

    def stats(self) -> dict:
        with self._cv:
            return {
                "cap": self._cap,
                "active": self._active_locked(),
                "available": max(0, self._cap - self._active_locked()),
                "leases": len(self._leases),
                "waiting": len(self._waiters),
                "queue_timeout_seconds": self._queue_timeout,
            }

    def set_cap(self, cap: int) -> None:
        with self._cv:
            self._cap = max(1, int(cap))
            self._cv.notify_all()

    # -- admission -----------------------------------------------------

    def acquire(
        self,
        *,
        requested: int = 1,
        session_id: str = "",
        turn_id: str = "",
        label: str = "",
        timeout: Optional[float] = None,
    ) -> Admission:
        """Take up to ``requested`` slots, waiting at most ``timeout`` seconds.

        ``requested`` is clamped to the cap rather than refused — asking for
        more children than the cap is legal, you just cannot hold more slots
        than exist.
        """
        want = max(1, min(int(requested or 1), self._cap))
        limit = self._queue_timeout if timeout is None else max(0.0, float(timeout))
        start = self._clock()
        deadline = start + limit

        with self._cv:
            self._expire_locked()
            ticket = next(self._tickets)
            self._waiters[ticket] = _Waiter(ticket, want)
            try:
                while True:
                    now = self._clock()
                    waited = now - start
                    self._expire_locked()
                    if self._can_grant_locked(ticket, want):
                        lease = Lease(
                            id=uuid.uuid4().hex[:12],
                            session_id=session_id,
                            turn_id=turn_id,
                            slots=want,
                            created_at=now,
                            label=label,
                        )
                        self._leases[lease.id] = lease
                        return Admission(
                            True, "granted", want, waited,
                            self._active_locked(), self._cap, lease.id,
                        )
                    remaining = deadline - now
                    if remaining <= 0:
                        return Admission(
                            False, "concurrency-cap", 0, waited,
                            self._active_locked(), self._cap, None,
                        )
                    self._cv.wait(min(remaining, _WAIT_SLICE))
            finally:
                self._waiters.pop(ticket, None)

    def _can_grant_locked(self, ticket: int, want: int) -> bool:
        """FIFO with no head-of-line blocking.

        A waiter may proceed when it fits AND no earlier waiter also fits — so
        a request for 6 slots never strands a later request for 1 behind it.
        """
        active = self._active_locked()
        if active + want > self._cap:
            return False
        for other in self._waiters.values():
            if other.ticket == ticket:
                return True
            if active + other.want <= self._cap:
                return False
        return True

    # -- release -------------------------------------------------------

    def release_for(self, session_id: str, turn_id: str = "") -> int:
        """Release one slot, preferring an exact (session, turn) lease."""
        with self._cv:
            lease = self._match_lease_locked(session_id, turn_id)
            if lease is None:
                return 0
            lease.slots -= 1
            if lease.slots <= 0:
                del self._leases[lease.id]
            self._cv.notify_all()
            return 1

    def release_lease(self, lease_id: str) -> int:
        with self._cv:
            lease = self._leases.pop(lease_id, None)
            if lease is None:
                return 0
            self._cv.notify_all()
            return lease.slots

    def release_session(self, session_id: str) -> int:
        """Drop every lease held by a session (its ``on_session_end`` hook)."""
        with self._cv:
            freed = 0
            for lease_id in [
                lid
                for lid, lease in self._leases.items()
                if lease.session_id == session_id
            ]:
                freed += self._leases.pop(lease_id).slots
            if freed:
                self._cv.notify_all()
            return freed

    def _match_lease_locked(self, session_id: str, turn_id: str) -> Optional[Lease]:
        if not session_id:
            return None
        exact = [
            lease
            for lease in self._leases.values()
            if lease.session_id == session_id and lease.turn_id == turn_id
        ]
        pool = exact or [
            lease for lease in self._leases.values() if lease.session_id == session_id
        ]
        if not pool:
            return None
        return min(pool, key=lambda lease: lease.created_at)

    def _expire_locked(self, now: Optional[float] = None) -> None:
        """Reclaim slots whose children never reported back."""
        now = self._clock() if now is None else now
        stale = [
            lease_id
            for lease_id, lease in self._leases.items()
            if now - lease.created_at > self._lease_ttl
        ]
        if not stale:
            return
        for lease_id in stale:
            del self._leases[lease_id]
        self._cv.notify_all()


_DEFAULT_GUARD: Optional[DelegationGuard] = None
_DEFAULT_LOCK = threading.Lock()


def get_guard() -> DelegationGuard:
    """Process-wide guard, seeded from config on first use."""
    global _DEFAULT_GUARD
    if _DEFAULT_GUARD is None:
        with _DEFAULT_LOCK:
            if _DEFAULT_GUARD is None:
                from plobi.delegation import config

                cfg = config.load()
                _DEFAULT_GUARD = DelegationGuard(
                    cap=cfg["global_max_children"],
                    queue_timeout=cfg["queue_timeout_seconds"],
                    lease_ttl=cfg["lease_ttl_seconds"],
                )
    return _DEFAULT_GUARD


def set_guard(guard: Optional[DelegationGuard]) -> None:
    global _DEFAULT_GUARD
    _DEFAULT_GUARD = guard
