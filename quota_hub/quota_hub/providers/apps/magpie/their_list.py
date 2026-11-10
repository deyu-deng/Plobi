"""Which providers magpie has today — read from their own files, never guessed.

magpie keeps the roster of its provider plugins in two places we can read
without a network call and without a browser:

1. ``plugin-providers.json`` — the plugin host's own table, written next to the
   rest of its data (one row per provider: its id, its name, its models and
   whether it is signed in). It is the richest source and it costs no
   subprocess: we already know that directory — the desktop pins
   ``XDG_CONFIG_HOME`` to our own volume, so magpie's data lands next to the
   settings file we read the port from.
2. ``magpie plugin list`` — the CLI's own table, one line per provider::

       workbuddy-ai (WorkBuddy AI, 16 models)  not signed in · magpie plugin login workbuddy-ai
       opencode-zen-free (OpenCode Zen Free, 11 models)  signed in

   Their plugin host's logs share that stream (a plugin that cannot load prints
   its own banner there), so only the lines with that shape are read.

Neither shape is a contract we may lean on — magpie ships several releases a
day — so both are read defensively: anything we do not recognise yields "we
could not ask" (``None``) rather than a guess. The **card list** is our own
table either way (``providers.yaml``); this only answers "does magpie still
have this one", and that answer must never be the reason a vendor's card
disappears from the user's screen.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

#: The plugin host's table, next to ``settings.json`` in magpie's data dir.
PROVIDERS_FILE = "plugin-providers.json"

#: One line of their printed table: ``id (Name, N models)``. The name may itself
#: carry brackets (``Grok (SuperGrok), 1 models``), so the shape is matched from
#: the *end* of the bracket — the count of models is what closes it.
_CLI_ROW = re.compile(r"^\s*([A-Za-z0-9_.@-]+)\s+\((.*),\s*(\d+)\s+models?\)")


def read_provider_table(config_root: Path | str) -> dict[str, dict] | None:
    """Their own table of providers, keyed by id — or None when there is none.

    ``None`` means "we could not read it" (the file is not there, or it is not
    the shape we know). An empty dict means "we read it and it is empty" — the
    caller treats that as the same thing: a machine whose plugin host has not
    finished loading must not be told that every provider is gone.
    """
    path = Path(config_root) / "magpie" / PROVIDERS_FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(raw, list):
        return None
    table: dict[str, dict] = {}
    for row in raw:
        if not isinstance(row, dict):
            continue
        pid = str(row.get("id") or "").strip()
        if pid:
            table[pid] = row
    return table


def ids_from_table(table: dict[str, dict]) -> set[str]:
    """The provider ids in their table."""
    return set(table)


def parse_cli_table(text: str) -> set[str]:
    """The provider ids in the CLI's printed table.

    Only the rows with that shape are read; their host's logs share the stream.
    """
    ids: set[str] = set()
    for line in (text or "").splitlines():
        match = _CLI_ROW.match(line)
        if match:
            ids.add(match.group(1))
    return ids


def cli_provider_ids(binary: str, env: dict | None = None, timeout: float = 120.0) -> set[str] | None:
    """Ask their CLI once and read the provider ids out of its table.

    ``None`` means the CLI could not be run, or printed no table we recognise —
    the caller then says "we could not ask" instead of "they have none".
    """
    try:
        import subprocess  # noqa: PLC0415 - a sync call in a sync helper

        out = subprocess.run(
            [binary, "plugin", "list"],
            capture_output=True,
            timeout=timeout,
            env=env,
        )
    except Exception:
        return None
    ids = parse_cli_table((out.stdout or b"").decode("utf-8", "replace"))
    return ids or None
