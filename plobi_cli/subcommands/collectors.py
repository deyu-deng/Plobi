"""``plobi collectors`` —— 装 / 卸 / 列第三方信源包（「接一个自己的」那一格）。

为什么走 CLI 而不直接给 HTTP 一个 ``POST /install``：闸门在 :mod:`plobi.collectors.external`
那一层，HTTP 只 spawn 这个子命令（argv 列表），跟 ``plobi skills install`` 同形。
理由在那个模块的头两段——**用户输入离执行最近的一跳不该只隔一次函数调用**。

退出码就是那条链的真话：0 成了，1 没成，而 stdout/stderr 上是**那一层自己写的那句话**
（不是"操作失败"）——设置面板的日志尾巴直接取它。
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Callable, Optional


def build_collectors_parser(subparsers, *, cmd_collectors: Callable) -> None:
    """Attach the ``collectors`` subcommand to ``subparsers``."""
    parser = subparsers.add_parser(
        "collectors",
        help="Install, remove, or list third-party source packages",
        description=(
            "Install a third-party collector package that declares the "
            "`plobi.collectors` entry-point group. Only a bare package NAME is accepted "
            "here — no versions, extras, URLs or local paths; use a terminal for those."
        ),
    )
    actions = parser.add_subparsers(dest="collectors_action")

    # `--yes` 挂在**每一个动作上**，不是挂在 collectors 那一层：设置面板 spawn 的是
    # `collectors install <包名> --yes`，flag 放在父解析器上就得写成
    # `collectors --yes install <包名>`，而那样写出来的 argv 一旦被谁改了顺序就静默失效。
    # 它不接任何东西（这里没有交互确认），只是明确"这一条是非交互的"。
    install = actions.add_parser("install", help="Install a package from the index")
    install.add_argument("package", help="Package name, e.g. plobi-collector-telegram")
    install.add_argument("--json", action="store_true", help="Print the report as JSON instead of prose")
    install.add_argument(
        "--no-upgrade", action="store_true",
        help="Do not pass --upgrade to pip (default is to upgrade an existing install)",
    )
    install.add_argument("--yes", action="store_true", help=argparse.SUPPRESS)

    remove = actions.add_parser("remove", aliases=["rm", "uninstall"], help="Uninstall a package")
    remove.add_argument("package", help="Package name")
    remove.add_argument("--json", action="store_true", help="Print the report as JSON")
    remove.add_argument("--yes", action="store_true", help=argparse.SUPPRESS)

    listing = actions.add_parser("list", aliases=["ls"], help="List third-party packages")
    listing.add_argument("--json", action="store_true", help="Print JSON instead of prose")
    listing.add_argument("--yes", action="store_true", help=argparse.SUPPRESS)

    parser.set_defaults(func=cmd_collectors)


def cmd_collectors(args: Any) -> int:
    """跑一个 collectors 动作。"""
    from plobi.collectors import external
    from plobi.collectors.external import InvalidPackageName

    action = getattr(args, "collectors_action", None)
    as_json = bool(getattr(args, "json", False))

    try:
        if action == "install":
            report: dict[str, Any] = external.install(
                args.package, upgrade=not getattr(args, "no_upgrade", False),
            )
        elif action in {"remove", "rm", "uninstall"}:
            report = external.uninstall(args.package)
        elif action in {"list", "ls"}:
            report = {"ok": True, "packages": external.installed_external_packages()}
        else:
            print("说一句要做什么：install / remove / list。", file=sys.stderr)
            return 1
    except InvalidPackageName as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if action in {"list", "ls"}:
        if as_json:
            print(json.dumps(report, ensure_ascii=False))
        else:
            _print_packages(report.get("packages") or [])
        return 0

    if as_json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        print(report.get("summary") or json.dumps(report, ensure_ascii=False))
        for line in _as_lines(report.get("output")):
            print(f"  {line}")

    return 0 if report.get("ok") else 1


def _as_lines(output: Optional[Any]) -> list[str]:
    if output is None:
        return []
    if isinstance(output, str):
        return [line for line in output.splitlines() if line.strip()]

    return [str(line) for line in output]


def _print_packages(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("外面一个都没装。这不算错。")
        return

    for row in rows:
        state = row.get("source") or row.get("load_error") or "（没报出信源 id）"
        print(f"  {row['entry_point']}  →  {state}")