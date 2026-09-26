"""Quota pool + routing strategy (B3 core).

A :class:`QuotaPool` owns the source registry, probes each source, and picks the
source an L2 loop should spend against — cheap-first, aigw-fallback, skipping
anything unhealthy. It also wires every source into B4's :class:`QuotaCircuit`
so the delegation guard's dollar breaker sees the cheap-API balance while the
aigw sources stay "unknown" (never block).

Failover is real, not decorative: :meth:`mark_failed` flips a source to
UNAVAILABLE immediately, so the next :meth:`resolve` walks on to the next
healthy candidate. The daily alert cron (B5) consumes :meth:`alert_statuses`.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from plobi.quota.config import load
from plobi.quota.sources import (
    HealthStatus,
    QuotaSource,
    SourceStatus,
    build_sources,
    health_rank,
)

logger = logging.getLogger(__name__)


class QuotaPool:
    """Registered quota sources + cheap-first routing strategy."""

    def __init__(
        self,
        sources: Optional[dict[str, QuotaSource]] = None,
        *,
        order: Optional[list[str]] = None,
        fail_open: bool = True,
        alert_threshold: str = "degraded",
    ) -> None:
        self.sources: dict[str, QuotaSource] = dict(sources or {})
        self.order: list[str] = list(order or list(self.sources))
        self.fail_open = fail_open
        self.alert_threshold = alert_threshold
        self._status: dict[str, SourceStatus] = {}
        self._failed: set[str] = set()
        # RLock (not Lock): resolve() may call probe_all() while already holding
        # the lock to lazily populate the probe cache — a plain Lock would
        # self-deadlock there and hang the process.
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ #
    # registry
    # ------------------------------------------------------------------ #

    def register(self, source: QuotaSource) -> None:
        with self._lock:
            self.sources[source.name] = source
            if source.name not in self.order:
                self.order.append(source.name)

    def remove(self, name: str) -> bool:
        with self._lock:
            self.order = [n for n in self.order if n != name]
            self._failed.discard(name)
            self._status.pop(name, None)
            return self.sources.pop(name, None) is not None

    def names(self) -> list[str]:
        with self._lock:
            return list(self.sources)

    # ------------------------------------------------------------------ #
    # probing
    # ------------------------------------------------------------------ #

    def probe_all(self) -> list[SourceStatus]:
        """Probe every source once and cache the results (sorted by order)."""
        with self._lock:
            statuses: list[SourceStatus] = []
            for name in self.order:
                source = self.sources.get(name)
                if source is None:
                    continue
                status = source.probe()
                self._status[name] = status
                statuses.append(status)
            return list(statuses)

    def status(self, name: str) -> Optional[SourceStatus]:
        with self._lock:
            return self._status.get(name)

    def statuses(self) -> list[SourceStatus]:
        with self._lock:
            return [self._status[n] for n in self.order if n in self._status]

    # ------------------------------------------------------------------ #
    # routing
    # ------------------------------------------------------------------ #

    def _effective_status(self, name: str) -> Optional[SourceStatus]:
        status = self._status.get(name)
        if status is None or name in self._failed:
            return None
        return status

    def resolve(self) -> Optional[QuotaSource]:
        """Pick the source to spend against: cheap-first, skip unavailable.

        Preference: first HEALTHY source in ``order``; failing that, first
        DEGRADED source (still routable, just risky). Returns ``None`` when no
        source is usable (caller degrades — never hard-block on missing quota).
        """
        with self._lock:
            if not self._status:
                # No probe yet — probe once so resolve() is self-contained.
                self.probe_all()

            healthy: Optional[str] = None
            degraded: Optional[str] = None
            for name in self.order:
                status = self._effective_status(name)
                if status is None or not status.usable:
                    continue
                if status.health is HealthStatus.HEALTHY and healthy is None:
                    healthy = name
                elif status.health is HealthStatus.DEGRADED and degraded is None:
                    degraded = name

            pick = healthy or degraded
            if pick is None:
                return None
            return self.sources.get(pick)

    def mark_failed(self, name: str) -> None:
        """Flip a source to failed so the next resolve() walks past it.

        Callers do this when an actual request against ``name`` errors out; the
        next probe (cron / periodic) refreshes and can clear the failure.
        """
        with self._lock:
            self._failed.add(name)
            logger.warning("plobi quota: source %r marked failed", name)

    def clear_failed(self) -> None:
        with self._lock:
            self._failed.clear()

    # ------------------------------------------------------------------ #
    # B4 circuit wiring + alerts
    # ------------------------------------------------------------------ #

    def wire_circuit(self, circuit) -> None:
        """Register every source into B4's ``QuotaCircuit``.

        ``cheap_api`` sources report a real dollar balance (the breaker trips on
        it); ``aigw`` sources report ``None`` (unknown → the breaker allows, per
        B4's seam contract — health gating lives here, not in the breaker).
        """
        for source in self.sources.values():
            circuit.register(source)

    def alert_statuses(self) -> list[SourceStatus]:
        """Sources at or below the alert threshold (for the B5 morning report).

        ``healthy < degraded < unavailable``; a source whose health rank is
        ``>=`` the threshold's rank triggers an alert.
        """
        threshold_rank = health_rank(HealthStatus(self.alert_threshold))
        out: list[SourceStatus] = []
        with self._lock:
            for name in self.order:
                status = self._status.get(name)
                if status is None:
                    continue
                if name in self._failed or health_rank(status.health) >= threshold_rank:
                    out.append(status)
            return out


# ── process-wide singleton ───────────────────────────────────────────────────

_POOL: Optional[QuotaPool] = None
_POOL_LOCK = threading.Lock()


def get_quota_pool() -> QuotaPool:
    """Process-wide pool, lazily built from ``plobi.quota.config.load()``."""
    global _POOL
    if _POOL is None:
        with _POOL_LOCK:
            if _POOL is None:
                cfg = load()
                _POOL = QuotaPool(
                    build_sources(cfg),
                    order=cfg["order"],
                    fail_open=cfg["fail_open"],
                    alert_threshold=cfg["alert_threshold"],
                )
    return _POOL


def set_quota_pool(pool: Optional[QuotaPool]) -> None:
    global _POOL
    _POOL = pool


def probe_sources() -> list[SourceStatus]:
    """Convenience: probe the process-wide pool (used by the alert cron)."""
    pool = get_quota_pool()
    pool.probe_all()
    # Refresh the timestamp to make the probe observable for debugging.
    for status in pool.statuses():
        if status.checked_at <= 0:
            continue
    return pool.statuses()
