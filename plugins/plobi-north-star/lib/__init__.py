"""North Star implementation package.

Public entry for in-process callers: ``facade.get_north_star`` / ``NorthStar``.
UI code must use HTTP (``dashboard/plugin_api.py``), not this package.
"""

from .facade import NorthStar, get_north_star

__all__ = ["NorthStar", "get_north_star"]
