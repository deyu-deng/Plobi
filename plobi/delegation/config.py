"""Plobi-owned ``delegation.*`` knobs (B4: L3 delegation guard).

Core ``delegate_task`` owns ``max_concurrent_children`` and its siblings —
those cap a *single call*. B4 adds the knobs core has no notion of: a
process-wide concurrency ceiling and a quota breaker. They live in the same
``delegation:`` block so operators find them next to the core knobs.

Priority matches core: config.yaml > env (``PLOBI_DELEGATION_*``) > default.
"""

from __future__ import annotations

import os
from typing import Any

DEFAULTS: dict[str, Any] = {
    # Process-wide ceiling on concurrently running L3 children, across every L2
    # and every delegate_task call. Core's max_concurrent_children is per call.
    "global_max_children": 6,
    # Bounded wait before an over-cap request degrades to a block message.
    "queue_timeout_seconds": 30.0,
    # A slot is reclaimed if its child never reports stop (crash / wedge).
    "lease_ttl_seconds": 900.0,
    # Master switch — off means the guard only observes, never blocks.
    "guard_enabled": True,
    # Quota breaker. B4 ships the seam + this budget default; B3 plugs in the
    # real sources (cheap API + aigw) without touching this file.
    "quota_enabled": True,
    "quota_daily_budget_usd": 5.0,
    # True = degrade to allow when the breaker trips (never block on quota).
    "quota_fail_open": False,
}

_ENV_PREFIX = "PLOBI_DELEGATION_"
_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}


def _coerce(raw: Any, default: Any) -> Any:
    """Best-effort coercion; anything unparseable silently keeps the default."""
    if raw is None:
        return default
    if isinstance(default, bool):
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in _TRUTHY:
            return True
        if text in _FALSY:
            return False
        return default
    if isinstance(default, float):
        try:
            return float(raw)
        except (TypeError, ValueError):
            return default
    if isinstance(default, int):
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default
    return raw


def _section() -> dict[str, Any]:
    """Read the ``delegation:`` block through the profile-aware loader.

    Mirrors ``tools.delegate_tool._load_config``: the shared
    ``load_config_readonly`` follows the active PLOBI_HOME/profile, and
    ``PLOBI_IGNORE_USER_CONFIG=1`` keeps its contract of suppressing
    user config.yaml.
    """
    if os.environ.get("PLOBI_IGNORE_USER_CONFIG") == "1":
        return {}
    try:
        from plobi_cli.config import load_config_readonly
    except Exception:
        return {}
    try:
        full = load_config_readonly() or {}
    except Exception:
        return {}
    section = full.get("delegation") or {}
    return section if isinstance(section, dict) else {}


def load() -> dict[str, Any]:
    """Resolve every B4 knob: config.yaml > env > default."""
    section = _section()
    out: dict[str, Any] = {}
    for key, default in DEFAULTS.items():
        raw = section.get(key)
        if raw is None:
            raw = os.environ.get(_ENV_PREFIX + key.upper())
        out[key] = _coerce(raw, default)

    # A cap below 1 would stall every delegation; floor it like core does.
    out["global_max_children"] = max(1, int(out["global_max_children"]))
    out["queue_timeout_seconds"] = max(0.0, float(out["queue_timeout_seconds"]))
    out["lease_ttl_seconds"] = max(1.0, float(out["lease_ttl_seconds"]))
    return out
