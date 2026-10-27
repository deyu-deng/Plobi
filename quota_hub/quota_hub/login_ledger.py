"""Who has actually signed in through Plobi, and when.

The hub's credential verdict used to be ``True, "ok"`` for every leg that did not
implement its own probe — i.e. every spawn-based CLI leg (Kiro, Cline, Codex,
Claude Code, OpenCode). The registry documented the intent ("we did not ask must
not be reported as it is broken"), but the mirror half was never written: **not
asking is equally not evidence of a sign-in**. Measured on this machine
2026-10-27 against a live hub: the settings cards for Kiro and Cline read
``live: true`` while nobody had ever signed either of them in, so the card showed
a switch, ``connectable`` was false, and the one click that would have fixed it
was gone.

This module is the honest half. A CLI leg is *verified* when the login command it
declared ran to completion with exit code 0 — that is a fact we observed, not an
assumption. Anything else is "not signed in", which puts the ``+`` back on the
card. The CLI owns its credential; we store neither, only the timestamp of the
last login we ran ourselves.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path

log = logging.getLogger("quota_hub")

LEDGER_ENV = "QUOTA_HUB_LOGIN_LEDGER"


def ledger_path() -> Path:
    """Where the ledger lives. Resolved per call, so a test can point it at a tmp dir.

    Precedence: ``QUOTA_HUB_LOGIN_LEDGER`` → ``PLOBI_HOME/quota_hub-login-ledger.json``
    → ``~/.quota_hub/login-ledger.json``. No literal POSIX path anywhere: on this
    machine ``/tmp`` resolves onto the wrong drive (裁定 79), so every landing spot
    goes through an env var or ``Path.home()``.
    """
    explicit = os.environ.get(LEDGER_ENV, "").strip()
    if explicit:
        return Path(explicit)
    home = os.environ.get("PLOBI_HOME", "").strip()
    if home:
        return Path(home) / "quota_hub-login-ledger.json"
    return Path.home() / ".quota_hub" / "login-ledger.json"


def _read(path: Path) -> dict:
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, FileNotFoundError):
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        # A torn file is not evidence about anybody's credentials. Forget, warn, move on.
        log.warning("login ledger at %s is not readable JSON; treating it as empty", path)
        return {}
    return data if isinstance(data, dict) else {}


def seen_at(app_id: str) -> float | None:
    """When Plobi last ran a successful sign-in for this leg, or None if never."""
    entry = _read(ledger_path()).get(app_id)
    if not isinstance(entry, dict):
        return None
    at = entry.get("at")
    return float(at) if isinstance(at, (int, float)) else None


def verified(app_id: str) -> bool:
    return seen_at(app_id) is not None


def remember(app_id: str) -> None:
    """Record that this leg's own login command exited 0 just now.

    Written atomically (temp file + ``os.replace``): the hub answers catalog reads
    every few seconds and a half-written ledger would read as "nobody is signed in".
    A write failure is logged and swallowed — losing the ledger must not lose a
    login the user just completed.
    """
    path = ledger_path()
    data = _read(path)
    data[app_id] = {"at": time.time()}
    _write(path, data)


def forget(app_id: str) -> None:
    """Drop the evidence: the next catalog read offers the click again."""
    path = ledger_path()
    data = _read(path)
    if data.pop(app_id, None) is not None:
        _write(path, data)


def _write(path: Path, data: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".login-ledger-", suffix=".json", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(data, ensure_ascii=False, indent=1))
            os.replace(tmp, path)
        except OSError:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
    except OSError as exc:
        log.warning("could not write the login ledger at %s: %s", path, exc)
