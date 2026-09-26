from __future__ import annotations

from typing import Dict, Type

from .base import SurfaceAdapter
from .browser import BrowserAdapter
from .cursor import CursorAdapter
from .marvis import MarvisAdapter

_ADAPTERS: Dict[str, Type[SurfaceAdapter]] = {
    "marvis": MarvisAdapter,
    "cursor": CursorAdapter,
    "browser": BrowserAdapter,
}


def get_adapter(surface: str) -> SurfaceAdapter:
    key = (surface or "marvis").strip().lower()
    cls = _ADAPTERS.get(key)
    if cls is None:
        raise ValueError(f"Unknown HID surface: {surface!r}. Known: {sorted(_ADAPTERS)}")
    return cls()


def list_surfaces() -> list[str]:
    return sorted(_ADAPTERS)
