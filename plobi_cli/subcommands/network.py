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
    # 订阅进表：三个来源，一个读者。`--from-client` 读的是本机客户端自己缓存的那份
    # （有些订阅商只给软件、不给"复制订阅链接"的按钮，但配置就在这台机器上）。
    import_parser = network_sub.add_parser(
        "import-subscription",
        help="Pull a subscription and unfold it into the table's nodes and groups",
        description="Pull a subscription (URL / file / a local client's own cache) and write it "
        "into network.egress.nodes and .groups.  Credentials go to the root .env; the table keeps "
        "only the *_env names.  A subscription that carries no groups gets a select group per place "
        "and one url-test POOL, so a rule has something to point at.",
    )
    import_parser.add_argument("--url", default="", help="Subscription URL (fetched once, no silent retry)")
    import_parser.add_argument("--file", dest="file", default="", help="A config file or a link list on disk")
    import_parser.add_argument(
        "--text",
        default="",
        help="Paste a whole config (a Clash/mihomo yaml, a base64 blob, or link lines); '-' reads stdin",
    )
    import_parser.add_argument(
        "--from-client",
        dest="from_client",
        default="",
        help="Read the subscription out of a local client's own cache (e.g. ikuuu)",
    )
    import_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be written; write nothing (not the table, not the .env)",
    )
    import_parser.add_argument(
        "--catch-all",
        dest="catch_all",
        default="",
        help="Point the table's last rule at this group (e.g. POOL)",
    )
    import_parser.add_argument(
        "--pool-name",
        dest="pool_name",
        default="POOL",
        help="Name of the url-test group the import builds (default: POOL)",
    )
    import_parser.add_argument(
        "--save",
        action="store_true",
        help="Remember the URL in the root .env (PLOBI_EGRESS_SUBSCRIPTION_URL) so --refresh needs no app",
    )
    import_parser.add_argument(
        "--refresh",
        action="store_true",
        help="Re-pull the URL stored by --save; keeps the table's own groups, updates the members",
    )
    import_parser.add_argument(
        "--keep-groups",
        dest="keep_groups",
        action="store_true",
        help="Do not import the subscription's own groups (keep the table's; members still update)",
    )
    import_parser.set_defaults(func=cmd_network)
    # 整机那一格：接管/交还系统的代理。`on --dry-run` 先看要改什么，不写注册表。
    proxy_parser = network_sub.add_parser(
        "proxy",
        help="Take over (or hand back) the machine's system proxy: Plobi as the exit",
        description="Windows only for now.  `on` writes a PAC generated from network.egress into "
        "PLOBI_HOME/egress and points the current user's system proxy at it, backing up whatever "
        "was there first; `off` restores that backup byte for byte.  Apps that ignore a system "
        "proxy (raw TCP, most games) are not covered by this — that needs TUN.",
    )
    proxy_parser.add_argument("proxy_action", choices=("on", "off", "status"), nargs="?", default="status")
    proxy_parser.add_argument("--dry-run", action="store_true", help="Show what `on` would write; change nothing")
    proxy_parser.set_defaults(func=cmd_network)
    # 底栏那个 overlay 的命令行形状（用户 11-01 要「一屏开关、一屏选节点」）：同一张表的同三个
    # 动作，桌面点得到、没有桌面也该拨得动（口径 32：装机后的机器不该只能从某个 GUI 配置）。
    switch_parser = network_sub.add_parser(
        "switch",
        help="Drive the machine-wide switch: which carrier, rules or global, which default exit",
        description="Read (with no flags) or change the three things the bottom-bar overlay shows: "
        "the machine-wide carrier (TUN / system proxy / none), whether the CN classification still "
        "goes direct (rules) or everything follows the catch-all (global), and what the catch-all "
        "points at. Writes the table, then hands it to supervision — same reading as the pane.",
    )
    switch_parser.add_argument(
        "--transport", dest="transport", choices=("tun", "system", "off"), default="",
        help="Which carrier the machine gets; the other one is turned off (they are modes, not stacks)",
    )
    switch_parser.add_argument(
        "--mode", dest="mode", choices=("rule", "global"), default="",
        help="rule = the geosite:cn classification goes direct; global = everything uses the default exit",
    )
    switch_parser.add_argument(
        "--exit", dest="exit_to", default="", help="Point the catch-all at this group or member"
    )
    switch_parser.add_argument(
        "--allow-foreign",
        dest="allow_foreign",
        action="store_true",
        help="With --transport tun: start our adapter next to somebody else's tunnel instead of refusing "
        "(纸 §25 — a stated decision, never a default)",
    )
    switch_parser.set_defaults(func=cmd_network)
    # `plobi network` alone should do the read-only thing, never the destructive one.
    network_parser.set_defaults(func=cmd_network, network_action="status")
