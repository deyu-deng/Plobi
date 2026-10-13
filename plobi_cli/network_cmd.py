"""``plobi network`` — the egress table's visible surface and its switch.

``status`` prints the one reading :func:`agent.egress.status.render` produces,
which is the same reading the desktop pane gets from ``GET /api/network/status``:
there is deliberately no second assembly of these facts here (裁定 94 — something
visible only in a log, or only to one surface, is not visible).

``start`` / ``stop`` / ``reload`` drive :mod:`agent.egress.engine`, and inherit
its boundary: **only a process Plobi recorded is ever stopped**, and a port
someone else already holds is reported, never taken over.
"""

from __future__ import annotations

import argparse


def cmd_network(args: argparse.Namespace) -> int:
    from agent.egress import engine
    from agent.egress.rules import load_table
    from agent.egress.status import render

    action = getattr(args, "network_action", None) or "status"

    if action == "status":
        for line in render():
            print(line)
        return 0

    if action == "export-pool":
        return _export_pool(args)

    table = load_table()
    if action == "start":
        report = engine.ensure(table)
    elif action == "stop":
        report = engine.stop_ours(table)
    elif action == "reload":
        report = engine.reload_engine(table)
    else:  # argparse restricts the choices; this is here so a wrong dest fails loud
        print(f"unknown network action: {action}")
        return 2

    for line in report.lines():
        print(line)
    return 0 if report.state in (engine.OFF, engine.OURS, engine.STOPPED) else 1


def _export_pool(args: argparse.Namespace) -> int:
    """Print the table shape of a mihomo pool file.  Read-only; writes nothing.

    Credential values are withheld unless ``--show-values`` is given: the point of
    this output is to be pasted into ``config.yaml``, and a cleartext pool printed
    into a terminal log or a chat window is how a migration leaks.
    """
    import yaml

    from plobi_cli.egress_config import export_pool_membership

    snippet, env_values, notes = export_pool_membership(getattr(args, "pool_path", "") or "")
    if not snippet:
        for note in notes:
            print(f"cannot import: {note}")
        return 1

    print("# paste under network.egress in config.yaml")
    print(yaml.safe_dump(snippet, sort_keys=False, allow_unicode=True).rstrip())
    print()
    print(f"# {len(env_values)} credentials belong in the root .env, not in config.yaml:")
    for name, value in sorted(env_values.items()):
        print(f"  {name}={value if getattr(args, 'show_values', False) else '***'}")
    for note in notes:
        print(f"# note: {note}")
    return 0
