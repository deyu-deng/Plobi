"""Write side of the egress table for the GUI (WP-EGRESS-ROUTING).

Shaped like the MCP editor: a **replace**-semantics save, because
``PUT /api/config`` deep-merges maps and can therefore never delete a rule, an
exit or a mirror — an edit looks saved while the old entry survives on disk.

Validation is deliberately *not* implemented here.  A candidate block is run
through :func:`agent.egress.rules.parse_table` — the one interpreter of this
table — so the GUI cannot persist anything the runtime would reject.  That
matters more than it sounds: ``load_table`` raises on a malformed table, and
``load_table`` sits on the subprocess-spawn path, so one bad save through a
generic config endpoint takes the ``terminal`` tool down on every call.  The
schema-driven settings form renders ``network.egress.rules`` (a list of
``{match, via}`` mappings) as a comma-separated input, which turns the table
into ``["[object Object]"]`` on the first touch — reproduced on this box
2026-10-12, and the reason this module exists.
"""

from __future__ import annotations

from typing import Any

from agent.egress import rules as egress


def validate_egress(network_block: Any) -> tuple[bool, list[str]]:
    """Is this ``network:`` block something the runtime will accept?"""
    if network_block is None:
        return True, []
    if not isinstance(network_block, dict):
        return False, [f"network must be a mapping, got {type(network_block).__name__}"]
    try:
        egress.parse_table(network_block.get("egress"))
    except egress.EgressConfigError as exc:
        return False, [str(exc)]
    except Exception as exc:  # a parser that throws something else is still a refusal
        return False, [f"{type(exc).__name__}: {exc}"]
    return True, []


def read_egress() -> dict[str, Any]:
    """The block an editor can round-trip, plus what the runtime makes of it."""
    from plobi_cli.config import load_config

    network = (load_config().get("network") or {})
    table = egress.load_table()
    return {
        "network": network,
        "declared": table.active,
        "unreadable": table.unreadable,
        "engine_only_rules": [r.match for r in table.engine_only_rules],
        "env_dropped_rules": [r.match for r in table.env_dropped_rules],
    }


def replace_egress(network_block: Any) -> tuple[bool, list[str]]:
    """Set the whole ``network`` section (empty/None removes it), then re-read it.

    Returns ``(ok, issues)``; nothing is written when issues is non-empty, so a
    half-valid paste cannot be partially applied.
    """
    ok, issues = validate_egress(network_block)
    if not ok:
        return False, issues

    from plobi_cli.config import load_config, save_config

    config = load_config()
    if network_block:
        config["network"] = network_block
    else:
        config.pop("network", None)
    save_config(config)

    # The table is cached per process; an editor save that left the running
    # backend reading its old cache would make the UI and the product disagree.
    egress.load_table(force_reload=True)
    return True, []
