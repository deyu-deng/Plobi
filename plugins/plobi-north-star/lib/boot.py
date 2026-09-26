"""Load the plugin package the same way Hermes PluginManager does."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path


def load_package():
    """Return the loaded ``hermes_plugins.plobi_north_star`` module."""
    name = "hermes_plugins.plobi_north_star"
    if name in sys.modules and hasattr(sys.modules[name], "register"):
        return sys.modules[name]

    root = Path(__file__).resolve().parent.parent
    if "hermes_plugins" not in sys.modules:
        ns = types.ModuleType("hermes_plugins")
        ns.__path__ = []  # type: ignore[attr-defined]
        sys.modules["hermes_plugins"] = ns

    spec = importlib.util.spec_from_file_location(
        name,
        root / "__init__.py",
        submodule_search_locations=[str(root)],
    )
    if spec is None or spec.loader is None:
        raise ImportError("cannot load plobi-north-star")
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = name
    mod.__path__ = [str(root)]  # type: ignore[attr-defined]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def get_north_star():
    load_package()
    from hermes_plugins.plobi_north_star.lib.facade import get_north_star as _get

    return _get()
