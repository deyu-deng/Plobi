"""Derive the desktop-quota source list from the local gateway (裁定 46).

裁定 46 says the list of quota-bearing desktop apps has **one** source of truth —
the gateway itself — and forbids a second copy maintained elsewhere. Until now
``plobi/quota/config.py`` carried exactly such a copy (hardcoded ``antigravity``
/ ``workbuddy`` entries), which is 裁定 46.3 登记为「未落地」的那半件事.

This module replaces that copy: ask the gateway ``GET /v1/models`` and derive one
source per **``provider``**. Two details learned from the live gateway, both
load-bearing:

* Group by the ``provider`` field, **not** by the ``<app>/`` id prefix. The mock
  provider currently serves ``workbuddy/deepseek-chat`` (see
  ``aigw/config.yaml``), so prefix-matching reads a fake channel as a real one.
* ``capabilities`` is a *claim about the adapter*, not proof a chat round-trip
  works. A derived source is therefore at best DEGRADED (「存在 ≠ 能用」,
  ``aigw/docs/README.md`` 诚实清单).

Three outcomes are kept distinct on purpose — 埋雷清单 R-048 forbids both folding
a fault into "not wired yet" and painting "not wired yet" as a fault:
``OK`` (answered; the list, possibly empty), ``UNREACHABLE`` (no listener),
``REJECTED`` (answered but wrong — bad key, non-200, unparseable body).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

#: Local loopback gateways answer instantly; this only bounds a wedged process.
DEFAULT_TIMEOUT = 2.5

#: The gateway's own dev/demo channel — never a spendable quota source.
MOCK_PROVIDER = "mock"


class CatalogState(str, Enum):
    OK = "ok"
    UNREACHABLE = "unreachable"
    REJECTED = "rejected"
    #: ``gateway.enabled: false`` — a choice, not a fault and not an empty list.
    DISABLED = "disabled"


@dataclass(frozen=True)
class GatewayOutcome:
    """One catalog read. ``sources`` is only meaningful when ``state`` is OK."""

    state: CatalogState
    sources: dict[str, dict[str, Any]] = field(default_factory=dict)
    detail: str = ""
    checked_at: float = 0.0
    models_seen: int = 0

    @property
    def answered(self) -> bool:
        """Did the gateway actually reply? Empty-but-answered ≠ unreachable."""
        return self.state is CatalogState.OK

    def as_dict(self) -> dict:
        return {
            "state": self.state.value,
            "detail": self.detail,
            "checked_at": self.checked_at,
            "models_seen": self.models_seen,
            "source_names": sorted(self.sources),
        }


def catalog_url(base_url: str) -> str:
    """Models endpoint for a gateway base URL (which may or may not end in /v1)."""
    base = (base_url or "").rstrip("/")
    if not base:
        return ""
    return f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"


def _pick_model(entries: list[dict]) -> tuple[str, dict]:
    """Deterministic representative: tool-capable first, then sessionful, then id.

    Ordered by capability rather than by arrival because ``served_models`` is a
    plain list whose order comes from provider config, and a source that silently
    changes model between two catalog reads would move the L2 loop's context.
    """
    scored = sorted(
        entries,
        key=lambda e: (
            not (e[1].get("tools") or e[1].get("sessionful")),
            e[0],
        ),
    )
    return scored[0]


def sources_from_catalog(
    payload: Any, *, base_url: str, api_key: str
) -> tuple[dict[str, dict[str, Any]], int]:
    """Group the catalog by ``provider`` into one source dict per app.

    Returns ``(sources, models_seen)`` where ``models_seen`` counts every entry
    including the mock ones — so a caller can tell "gateway serves only mock"
    apart from "gateway serves nothing".
    """
    if isinstance(payload, dict):
        rows = payload.get("data") or payload.get("models") or []
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = []
    grouped: dict[str, list[tuple[str, dict]]] = {}
    seen = 0
    for row in rows:
        if isinstance(row, str):
            provider, model_id, caps = "", row, {}
        elif isinstance(row, dict):
            model_id = str(row.get("id") or "")
            provider = str(row.get("provider") or "").strip().lower()
            caps = row.get("capabilities") if isinstance(row.get("capabilities"), dict) else {}
        else:
            continue
        if not model_id:
            continue
        seen += 1
        if not provider or provider == MOCK_PROVIDER:
            continue
        # No fabricated defaults: an adapter that reports no capabilities gets an
        # empty claim dict, which reads as "unknown" downstream rather than "no
        # streaming".
        grouped.setdefault(provider, []).append((model_id, dict(caps)))

    sources: dict[str, dict[str, Any]] = {}
    for provider, entries in grouped.items():
        model, caps = _pick_model(entries)
        sources[provider] = {
            "kind": "aigw",
            "model": model,
            "models": [m for m, _ in sorted(entries, key=lambda e: e[0])],
            "base_url": base_url,
            "api_key": api_key,
            "capabilities": dict(caps),
            "derived": True,
        }
    return sources, seen


def fetch_sources(
    base_url: str,
    api_key: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    open_url: Optional[Callable[[urllib.request.Request, float], Any]] = None,
) -> GatewayOutcome:
    """Read the gateway catalog once. Never raises — an unreadable gateway is data.

    ``open_url`` is the test seam (``(request, timeout) -> context manager``).
    """
    now = time.time()
    url = catalog_url(base_url)
    if not url:
        return GatewayOutcome(
            CatalogState.UNREACHABLE, {}, "no gateway base_url configured", now
        )

    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    # The gateway key is a local shared secret, not a per-user credential; the
    # loopback hub is deliberately key-optional (裁定 46.1), so send it only
    # when one is actually configured.
    if api_key:
        request.add_header("Authorization", f"Bearer {api_key}")

    try:
        if open_url is not None:
            response = open_url(request, timeout)
        else:
            from plobi_cli.urllib_security import open_credentialed_url

            response = open_credentialed_url(request, timeout=timeout)
        with response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        code = getattr(exc, "code", "?")
        reason = "gateway rejected the key" if code in (401, 403) else f"HTTP {code}"
        return GatewayOutcome(CatalogState.REJECTED, {}, f"{url} {reason}", now)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return GatewayOutcome(
            CatalogState.UNREACHABLE, {}, f"{url} unreachable ({exc})", now
        )
    except (ValueError, UnicodeDecodeError) as exc:
        return GatewayOutcome(CatalogState.REJECTED, {}, f"{url} bad JSON ({exc})", now)

    sources, seen = sources_from_catalog(payload, base_url=base_url, api_key=api_key)
    return GatewayOutcome(
        CatalogState.OK,
        sources,
        f"{url} answered with {seen} models, {len(sources)} quota app(s)",
        now,
        seen,
    )
