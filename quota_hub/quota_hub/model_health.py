"""Per-model evidence: which ids this machine's upstream actually served or refused.

The catalog a leg declares in `config.yaml` is the operator's claim about what it
serves, not a measurement. Nothing in the hub could tell that `workbuddy/<something>`
was renamed upstream last week, so the picker would keep offering a model that fails
the first time the user tries it — the exact thing the box must not do (user
2026-10-27: 「模型框里面应该显示真正能用的模型」).

This ledger is the measurement side, built from traffic the user generates anyway:

* a turn that came back with content is **positive evidence** for that id;
* a 400/404 whose message talks about the model, seen twice in a row, is evidence the
  id is not served right now → quarantine it for a while, so it leaves the picker and
  comes back when it works again.

A rate limit, a timeout, or a dead credential is NOT evidence about a model id — those
say nothing here, because removing a perfectly good name on a 429 would be a worse lie
than the one we are fixing.

Only ids, statuses, counts and timestamps are stored. No prompt, no answer, no token.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path

log = logging.getLogger("quota_hub")

MODEL_HEALTH_ENV = "QUOTA_HUB_MODEL_HEALTH"

#: How long a refused id stays out of the picker. Long enough to survive a restart,
#: short enough that an upstream that re-enables a model is not blocked forever.
DEFAULT_COOLDOWN = 6 * 3600.0
#: One refusal can be a hiccup; two in a row is a pattern worth acting on.
THRESHOLD = 2

# Only errors about the model itself count. Measured against this hub's own upstream
# shapes: `... model 'x' not found`, `invalid model`, `模型不存在`.
_MODEL_HINT = re.compile(r"\bmodel\b|模型|deployment|engine", re.I)
_MODEL_STATUSES = (400, 404)


def health_path() -> Path:
    """Where the ledger lives (resolved per call so tests can point it at a tmp dir).

    Precedence: ``QUOTA_HUB_MODEL_HEALTH`` → ``PLOBI_HOME/quota_hub-model-health.json``
    → ``~/.quota_hub/model-health.json``. No literal POSIX path: on this machine those
    resolve onto the wrong drive (裁定 79).
    """
    explicit = os.environ.get(MODEL_HEALTH_ENV, "").strip()
    if explicit:
        return Path(explicit)
    home = os.environ.get("PLOBI_HOME", "").strip()
    if home:
        return Path(home) / "quota_hub-model-health.json"
    return Path.home() / ".quota_hub" / "model-health.json"


def _read(path: Path) -> dict:
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, FileNotFoundError):
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        log.warning("model-health file at %s is not readable JSON; treating it as empty", path)
        return {}
    return data if isinstance(data, dict) else {}


def _write(path: Path, data: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".model-health-", suffix=".json", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(data, ensure_ascii=False, indent=1))
            os.replace(tmp, path)
        except OSError:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
    except OSError as exc:
        log.warning("could not write the model-health ledger at %s: %s", path, exc)


def _entry(data: dict, model: str) -> dict:
    entry = data.get(model)
    return dict(entry) if isinstance(entry, dict) else {}


def looks_like_unknown_model(status: object, message: str) -> bool:
    """Is this refusal about the model id, as opposed to about quota or uptime?"""
    try:
        code = int(status)
    except (TypeError, ValueError):
        return False
    return code in _MODEL_STATUSES and bool(_MODEL_HINT.search(message or ""))


def record_success(model: str) -> None:
    """A turn came back with content: this id is served. Any quarantine is void."""
    if not model:
        return
    path = health_path()
    data = _read(path)
    data[model] = {"ok_at": time.time(), "fails": 0, "until": 0.0}
    _write(path, data)


def record_refusal(model: str, status: object, message: str, *, cooldown: float = DEFAULT_COOLDOWN) -> bool:
    """Fold one upstream error into the ledger. Returns True if it quarantined the id."""
    if not model or not looks_like_unknown_model(status, message):
        return False
    path = health_path()
    data = _read(path)
    entry = _entry(data, model)
    fails = int(entry.get("fails") or 0) + 1
    until = float(entry.get("until") or 0.0)
    if fails >= THRESHOLD:
        until = time.time() + cooldown
    data[model] = {**entry, "fails": fails, "until": until, "last_status": int(status), "last_seen": time.time()}
    _write(path, data)
    if until > time.time():
        log.info("model %s quarantined for %.0fs after %d refusals", model, cooldown, fails)
        return True
    return False


def quarantine_left(model: str) -> float:
    """Seconds this id still has to sit out (0.0 when it is welcome back)."""
    entry = _entry(_read(health_path()), model)
    until = float(entry.get("until") or 0.0)
    if until <= 0:
        return 0.0
    now = time.time()
    if until <= now:
        return 0.0
    return until - now


def quarantined(model: str) -> bool:
    return quarantine_left(model) > 0.0


def served(model: str) -> bool:
    """True when a turn through this id actually came back with content."""
    entry = _entry(_read(health_path()), model)
    return bool(entry.get("ok_at")) and not quarantined(model)


def clear(model: str) -> None:
    """Forget everything we think we know about one id (a config change deserves that)."""
    path = health_path()
    data = _read(path)
    if data.pop(model, None) is not None:
        _write(path, data)
