"""Config loader: YAML + env expansion."""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

_ENV = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")

# Package root: quota_hub/quota_hub/config.py -> parents[1] is the project root (quota_hub/).
_PKG_ROOT = Path(__file__).resolve().parents[1]
_SAFE_PROFILE = _PKG_ROOT / "profiles" / "safe.yaml"


def _env_value(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    if value is None and name.startswith("QUOTA_HUB_"):
        value = os.environ.get("AIGW_" + name[len("QUOTA_HUB_"):])
    return value if value is not None else default


def _expand(obj):
    if isinstance(obj, str):
        return _ENV.sub(lambda m: _env_value(m.group(1), m.group(2) or ""), obj)
    if isinstance(obj, dict):
        return {k: _expand(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand(v) for v in obj]
    return obj


def resolve_config_path(path: str | None = None) -> Path:
    """Resolve which config file to load.

    Order:
      1. Explicit path / ``QUOTA_HUB_CONFIG`` (``QUOTA_HUB_CONFIG`` accepted for old scripts)
      2. ``./config.yaml`` (cwd)
      3. ``profiles/safe.yaml`` next to the package (ToS-safe default)
    """
    configured = os.environ.get("QUOTA_HUB_CONFIG") or os.environ.get("QUOTA_HUB_CONFIG")
    if path or configured:
        return Path(path or configured or "")
    cwd_cfg = Path("config.yaml")
    if cwd_cfg.exists():
        return cwd_cfg
    if _SAFE_PROFILE.exists():
        return _SAFE_PROFILE
    return cwd_cfg


def load(path: str | None = None) -> dict:
    p = resolve_config_path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"config not found: {p}\n"
            f"  hint: copy config.example.yaml -> config.yaml, or run:\n"
            f"        quota_hub start --config profiles/safe.yaml"
        )
    return _expand(yaml.safe_load(p.read_text(encoding="utf-8")))
