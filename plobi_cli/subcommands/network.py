"""``plobi network`` subcommand parser.

Follows the injected-handler shape of the other extracted subcommands so the
god-file only supplies the callable.
"""

from __future__ import annotations

from typing import Callable

_ACTIONS = ("status", "start", "stop", "reload")
_HELP = {
    "status": "Print the declared routing, the engine's live state, and any downgrades",
    "start": "Bring the engine up if nobody else holds the port",
    "stop": "Stop the engine Plobi started — never one it did not start",
    "reload": "Restart our engine with the current table; refuse to touch a foreign one",
}


def build_network_parser(subparsers, *, cmd_network: Callable) -> None:
    """Attach the ``network`` subcommand to ``subparsers``."""
    network_parser = subparsers.add_parser(
        "network",
        help="Show or drive egress routing (the network.egress table)",
        description="Read and drive the routing table Plobi owns: which exit each destination "
        "uses, which rules only the engine can honor, and the local proxy Plobi supervises. "
        "The desktop Network pane reads the same state.",
    )
    network_sub = network_parser.add_subparsers(dest="network_action")
    for action in _ACTIONS:
        network_sub.add_parser(action, help=_HELP[action]).set_defaults(func=cmd_network)
    # `plobi network` alone should do the read-only thing, never the destructive one.
    export_parser = network_sub.add_parser(
        "export-pool",
        help="Print the table shape of a mihomo pool file (read-only; writes nothing)",
    )
    export_parser.add_argument("pool_path", help="Path to the pool's config.yaml")
    export_parser.add_argument(
        "--show-values",
        action="store_true",
        help="Print the credential values to migrate into .env (off by default)",
    )
    export_parser.set_defaults(func=cmd_network)
    # `plobi network` alone should do the read-only thing, never the destructive one.
    network_parser.set_defaults(func=cmd_network, network_action="status")
