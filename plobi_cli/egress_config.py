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


def export_pool_membership(path: str) -> tuple[dict[str, Any], dict[str, str], list[str]]:
    """Read a mihomo pool file and turn it into table membership.  **Read-only.**

    Returns ``(egress_snippet, env_values, notes)``: the snippet is what goes under
    ``network.egress`` in ``config.yaml`` with every credential replaced by a
    ``*_env`` reference, and ``env_values`` is what belongs in the root ``.env``.
    Nothing here writes either file — migrating a live table is the user's edit,
    because an automatic rewrite of the file he hand-maintains is exactly the kind
    of thing this codebase has been burned by.

    Nodes whose type we cannot compile are listed in ``notes`` and dropped rather
    than guessed at; a silently missing node reads as "the pool is fine" until the
    engine cannot dial.
    """
    import os
    import re

    import yaml

    from agent.egress.rules import _GROUP_TYPES, _NODE_TYPES

    try:
        doc = yaml.safe_load(open(path, encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        return {}, {}, [f"could not read '{path}': {type(exc).__name__}: {exc}"]

    def var_name(name: str, field: str) -> str:
        slug = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").upper()
        return f"PLOBI_EGRESS_{slug}_{field.upper()}"

    nodes: dict[str, Any] = {}
    env_values: dict[str, str] = {}
    notes: list[str] = []
    for entry in doc.get("proxies") or []:
        if not isinstance(entry, dict):
            notes.append("skipped a proxies[] entry that is not a mapping")
            continue
        name = str(entry.get("name", "")).strip()
        kind = str(entry.get("type", "")).strip().lower()
        if not name:
            notes.append("skipped a proxies[] entry with no name")
            continue
        if kind not in _NODE_TYPES:
            notes.append(
                f"'{name}' has type '{kind or '(none)'}' which this table cannot hold "
                f"(supported: {', '.join(sorted(_NODE_TYPES))})"
            )
            continue
        node: dict[str, Any] = {
            "type": kind,
            "server": entry.get("server"),
            "port": entry.get("port"),
        }
        if not node["server"] or not node["port"]:
            notes.append(f"'{name}' is missing server/port")
            continue
        for key, value in entry.items():
            key = str(key)
            if key in ("name", "type", "server", "port"):
                continue
            if key in ("password", "uuid", "username"):
                var = var_name(name, key)
                node[f"{key}_env"] = var
                env_values[var] = str(value)
                continue
            node[key] = value
        for required in _NODE_TYPES[kind]:
            if required not in ("password", "uuid", "username") and required not in node:
                notes.append(f"'{name}' lacks '{required}', required for type '{kind}'")
                node = {}
                break
        if node:
            nodes[name] = node

    groups: dict[str, Any] = {}
    for entry in doc.get("proxy-groups") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        kind = str(entry.get("type", "")).strip()
        members = entry.get("proxies") or entry.get("use") or []
        if not name or kind not in _GROUP_TYPES:
            notes.append(f"group '{name or '(unnamed)'}' has unsupported type '{kind}' — skipped")
            continue
        unknown = [m for m in members if m not in nodes]
        if unknown:
            notes.append(f"group '{name}' references unavailable members: {', '.join(map(str, unknown))}")
        usable = [m for m in members if m in nodes]
        if usable:
            groups[name] = {"type": kind, "include": usable}
            if entry.get("interval"):
                groups[name]["interval"] = int(entry["interval"])

    if not nodes:
        return {}, {}, notes or [f"no usable proxies[] entries in '{path}'"]
    return {"nodes": nodes, "groups": groups}, env_values, notes


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


def set_system_proxy(desired: bool) -> tuple[bool, list[str]]:
    """Flip ``network.egress.system_proxy.enabled`` and leave every other key alone.

    This lives here rather than in the CLI because this module is the one place
    allowed to name the ``egress`` key without interpreting it (the single-source
    guard); the read-modify-write goes through :func:`read_egress`, which hands back
    the verbatim block, so nodes / mirrors / ``engine.dns`` survive the write.
    """
    network = dict(read_egress().get("network") or {})
    egress = dict(network.get("egress") or {})
    egress["system_proxy"] = {"enabled": desired}
    network["egress"] = egress
    return replace_egress(network)


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
