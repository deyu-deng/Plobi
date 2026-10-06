"""Quota pool + routing strategy (B3 core).

A :class:`QuotaPool` owns the source registry, probes each source, and picks the
source an L2 loop should spend against — cheap-first, aigw-fallback, skipping
anything unhealthy. It also wires every source into B4's :class:`QuotaCircuit`
so the delegation guard's dollar breaker sees the cheap-API balance while the
aigw sources stay "unknown" (never block).

Failover is real, not decorative: :meth:`mark_failed` flips a source to
UNAVAILABLE immediately, so the next :meth:`resolve` walks on to the next
healthy candidate. The daily alert cron (B5) consumes :meth:`alert_statuses`.

The desktop-quota half of the pool is **not configured here** — it is derived
from the gateway's own catalog by :meth:`refresh_gateway` (see
:mod:`plobi.quota.gateway`, 裁定 46). Probing and resolving stay offline; only
:meth:`refresh_gateway` and ``probe_sources()`` reach the network.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

from plobi.quota.config import load
from plobi.quota.gateway import CatalogState, GatewayOutcome, fetch_sources
from plobi.quota.sources import (
    HealthStatus,
    QuotaSource,
    SourceStatus,
    build_sources,
    health_rank,
)

logger = logging.getLogger(__name__)

#: How long a derived desktop-quota catalog stays trusted before the next spend
#: re-reads it. The catalog only changes when the user connects/disconnects a
#: desktop app, so this is a latency bound, not a freshness requirement.
GATEWAY_TTL_SECONDS = 300.0

#: A failed read retries soon — the common case is the gateway still booting,
#: and caching that for the full TTL would keep the desktop channels invisible
#: for minutes after they came up.
GATEWAY_RETRY_SECONDS = 15.0


class QuotaPool:
    """Registered quota sources + cheap-first routing strategy."""

    def __init__(
        self,
        sources: Optional[dict[str, QuotaSource]] = None,
        *,
        order: Optional[list[str]] = None,
        fail_open: bool = True,
        alert_threshold: str = "degraded",
        gateway: Optional[dict] = None,
        gateway_ttl: float = GATEWAY_TTL_SECONDS,
    ) -> None:
        self.sources: dict[str, QuotaSource] = dict(sources or {})
        self.order: list[str] = list(order or list(self.sources))
        self.fail_open = fail_open
        self.alert_threshold = alert_threshold
        self.gateway = gateway or {}
        self.gateway_ttl = gateway_ttl
        self._status: dict[str, SourceStatus] = {}
        self._failed: set[str] = set()
        # Last catalog read. ``None`` means "never asked" — which the console
        # surface must be able to tell apart from "asked, and it was empty".
        self._catalog: Optional[GatewayOutcome] = None
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
    # gateway derivation (裁定 46: the gateway owns the desktop-quota list)
    # ------------------------------------------------------------------ #

    def catalog_status(self) -> Optional[dict]:
        """The last catalog read, or ``None`` when the gateway was never asked.

        Offline and non-raising on purpose: ``GET /api/quota/summary`` documents
        itself as 「探针是状态推导而非网络请求」 (WP-BE-4), so it reports the last
        known outcome instead of triggering a fetch. ``None`` must stay
        distinguishable from "asked and it had nothing" — an unchecked subsystem
        that renders as an absent one is the R-051 failure this mirrors.
        """
        with self._lock:
            return self._catalog.as_dict() if self._catalog is not None else None

    def refresh_gateway(
        self,
        *,
        force: bool = False,
        fetch: Optional[Callable[..., GatewayOutcome]] = None,
    ) -> GatewayOutcome:
        """Re-derive the desktop-quota half of the pool from the gateway catalog.

        Never raises — an unreachable gateway is data, not an exception. When the
        read fails the previously derived sources are **kept**: the router marks
        them failed on the first real error and fails over, whereas dropping them
        here would silently convert "gateway down" into "no quota apps".
        """
        settings = self.gateway or {}
        enabled = bool(settings.get("enabled", True))
        base_url = str(settings.get("base_url") or "")

        with self._lock:
            if self._catalog is not None and not force:
                bound = (
                    self.gateway_ttl
                    if self._catalog.answered
                    else min(self.gateway_ttl, GATEWAY_RETRY_SECONDS)
                )
                if time.time() - self._catalog.checked_at < bound:
                    return self._catalog

            if not enabled:
                self._catalog = GatewayOutcome(
                    CatalogState.DISABLED, {}, "gateway disabled by config", time.time()
                )
                self._apply_catalog({})
                return self._catalog
            if not base_url:
                self._catalog = GatewayOutcome(
                    CatalogState.UNREACHABLE, {}, "no gateway endpoint configured", time.time()
                )
                return self._catalog

            fetcher = fetch or fetch_sources
            outcome = fetcher(base_url, str(settings.get("api_key") or ""))
            self._catalog = outcome
            if outcome.answered:
                exclude = {str(x).strip().lower() for x in (settings.get("exclude") or [])}
                kept = {
                    name: cfg
                    for name, cfg in sorted(outcome.sources.items())
                    if name not in exclude
                }
                self._apply_catalog(kept)
            return outcome

    def _apply_catalog(self, catalog: dict[str, dict]) -> None:
        """Swap the derived sources; leave config-owned ones untouched."""
        for name in [
            n for n, s in self.sources.items() if getattr(s, "derived", False) and n not in catalog
        ]:
            self.sources.pop(name, None)
            self._status.pop(name, None)
            self._failed.discard(name)
            if name in self.order:
                self.order.remove(name)
        if not catalog:
            return
        for name, source in build_sources({"sources": catalog}).items():
            self.sources[name] = source
            # A replaced source must be re-probed, not reported from the old cache.
            self._status.pop(name, None)
            if name not in self.order:
                self.order.append(name)

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
    """Process-wide pool, lazily built from ``plobi.quota.config.load()``.

    Reaching this singleton never touches the network — the config-owned sources
    (``cheap_api``) are built and the gateway stays un-asked until something
    calls :meth:`QuotaPool.refresh_gateway`. That keeps the console quota surface
    and every unit test hermetic.
    """
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
                    gateway=cfg.get("gateway") or {},
                )
    return _POOL


def set_quota_pool(pool: Optional[QuotaPool]) -> None:
    global _POOL
    _POOL = pool


def probe_sources() -> list[SourceStatus]:
    """Convenience for the alert cron: take a fresh catalog, then probe.

    This is the observational entry point, so it is allowed to reach the gateway
    (the read never raises). Status surfaces keep using the offline
    :meth:`QuotaPool.catalog_status` instead.
    """
    pool = get_quota_pool()
    pool.refresh_gateway()
    pool.probe_all()
    return pool.statuses()
