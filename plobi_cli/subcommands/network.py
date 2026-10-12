"""``plobi network`` subcommand parser.

Follows the injected-handler shape of the other extracted subcommands so the
god-file only supplies the callable.
"""

from __future__ import annotations

from typing import Callable


def build_network_parser(subparsers, *, cmd_network: Callable) -> None:
    """Attach the ``network`` subcommand to ``subparsers``."""
    network_parser = subparsers.add_parser(
        "network",
        help="Show how egress is routed right now",
        description="Print the declared network.egress table, which rules the runtime cannot "
        "honor by itself, and the state of the engine Plobi supervises — the same reading the "
        "desktop Network pane shows.",
    )
    network_sub = network_parser.add_subparsers(dest="network_action")
    status_parser = network_sub.add_parser("status", help="Routing, engine state, and downgrades")
    status_parser.set_defaults(func=cmd_network)
    # `plobi network` alone should do the one thing this command knows.
    network_parser.set_defaults(func=cmd_network, network_action="status")
