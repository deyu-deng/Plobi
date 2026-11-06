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
    # 设置 → 外包 里被主人关掉的那几家（**只影响派活**，不改「装没装/登没登录」这些事实）。
    # 存「关掉的」而不是「打开的」：空列表 = 全部允许，老配置升级后行为一个字不变。
    "offload_disabled_agents": [],
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
    out["offload_disabled_agents"] = _normalise_names(out.get("offload_disabled_agents"))
    return out


def _normalise_names(raw: Any) -> list[str]:
    """名字列表收敛成「小写 + 去空白 + 去重」：配置里写成列表、写成逗号串都认。"""
    if isinstance(raw, str):
        raw = [part for part in raw.replace("\n", ",").split(",")]
    if not isinstance(raw, (list, tuple, set)):
        return []
    seen: list[str] = []
    for item in raw:
        name = str(item or "").strip().lower()
        if name and name not in seen:
            seen.append(name)
    return seen


def offload_disabled() -> set[str]:
    """设置 → 外包 里被关掉的那几家。空集 = 全部允许（默认值）。"""
    return set(load()["offload_disabled_agents"])


def set_offload_enabled(name: str, enabled: bool) -> list[str]:
    """把一家外部软件的开关写进 config.yaml 的 ``delegation.offload_disabled_agents``。

    只动这一个键，其余配置原样写回（``save_config`` 保留未知段）；返回落盘后的「关掉」名单。
    写不进去就抛 ``RuntimeError``，让路由如实回错误，不假装存下了。
    """
    target = str(name or "").strip().lower()
    if not target:
        raise ValueError("name 不能为空")
    try:
        from plobi_cli.config import load_config, save_config
    except Exception as exc:  # noqa: BLE001 - 配置模块不在就当存不下
        raise RuntimeError(f"读不到配置模块：{exc}") from exc

    current = load_config() or {}
    section = current.get("delegation")
    if not isinstance(section, dict):
        section = {}
        current["delegation"] = section
    disabled = _normalise_names(section.get("offload_disabled_agents"))
    if enabled:
        disabled = [item for item in disabled if item != target]
    elif target not in disabled:
        disabled.append(target)
    section["offload_disabled_agents"] = disabled
    save_config(current)
    try:  # 懒导入：顺手把外包那一屏的取数缓存丢掉，别让旧开关撑到 TTL 过期
        from plobi.delegation.offload_surface import forget_rows_cache

        forget_rows_cache()
    except Exception:  # noqa: BLE001 - 缓存清不掉不影响这次落盘
        pass
    return disabled
