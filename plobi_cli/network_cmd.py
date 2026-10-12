"""``plobi network status`` — the egress table's visible surface.

The routing decision has had a single interpreter for a while now; what it did
not have was a way for a human to ask *"so what is happening"*.  This prints the
one reading :func:`agent.egress.status.render` produces, which is the same
reading the desktop pane gets from ``GET /api/network/status`` — there is
deliberately no second assembly of these facts here (裁定 94: something that is
only visible in a log file, or only visible to one surface, is not visible).
"""

from __future__ import annotations

import argparse


def cmd_network(args: argparse.Namespace) -> int:
    from agent.egress.status import render

    for line in render():
        print(line)
    return 0
