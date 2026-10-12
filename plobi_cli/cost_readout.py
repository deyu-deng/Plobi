"""One-line "what did this run cost" readout for the shell-visible CLI exits.

WP-COST-READOUT-CLI.  The money already exists — ``session_estimated_cost_usd``
plus ``cost_status`` / ``cost_source``, folded at turn end (see
``agent/turn_finalizer.py``) — but a human watching ``plobi -z`` or
``plobi --tui`` only ever sees tokens.  The dashboard learned this lesson first
(``_honest_session_cost`` in ``plobi_cli/web_server.py``); the CLI is catching up
with the same ruler instead of a second one.

The single rule this module exists to keep: **an absence of pricing is not a
zero cost.**  ``sessions.estimated_cost_usd`` is ``NOT NULL DEFAULT 0``, so
reading the raw column makes "this chat was free" and "we have no price for this
model" print identically.  Every amount here goes through
``agent.usage_pricing.project_cost``, which keys off ``cost_status`` and not off
the number.  Nothing in this file decides a status by itself.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from agent.usage_pricing import (
    COST_STATUS_NO_USAGE,
    COST_STATUS_PARTIAL,
    PRICED_COST_STATUSES,
    format_token_count_compact,
    project_cost,
)

# Turn results spell the counter ``api_calls``; session rows spell it
# ``api_call_count``.  Both call sites feed this module, so accept both.
_CALL_KEYS = ("api_calls", "api_call_count")
_TOKEN_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
)

# Readout words.  ``project_cost`` owns the statuses; this table only says what
# each one looks like to a person at a terminal — "unknown" must never be
# smoothed into a number.
_STATUS_WORD = {
    "actual": "actual",
    "estimated": "estimated",
    "included": "included in plan",
    COST_STATUS_PARTIAL: "priced partially",
    "unknown": "no price on file",
    COST_STATUS_NO_USAGE: "no usage",
}


def _count(row: Mapping[str, Any], keys) -> float:
    """First numeric value among *keys*, as a float.  Anything else is 0."""
    for key in keys:
        value = row.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return float(value)
    return 0.0


def _one(row: Mapping[str, Any], key: str) -> float:
    """Numeric value of *key*, or 0.0 for anything that isn't a number."""
    return _count(row, (key,))


def _money(amount: float) -> str:
    if amount >= 1:
        return f"${amount:,.2f}"
    return f"${amount:.4f}"


def _usage_tail(row: Mapping[str, Any]) -> str:
    bits = [
        f"{format_token_count_compact(int(_one(row, 'input_tokens')))} in",
        f"{format_token_count_compact(int(_one(row, 'output_tokens')))} out",
    ]
    calls = int(_count(row, _CALL_KEYS))
    if calls:
        bits.append(f"{calls} calls")
    model = str(row.get("model") or "").strip()
    if model:
        bits.append(model)
    return " · ".join(bits)


def cost_line(row: Optional[Mapping[str, Any]]) -> Optional[str]:
    """Fold one run (or one session row) into a printable cost line.

    Returns ``None`` when there is nothing honest to say — no usage and no
    money.  A run that used tokens on an unpriced model returns an ``unknown``
    line rather than a ``$0.00`` one; that distinction is the whole point.
    """
    if not row:
        return None

    amount = _count(row, ("estimated_cost_usd",))
    status = row.get("cost_status")
    has_usage = any(int(_count(row, (key,))) for key in _TOKEN_KEYS) or bool(
        int(_count(row, _CALL_KEYS))
    )
    if not has_usage and not amount:
        return None

    priced = status in PRICED_COST_STATUSES
    # `amount` counts as "there was spend here" too: a row with money but no
    # token columns must fold to `partial`, not to `(0.0, "none")` — that would
    # throw away the only number we have.
    projected, projected_status = project_cost(
        amount,
        1 if priced else 0,
        1 if (not priced and (has_usage or amount)) else 0,
        1 if status == "included" else 0,
    )

    # Same ruling as `_honest_session_cost` on the dashboard side: a priced row
    # keeps its own status (``actual`` must not be softened into ``estimated``),
    # and only an unpriced row takes the status the projection had to invent.
    word = _STATUS_WORD.get(status if priced else projected_status,
                            str(status if priced else projected_status))
    if projected is None:
        money = f"unknown ({word})"
    else:
        source = str(row.get("cost_source") or "").strip()
        money = f"{_money(projected)} ({word} · {source})" if source else f"{_money(projected)} ({word})"
    return f"{money} — {_usage_tail(row)}"


def epilogue_cost_lines(row: Optional[Mapping[str, Any]]) -> list[str]:
    """The ``Cost:`` line for the TUI exit summary, aligned with the ``Tokens:``
    column already printed there."""
    line = cost_line(row)
    if not line:
        return []
    return [f"{'Cost:':<16}{line}"]


def emit_cost_line(
    row: Optional[Mapping[str, Any]], stream, prefix: str = "plobi -z:"
) -> bool:
    """Write one cost line to *stream*; never raise.  Returns whether it wrote.

    A readout failure must not change the run's exit code or swallow its
    output, so everything here is best-effort.
    """
    try:
        line = cost_line(row)
        if not line:
            return False
        stream.write(f"{prefix} {line}\n")
        stream.flush()
    except Exception:  # noqa: BLE001 - display only, never the run's outcome
        return False
    return True
