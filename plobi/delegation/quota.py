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
    #: What the breaker has been fed since it was created (None = never told).
    spent: Optional[float] = None
    #: The configured daily budget behind `remaining`, when a provider reports one.
    budget: Optional[float] = None
    #: The delegation that last moved the number, so a refusal can name a cost
    #: instead of only a verdict ("this one is refused" is not "this one cost X").
    last_spend: Optional[float] = None


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
        # The breaker's own ledger of what it was fed. Providers keep their own
        # counters (a provider may ignore `record`); this is the one the refusal
        # message reads from, so a user gets a number instead of "额度耗尽".
        self._spent = 0.0
        self._last_spend: Optional[float] = None

    @property
    def spent_usd(self) -> float:
        with self._lock:
            return self._spent

    @property
    def last_spend_usd(self) -> Optional[float]:
        with self._lock:
            return self._last_spend

    def register(self, provider: QuotaProvider) -> None:
        with self._lock:
            self._providers.append(provider)

    def providers(self) -> List[QuotaProvider]:
        with self._lock:
            return list(self._providers)

    def record_spend(self, usd: float) -> None:
        """Fold real spend into every provider that can take it.

        This is the ledger feed the breaker was missing: delegation ran, children
        billed, and `remaining_usd()` still answered the full budget because
        nothing ever called in here. Call it once per delegation, at the layer
        that already aggregates its children — see `tools/delegate_tool.py`.
        """
        try:
            amount = float(usd or 0.0)
        except (TypeError, ValueError):
            return
        if amount <= 0.0:
            return

        with self._lock:
            self._spent += amount
            self._last_spend = amount

        for provider in self.providers():
            record = getattr(provider, "record", None)
            if callable(record):
                try:
                    record(amount)
                except Exception:  # a bad provider must not break delegation
                    continue

    def check(self) -> QuotaVerdict:
        with self._lock:
            if self._tripped:
                return QuotaVerdict(
                    False,
                    self._trip_reason or "quota breaker tripped",
                    0.0,
                    "breaker",
                    spent=self._spent,
                    budget=self._budget_unlocked(),
                    last_spend=self._last_spend,
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

            spent = self._spent
            last = self._last_spend

            if worst is None:
                return QuotaVerdict(True, "no quota data", None, "none", spent=spent or None, last_spend=last)
            if worst <= 0:
                budget = self._budget_unlocked()
                # Two numbers, not one mood: what is left and what the last
                # delegation actually cost — the sentence R-073 asked for. A
                # negative remaining is an overshoot, not a balance: show zero and
                # name the overage, or the user reads "-$0.50 还剩" as a second bug.
                over = max(0.0, -float(worst))
                detail = (
                    f"{source} 额度耗尽：今日预算 {self._money(budget)}，已花 {self._money(spent)}，"
                    f"还剩 {self._money(0.0)}"
                )
                if over > 0.0:
                    detail += f"（这一单超出 {self._money(over)}）"
                if last is not None:
                    detail += f"；上一单实花 {self._money(last)}"
                self._tripped = True
                self._trip_reason = detail
                return QuotaVerdict(
                    False, detail, worst, source, spent=spent, budget=budget, last_spend=last
                )
            return QuotaVerdict(
                True,
                "ok",
                worst,
                source,
                spent=spent or None,
                budget=self._budget_unlocked(),
                last_spend=last,
            )

    def _budget_unlocked(self) -> Optional[float]:
        """The configured daily budget across providers (caller holds the lock)."""
        total = 0.0
        seen = False
        for provider in self._providers:
            value = getattr(provider, "budget_usd", None)
            if isinstance(value, (int, float)):
                total += float(value)
                seen = True
        return total if seen else None

    @staticmethod
    def _money(value: Optional[float]) -> str:
        return "未知" if value is None else f"${float(value):.2f}"

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
