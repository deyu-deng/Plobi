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

log = logging.getLogger("bezel")

MODEL_HEALTH_ENV = "BEZEL_MODEL_HEALTH"

#: How long a refused id stays out of the picker. Long enough to survive a restart,
#: short enough that an upstream that re-enables a model is not blocked forever.
DEFAULT_COOLDOWN = 6 * 3600.0
#: One refusal can be a hiccup; two in a row is a pattern worth acting on.
THRESHOLD = 2

# Only errors about the model itself count. Measured against this hub's own upstream
# shapes: `... model 'x' not found`, `invalid model`, `模型不存在`.
_MODEL_HINT = re.compile(r"\bmodel\b|模型|deployment|engine", re.I)
#: A *permission* verdict on one id: the model exists, this account may not use it.
#: Measured 2026-11-24: opencode answers 403 "entitlement/forbidden", Devin answers 502
#: "no longer available for your account" (bezel turns that into a 404 before it lands
#: here). Waiting cannot change either one, so one refusal is already the whole answer.
_ENTITLEMENT_HINT = re.compile(
    r"entitlement|forbidden|not available for your account|no longer available|"
    r"not available in your (?:country|region)|geo.?block|not subscribed|no permission|"
    r"未开通|无权限|不可用",
    re.I,
)
#: ...but a dead *key* refuses every id at once. Quarantining a whole catalogue because
#: a token expired would be its own kind of lie, so those never count as model verdicts.
_CREDENTIAL_HINT = re.compile(
    r"api.?key|unauthori[sz]ed|authenticat|credential|\btoken\b", re.I
)
_MODEL_STATUSES = (400, 403, 404)


def _is_entitlement_refusal(status: object, message: str) -> bool:
    """Does this say "you may not use this model" rather than "your key is wrong"?"""
    try:
        code = int(status)
    except (TypeError, ValueError):
        return False
    if code not in (402, 403, 404) and code < 500:
        # 5xx gets through on purpose: Devin wraps its account verdict in the gateway's
        # own 502 ("This model is no longer available for your account", measured
        # 2026-11-24), and the sentence — not the status — settles the question. The
        # credential check below still catches an expired token dressed up as a 5xx.
        return False
    text = message or ""
    return bool(_ENTITLEMENT_HINT.search(text)) and not _CREDENTIAL_HINT.search(text)


def health_path() -> Path:
    """Where the ledger lives (resolved per call so tests can point it at a tmp dir).

    Precedence: ``BEZEL_MODEL_HEALTH`` → ``PLOBI_HOME/bezel-model-health.json``
    → ``~/.bezel/model-health.json``. No literal POSIX path: on this machine those
    resolve onto the wrong drive (裁定 79).
    """
    explicit = os.environ.get(MODEL_HEALTH_ENV, "").strip()
    if explicit:
        return Path(explicit)
    home = os.environ.get("PLOBI_HOME", "").strip()
    if home:
        return Path(home) / "bezel-model-health.json"
    return Path.home() / ".bezel" / "model-health.json"


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
    if code not in _MODEL_STATUSES:
        # A vendor's refusal can arrive wrapped in the gateway's own 502 ("Devin: This
        # model is no longer available for your account", measured 2026-11-24). The status
        # belongs to the transport, the sentence belongs to the vendor — and only the
        # sentence says whether the id is ours to use. Only 5xx gets this reading: a 401
        # is about the key, not the model.
        if code < 500:
            return False
        return bool(_ENTITLEMENT_HINT.search(message or ""))
    if _is_entitlement_refusal(code, message):
        return True
    return bool(_MODEL_HINT.search(message or ""))


def seat_refusal(status: object, message: str) -> bool:
    """Was the *account* turned down, rather than this one id?

    A seat verdict settles every model behind it, so callers should stop asking siblings
    the same question and pay for one answer instead of N.
    """
    return _is_entitlement_refusal(status, message)


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
    # A permission verdict is a fact, not a pattern to be confirmed: charging the user a
    # second identical failure before acting is precisely how a picker stays green.
    needed = 1 if _is_entitlement_refusal(status, message) else THRESHOLD
    if fails >= needed:
        until = time.time() + cooldown
    data[model] = {
        **entry,
        "fails": fails,
        "until": until,
        "last_status": int(status),
        # The upstream's own words. Without them the picker could only ever say
        # "model_rejected", which is a shrug dressed as an explanation.
        "last_message": str(message or "")[:300],
        "last_seen": time.time(),
    }
    if _is_entitlement_refusal(status, message):
        # A region or entitlement verdict does not expire with the cooldown: the same
        # account will still be refused tomorrow, so a 90-minute clock would hand the
        # picker a green lamp over a model nobody can call. Keep it off until a real turn
        # comes back (record_success replaces the entry wholesale).
        data[model].update(
            unproven=True,
            unproven_reason=(
                f"upstream refused this model ({status}): {str(message or '')[:200]}"
            ),
            unproven_at=time.time(),
        )
    _write(path, data)
    if until > time.time():
        log.info("model %s quarantined for %.0fs after %d refusals", model, cooldown, fails)
        return True
    return False


def record_unproven(model: str, reason: str) -> None:
    """Remember that a probe could not get an answer out of this id.

    Deliberately *not* :func:`record_refusal`: a timeout is not a verdict about a model,
    and starting a 24 h cooling clock because a reasoning model thought too long would
    be its own kind of lie. It does keep the lamp off — a picker may only offer what was
    proven to answer, and "we never got an answer" is not proof.
    """
    if not model:
        return
    path = health_path()
    data = _read(path)
    entry = _entry(data, model)
    data[model] = {
        **entry,
        "unproven": True,
        "unproven_reason": str(reason or "")[:300],
        "unproven_at": time.time(),
    }
    _write(path, data)


def unproven_reason(model: str) -> str:
    """Why this id has no proof of life yet — empty when it has some."""
    entry = _read(health_path()).get(model) or {}
    if not entry.get("unproven"):
        return ""
    why = str(entry.get("unproven_reason") or "").strip()
    return f"not proven to answer yet ({why})" if why else "not proven to answer yet"


def refusal_reason(model: str) -> str:
    """Why this id is out, in the upstream's words — empty when it is not out."""
    entry = _read(health_path()).get(model) or {}
    until = float(entry.get("until") or 0.0)
    now = time.time()
    if until <= now:
        return ""
    left = until - now
    message = str(entry.get("last_message") or "").strip()
    status = entry.get("last_status")
    head = f"upstream refused this model ({status})"
    if message:
        head += f": {message}"
    return f"{head} — back in {left / 3600:.1f}h"


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
