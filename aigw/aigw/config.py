"""Config loader: YAML + env expansion."""
from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

_ENV = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")


def _expand(obj):
    if isinstance(obj, str):
        return _ENV.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""), obj)
    if isinstance(obj, dict):
        return {k: _expand(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand(v) for v in obj]
    return obj


def load(path: str | None = None) -> dict:
    p = Path(path or os.environ.get("AIGW_CONFIG", "config.yaml"))
    if not p.exists():
        raise FileNotFoundError(f"config not found: {p}")
    return _expand(yaml.safe_load(p.read_text()))
