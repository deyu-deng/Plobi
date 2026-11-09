"""Leg-level cooldowns: which subscription leg is parked until when, and why.

The per-model ledger (:mod:`model_health`) remembers that an upstream refused one
model id.  This one remembers the other half: the **whole leg** said "not now" —
the free pool ran out (HTTP 429 with a ``retry-after``), and it said how long that
lasts.

Why it has to be on disk: the Zen free pool is a shared bucket that refills on its
own (measured 2026-11-08 on this machine: the upstream answers 429 with
``retry-after`` ≈ 10.7 h).  A restart used to forget that, so the picker offered
that leg's models again and the user only learned the truth by clicking one and
getting an error — the opposite of 「开关打开就一定要能用」 (user, 2026-11-08 21:2x).

Shape on disk: ``{leg_id: {"until": <epoch seconds>, "reason": "<machine code>"}}``.
The reason stays a machine code; the screens translate it (裁定: 机器词不上屏).

Precedence for the path: ``QUOTA_HUB_LEG_COOLDOWN`` → ``PLOBI_HOME/quota_hub-leg-cooldowns.json``
→ ``~/.quota_hub/leg-cooldowns.json``.  No literal POSIX path: on this machine those
resolve onto the wrong drive (裁定 79).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time

from pathlib import Path

log = logging.getLogger("quota_hub")

COOLDOWN_ENV = "QUOTA_HUB_LEG_COOLDOWN"


def cooldown_path() -> Path:
    """Where the leg-cooldown ledger lives (resolved per call, so tests can redirect it)."""
    explicit = os.environ.get(COOLDOWN_ENV, "").strip()
    if explicit:
        return Path(explicit)
    home = os.environ.get("PLOBI_HOME", "").strip()
    if home:
        return Path(home) / "quota_hub-leg-cooldowns.json"
    return Path.home() / ".quota_hub" / "leg-cooldowns.json"


def _read(path: Path) -> dict:
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, FileNotFoundError):
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        log.warning("leg-cooldown file at %s is not readable JSON; treating it as empty", path)
        return {}
    return data if isinstance(data, dict) else {}


def _write(path: Path, data: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".leg-cooldown-", suffix=".json", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(data, ensure_ascii=False, indent=1))
            os.replace(tmp, path)
        except OSError:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
    except OSError as exc:
        log.warning("could not write the leg-cooldown ledger at %s: %s", path, exc)


def note(leg: str, seconds: float, reason: str = "quota_exhausted") -> None:
    """Record that this leg is parked for ``seconds`` (from now), and why.

    A longer existing cooldown is never shortened by a shorter new one: the
    upstream is the only authority on how long the bucket stays empty, and a
    later, smaller ``retry-after`` must not unlock the leg early.
    """
    if not leg:
        return
    try:
        wait = float(seconds)
    except (TypeError, ValueError):
        return
    if wait <= 0:
        return
    path = cooldown_path()
    data = _read(path)
    entry = data.get(leg)
    previous = entry.get("until") if isinstance(entry, dict) else None
    until = time.time() + wait
    if isinstance(previous, (int, float)) and float(previous) > until:
        until = float(previous)
    data[leg] = {"until": until, "reason": reason}
    _write(path, data)
    log.info("[%s] leg parked for %.0fs (%s)", leg, wait, reason)


def until(leg: str) -> float | None:
    """The epoch second this leg's cooldown ends, or None when it is not parked."""
    if not leg:
        return None
    entry = _read(cooldown_path()).get(leg)
    if not isinstance(entry, dict):
        return None
    value = entry.get("until")
    if not isinstance(value, (int, float)):
        return None
    return float(value)


def remaining(leg: str) -> float:
    """Seconds left on this leg's cooldown; 0 when there is none (or it expired)."""
    deadline = until(leg)
    if deadline is None:
        return 0.0
    left = deadline - time.time()
    return left if left > 0 else 0.0


def reason_for(leg: str) -> str:
    """Why this leg is parked (machine code), or "" when it is not."""
    entry = _read(cooldown_path()).get(leg)
    if not isinstance(entry, dict):
        return ""
    value = entry.get("reason")
    return str(value) if isinstance(value, str) else ""


def clear(leg: str) -> None:
    """Forget this leg's cooldown — a served turn is the proof that it is back."""
    if not leg:
        return
    path = cooldown_path()
    data = _read(path)
    if leg in data:
        data.pop(leg, None)
        _write(path, data)
        log.info("[%s] leg cooldown cleared", leg)
