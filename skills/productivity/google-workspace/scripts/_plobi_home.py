"""Resolve PLOBI_HOME for standalone skill scripts.

Skill scripts may run outside the Plobi process (e.g. system Python,
nix env, CI) where ``plobi_constants`` is not importable.  This module
provides the same ``get_plobi_home()`` and ``display_plobi_home()``
contracts as ``plobi_constants`` without requiring it on ``sys.path``.

When ``plobi_constants`` IS available it is used directly so that any
future enhancements (profile resolution, Docker detection, etc.) are
picked up automatically.  The fallback path replicates the core logic
from ``plobi_constants.py`` using only the stdlib.

All scripts under ``google-workspace/scripts/`` should import from here
instead of duplicating the ``PLOBI_HOME = Path(os.getenv(...))`` pattern.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from plobi_constants import display_plobi_home as display_plobi_home
    from plobi_constants import get_plobi_home as get_plobi_home
except (ModuleNotFoundError, ImportError):

    def get_plobi_home() -> Path:
        """Return the Plobi home directory (default: ~/.plobi).

        Mirrors ``plobi_constants.get_plobi_home()``."""
        val = os.environ.get("PLOBI_HOME", "").strip()
        return Path(val) if val else Path.home() / ".plobi"

    def display_plobi_home() -> str:
        """Return a user-friendly ``~/``-shortened display string.

        Mirrors ``plobi_constants.display_plobi_home()``."""
        home = get_plobi_home()
        try:
            return "~/" + str(home.relative_to(Path.home()))
        except ValueError:
            return str(home)
