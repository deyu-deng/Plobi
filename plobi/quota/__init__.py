"""Quota source management (B3): source pool + probes + routing + L2 wiring.

The desktop-quota source list is derived from the local gateway, not configured
here (裁定 46 — one source of truth, and it is the gateway's ``/v1/models``).
"""

from plobi.quota.config import DEFAULT_ORDER, config_path, gateway_settings, load, load_source
from plobi.quota.gateway import CatalogState, GatewayOutcome, fetch_sources
from plobi.quota.pool import (
    QuotaPool,
    get_quota_pool,
    probe_sources,
    set_quota_pool,
)
from plobi.quota.sources import (
    AigwSource,
    CheapApiSource,
    HealthStatus,
    QuotaSource,
    SourceStatus,
    build_sources,
    health_rank,
)

__all__ = [
    "AigwSource",
    "CatalogState",
    "CheapApiSource",
    "DEFAULT_ORDER",
    "GatewayOutcome",
    "HealthStatus",
    "QuotaPool",
    "QuotaSource",
    "SourceStatus",
    "build_sources",
    "config_path",
    "fetch_sources",
    "gateway_settings",
    "get_quota_pool",
    "health_rank",
    "load",
    "load_source",
    "probe_sources",
    "set_quota_pool",
]
