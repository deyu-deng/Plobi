"""Quota circuit breaker for delegation (B4 ships the seam).

B4 delivers the seam plus one default provider: a configured daily budget. B3
plugs the real sources (cheap API + aigw aggregation) in through
:func:`register_provider` — nothing here has to change for that.

Unknown quota never blocks. A provider reporting ``None`` (no telemetry yet)
is treated as "allow": refusing work on missing data would stall the system
for no reason, and B3's probes are exactly what will start reporting numbers.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Iterable, List, Optional, Protocol


@dataclass(frozen=True)
class QuotaVerdict:
    allowed: bool
    reason: str
    remaining: Optional[float]
    source: str


class QuotaProvider(Protocol):
    """A source of spendable budget. ``None`` means "unknown", not "empty"."""

    name: str

    def remaining_usd(self) -> Optional[float]: ...


class BudgetProvider:
    """Default B4 provider: a fixed daily budget minus recorded spend."""

    def __init__(self, budget_usd: float) -> None:
        self.name = "budget"
        self.budget_usd = float(budget_usd)
        self._spent = 0.0
        self._lock = threading.Lock()

    def remaining_usd(self) -> float:
        with self._lock:
            return self.budget_usd - self._spent

    def record(self, usd: float) -> None:
        with self._lock:
            self._spent += max(0.0, float(usd or 0.0))

    def reset(self) -> None:
        with self._lock:
            self._spent = 0.0


class QuotaCircuit:
    """Breaker over one or more providers — trips on the *worst* remaining.

    Tripping is sticky (that is what makes it a breaker rather than a check):
    once a source reports exhausted, subsequent delegations are refused until
    :meth:`reset` runs (B5's daily cron is the natural place to call it).
    """

    def __init__(self, providers: Iterable[QuotaProvider] = ()) -> None:
        self._providers: List[QuotaProvider] = list(providers)
        self._lock = threading.Lock()
        self._tripped = False
        self._trip_reason = ""

    def register(self, provider: QuotaProvider) -> None:
        with self._lock:
            self._providers.append(provider)

    def providers(self) -> List[QuotaProvider]:
        with self._lock:
            return list(self._providers)

    def record_spend(self, usd: float) -> None:
        """Fold real spend into every provider that can take it."""
        for provider in self.providers():
            record = getattr(provider, "record", None)
            if callable(record):
                try:
                    record(usd)
                except Exception:  # a bad provider must not break delegation
                    continue

    def check(self) -> QuotaVerdict:
        with self._lock:
            if self._tripped:
                return QuotaVerdict(
                    False, self._trip_reason or "quota breaker tripped", 0.0, "breaker"
                )

            worst: Optional[float] = None
            source = "none"
            for provider in self._providers:
                try:
                    remaining = provider.remaining_usd()
                except Exception:
                    continue
                if remaining is None:
                    continue
                if worst is None or float(remaining) < worst:
                    worst = float(remaining)
                    source = getattr(provider, "name", "provider")

            if worst is None:
                return QuotaVerdict(True, "no quota data", None, "none")
            if worst <= 0:
                self._tripped = True
                self._trip_reason = f"{source} 额度耗尽"
                return QuotaVerdict(False, self._trip_reason, worst, source)
            return QuotaVerdict(True, "ok", worst, source)

    def trip(self, reason: str = "manual trip") -> None:
        with self._lock:
            self._tripped = True
            self._trip_reason = reason

    def reset(self) -> None:
        with self._lock:
            self._tripped = False
            self._trip_reason = ""

    @property
    def tripped(self) -> bool:
        with self._lock:
            return self._tripped


_DEFAULT_CIRCUIT: Optional[QuotaCircuit] = None
_DEFAULT_LOCK = threading.Lock()


def get_quota_circuit() -> QuotaCircuit:
    """Process-wide breaker, lazily seeded with the configured budget provider."""
    global _DEFAULT_CIRCUIT
    if _DEFAULT_CIRCUIT is None:
        with _DEFAULT_LOCK:
            if _DEFAULT_CIRCUIT is None:
                from plobi.delegation import config

                budget = config.load()["quota_daily_budget_usd"]
                _DEFAULT_CIRCUIT = QuotaCircuit([BudgetProvider(budget)])
    return _DEFAULT_CIRCUIT


def set_quota_circuit(circuit: Optional[QuotaCircuit]) -> None:
    global _DEFAULT_CIRCUIT
    _DEFAULT_CIRCUIT = circuit
