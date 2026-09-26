"""Quota sources (B3): cheap API + aigw aggregation.

A ``QuotaSource`` is one thing the L2 loop can spend against. It implements B4's
``QuotaProvider`` protocol (``name`` + ``remaining_usd()``), so the same object
can be handed to ``QuotaCircuit.register()``. It adds what B4 deliberately left
out — a health probe — because aigw's GUI quotas have **no dollar balance** to
report: they are only ever healthy / degraded / unavailable.

诚实约束（对应 slice-map B3 禁区）：
- ``cheap_api`` 源（zhipu air）能查真实余额 → ``remaining_usd()`` 返回真实值；
- ``aigw`` 源（antigravity / workbuddy 免费额度）查不到精确余额 → ``remaining_usd()``
  返回 ``None``（B4 seam 的 "unknown, not empty" 语义），只有 ``probe()`` 报健康度；
- 默认探针不伪造状态：未配置凭据 → UNAVAILABLE；配置了但未真机验证 → DEGRADED。
  只有注入真实 ``probe_fn``（真机接线时）才可能 HEALTHY。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional


class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


# 健康度排序（用于预警阈值比较）：healthy < degraded < unavailable。
_HEALTH_RANK = {
    HealthStatus.HEALTHY: 0,
    HealthStatus.DEGRADED: 1,
    HealthStatus.UNAVAILABLE: 2,
}


def health_rank(status: HealthStatus) -> int:
    return _HEALTH_RANK[status]


@dataclass(frozen=True)
class SourceStatus:
    """One probe result: a source's current health + optional dollar balance."""

    name: str
    kind: str
    health: HealthStatus
    remaining_usd: Optional[float] = None
    detail: str = ""
    checked_at: float = 0.0

    @property
    def usable(self) -> bool:
        """A source is routable unless it is flat-out unavailable."""
        return self.health is not HealthStatus.UNAVAILABLE

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "health": self.health.value,
            "remaining_usd": self.remaining_usd,
            "detail": self.detail,
            "checked_at": self.checked_at,
        }


class QuotaSource:
    """Base. Subclasses decide how to probe and whether a balance exists."""

    name: str = "source"
    kind: str = "aigw"  # cheap_api | aigw

    def __init__(
        self,
        name: str,
        *,
        kind: str,
        model: str = "",
        base_url: str = "",
        api_key: str = "",
        probe_fn: Optional[Callable[["QuotaSource"], SourceStatus]] = None,
    ) -> None:
        self.name = name
        self.kind = kind
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self._probe_fn = probe_fn

    # --- B4 QuotaProvider protocol --------------------------------------
    def remaining_usd(self) -> Optional[float]:
        """Spendable dollar balance, or ``None`` when unknown (never "empty")."""
        return None

    # --- introspection ---------------------------------------------------
    @property
    def endpoint(self) -> dict:
        return {"base_url": self.base_url, "api_key": self.api_key, "model": self.model}

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    def probe(self) -> SourceStatus:
        if self._probe_fn is not None:
            try:
                return self._probe_fn(self)
            except Exception as exc:  # a bad probe must never crash routing
                return SourceStatus(
                    self.name, self.kind, HealthStatus.DEGRADED, None,
                    f"probe raised: {exc}", time.time(),
                )
        return self._default_probe()

    def _default_probe(self) -> SourceStatus:
        # 诚实默认：未配置凭据 → UNAVAILABLE；配置了但未真机验证 → DEGRADED。
        if not self.configured:
            return SourceStatus(
                self.name, self.kind, HealthStatus.UNAVAILABLE, None,
                "no credential configured", time.time(),
            )
        return SourceStatus(
            self.name, self.kind, HealthStatus.DEGRADED, None,
            f"{self.kind} configured but not verified", time.time(),
        )


class CheapApiSource(QuotaSource):
    """A provider with a real dollar balance (zhipu air)."""

    def __init__(
        self,
        name: str,
        *,
        model: str = "",
        base_url: str = "",
        api_key: str = "",
        probe_fn: Optional[Callable[["QuotaSource"], SourceStatus]] = None,
        balance_fn: Optional[Callable[["QuotaSource"], Optional[float]]] = None,
    ) -> None:
        super().__init__(
            name, kind="cheap_api", model=model,
            base_url=base_url, api_key=api_key, probe_fn=probe_fn,
        )
        self._balance_fn = balance_fn
        self._cached_balance: Optional[float] = None
        self._cached_at: float = 0.0

    def remaining_usd(self) -> Optional[float]:
        """Last probed balance, or ``None`` before the first probe."""
        if self._cached_at > 0:
            return self._cached_balance
        return None

    def _default_probe(self) -> SourceStatus:
        if not self.configured:
            return SourceStatus(
                self.name, self.kind, HealthStatus.UNAVAILABLE, None,
                "no credential configured", time.time(),
            )
        # 有余额探测函数 → 真实余额；否则诚实降级（configured but unverified）。
        if self._balance_fn is None:
            return SourceStatus(
                self.name, self.kind, HealthStatus.DEGRADED, None,
                "configured but balance probe not wired", time.time(),
            )
        try:
            balance = self._balance_fn(self)
        except Exception as exc:
            return SourceStatus(
                self.name, self.kind, HealthStatus.DEGRADED, None,
                f"balance probe raised: {exc}", time.time(),
            )
        if balance is None:
            return SourceStatus(
                self.name, self.kind, HealthStatus.DEGRADED, None,
                "balance probe returned no data", time.time(),
            )
        self._cached_balance = float(balance)
        self._cached_at = time.time()
        if float(balance) <= 0:
            return SourceStatus(
                self.name, self.kind, HealthStatus.UNAVAILABLE, float(balance),
                "balance exhausted", self._cached_at,
            )
        return SourceStatus(
            self.name, self.kind, HealthStatus.HEALTHY, float(balance),
            "ok", self._cached_at,
        )


class AigwSource(QuotaSource):
    """A desktop app's free quota served through the local aigw gateway.

    No dollar balance exists — only health (token valid / account schedulable).
    ``remaining_usd()`` is always ``None`` (unknown), per the B4 seam contract.
    """

    def __init__(
        self,
        name: str,
        *,
        model: str = "",
        base_url: str = "",
        api_key: str = "",
        probe_fn: Optional[Callable[["QuotaSource"], SourceStatus]] = None,
    ) -> None:
        super().__init__(
            name, kind="aigw", model=model,
            base_url=base_url, api_key=api_key, probe_fn=probe_fn,
        )

    def _default_probe(self) -> SourceStatus:
        # aigw 的 antigravity / workbuddy 真机未跑通；诚实标注 DEGRADED。
        if not self.configured:
            return SourceStatus(
                self.name, self.kind, HealthStatus.UNAVAILABLE, None,
                "aigw gateway not configured", time.time(),
            )
        return SourceStatus(
            self.name, self.kind, HealthStatus.DEGRADED, None,
            "aigw upstream not verified on this machine", time.time(),
        )


def build_sources(config: dict) -> dict[str, QuotaSource]:
    """Build the source pool from ``config.load()`` output."""
    sources: dict[str, QuotaSource] = {}
    for name, scfg in config.get("sources", {}).items():
        kind = scfg.get("kind", "aigw")
        if kind == "cheap_api":
            cls = CheapApiSource
        else:
            cls = AigwSource
        sources[name] = cls(
            name,
            model=str(scfg.get("model") or ""),
            base_url=str(scfg.get("base_url") or ""),
            api_key=str(scfg.get("api_key") or ""),
        )
    return sources
