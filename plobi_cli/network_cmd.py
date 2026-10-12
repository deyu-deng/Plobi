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
