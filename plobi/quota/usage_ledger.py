"""The one place quota spend is written down (WP-L2-COMPLETER-USAGE-LEDGER).

Why this exists: a cheap-API completion used to leave no trace at all — no row,
no log line, not even a counter. The gateway does not parse ``usage`` either, and
its file handler is not mounted by default, so "how much did this turn cost"
could not be answered from anywhere on the machine.

Three rules this file is built around:

* **One book.** Only ``plobi/quota/**`` writes here. Product surfaces read it and
  must never keep a second copy (治理线判据 ①).
* **A spend you cannot price is still a spend.** ``cost`` stays ``None`` with a
  ``cost_status`` explaining why; reporting ``0`` would claim a free call (R-073).
* **Never raise from a ledger.** Accounting must not be able to break a spend.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: Where the ledger lives. Resolved per call so profile-scoped homes stay scoped.
LEDGER_FILENAME = "quota-spend.jsonl"

#: ``usage`` blocks without money in them are the normal case, not a failure.
COST_STATUS_PRICED = "priced"
COST_STATUS_USAGE_NO_PRICE = "usage_reported_without_price"
COST_STATUS_NO_USAGE = "no_usage_reported"
COST_STATUS_NOT_ATTEMPTED = "not_attempted"

_write_lock = threading.Lock()


def ledger_path() -> Path:
    try:
        from plobi_constants import get_plobi_home

        root = get_plobi_home() / "plobi"
    except Exception:  # noqa: BLE001 - a ledger must never be the reason a call fails
        root = Path(".")
    return root / LEDGER_FILENAME


def record(
    *,
    source: str,
    kind: str = "",
    attempted: bool = True,
    ok: bool = False,
    error_class: str = "",
    usage: Optional[dict[str, Any]] = None,
    cost: Optional[float] = None,
    cost_status: str = COST_STATUS_NO_USAGE,
    route: str = "",
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Append one row. Returns the row it wrote (never raises)."""
    row: dict[str, Any] = {
        "ts": round(time.time(), 3),
        "source": source,
        "kind": kind,
        "attempted": bool(attempted),
        "ok": bool(ok),
        "error_class": error_class,
        "usage": usage or {},
        "cost": cost,
        "cost_status": cost_status,
    }
    if route:
        row["route"] = route
    if extra:
        row.update(extra)

    try:
        path = ledger_path()
        with _write_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001 - accounting never breaks a spend
        logger.warning("plobi quota ledger: cannot record (%s); spend continues", exc)
        row["record_error"] = type(exc).__name__
    return row


def read_recent(limit: int = 50) -> list[dict[str, Any]]:
    """The last ``limit`` rows, newest last. Empty when there is no ledger yet."""
    path = ledger_path()
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                rows.append(parsed)
    except OSError as exc:
        logger.warning("plobi quota ledger: cannot read %s (%s)", path, exc)
        return []
    return rows[-max(0, int(limit)):]


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Answer 「打没打 / 哪个源 / 几次 / 多少钱」 from rows — money may stay null."""
    per_source: dict[str, dict[str, Any]] = {}
    total_cost = 0.0
    priced = 0
    for row in rows:
        name = str(row.get("source") or "?")
        bucket = per_source.setdefault(name, {"attempts": 0, "ok": 0, "failed": 0, "tokens": 0})
        if row.get("attempted"):
            bucket["attempts"] += 1
        if row.get("ok"):
            bucket["ok"] += 1
        else:
            bucket["failed"] += 1
        usage = row.get("usage") or {}
        if isinstance(usage, dict):
            bucket["tokens"] += int(usage.get("total_tokens") or 0)
        cost = row.get("cost")
        if isinstance(cost, (int, float)):
            total_cost += float(cost)
            priced += 1

    return {
        "rows": len(rows),
        "sources": sorted(per_source),
        "per_source": per_source,
        "cost": total_cost if priced else None,
        "priced_rows": priced,
        "unpriced_rows": len(rows) - priced,
        "cost_status": COST_STATUS_PRICED if priced and priced == len(rows) else (
            "partially_priced" if priced else COST_STATUS_USAGE_NO_PRICE
        ),
    }
