"""One readable answer to "what does this machine's egress actually do right now".

Everything that displays routing should print :func:`render` rather than
re-assembling the pieces: the table, the engine's live state, and the downgrades
we already decided.  裁定 94 is exactly about this — "it is only visible in the
log" does not count as visible, and a second, narrower reading of the same truth
is worse than none, because the two readings will disagree in the field.

This module starts nothing and probes nothing new; it calls
:func:`agent.egress.engine.status` (which is itself read-only) so the answer a
user sees is produced by the same functions supervision uses.
"""

from __future__ import annotations

from agent.egress import engine
from agent.egress.rules import Table, load_table


def snapshot(table: Table | None = None, *, home: str | None = None) -> dict:
    """The same answer as :func:`render`, structured for a UI to bind to.

    Kept next to :func:`render` and built from the same objects on purpose: two
    renderings of one reading is fine, two readings is 裁定 94's bug.
    """
    table = table if table is not None else load_table()
    report = engine.status(table, home=home)
    return {
        "declared": table.active,
        "unreadable": table.unreadable,
        "rules": [
            {
                "match": rule.match,
                "via": rule.via,
                "engine_only": rule in table.engine_only_rules,
                "env_dropped": rule in table.env_dropped_rules,
            }
            for rule in table.rules
        ],
        "mirrors": dict(sorted(table.mirrors.items())),
        "engine": {
            "enabled": table.engine_enabled,
            "state": report.state,
            "detail": report.detail,
            "mixed_port": report.mixed_port,
            "config_source": report.config_source,
            "pid": report.pid,
            "probe": report.probe,
            "notes": list(report.notes),
        },
        "downgrades": engine.recent_downgrades(),
    }


def render(
    table: Table | None = None,
    *,
    home: str | None = None,
    with_probe: bool = True,
) -> list[str]:
    """Human-readable lines: declared routing, what the engine is doing, what we gave up on."""
    table = table if table is not None else load_table()
    lines: list[str] = []

    if table.unreadable:
        lines.append(f"network.egress: COULD NOT READ config ({table.unreadable})")
        lines.append("  treated as no table; routing falls back to inherited env")
        return lines
    if not table.active:
        lines.append("network.egress: not declared — traffic uses whatever the environment says")
        return lines

    lines.append(f"table: {len(table.rules)} rule(s), catch-all {table.rules[-1].via}")
    for rule in table.rules:
        tag = "  (engine-only, not visible to spawned children)" if rule in table.engine_only_rules else ""
        lines.append(f"  {rule.match} -> {rule.via}{tag}")
    if table.env_dropped_rules:
        lines.append(
            f"  ceiling: {len(table.env_dropped_rules)} rule(s) cannot be expressed in a "
            f"spawned child's env vocabulary — those children go direct for them"
        )
    if table.mirrors:
        lines.append("mirrors: " + ", ".join(f"{k}={v}" for k, v in sorted(table.mirrors.items())))
    else:
        lines.append("mirrors: none")

    report = engine.status(table, home=home) if with_probe else None
    if report is not None:
        lines.extend(report.lines())
        for item in engine.recent_downgrades():
            lines.append(f"downgrade: {item['exit']} — {item['reason']}")

    return lines
