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

    if action == "proxy":
        return _proxy(args)

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


def _proxy(args: argparse.Namespace) -> int:
    """Drive the machine-wide half: the system proxy is Plobi's or it is not."""
    from agent.egress import system_proxy

    which = getattr(args, "proxy_action", "status")
    if which == "status":
        info = system_proxy.status()
        for line in system_proxy.status_lines(info):
            print(line)
        return 0
    if not system_proxy.supported():
        print(f"这一格目前只实现 Windows（这台是 {sys.platform}）")
        return 1

    if which == "on":
        dry = bool(getattr(args, "dry_run", False))
        plan = system_proxy.enable(apply=not dry)
        for line in plan.lines():
            print(line)
        if dry and plan.ok:
            print(f"--- PAC 内容（{plan.pac_path}）---")
            print(plan.pac_text.rstrip())
            print("--- 没有写任何东西：去掉 --dry-run 才真接管 ---")
        elif plan.ok:
            _persist_switch(True)
        return 0 if plan.ok else 1

    ok, detail = system_proxy.disable()
    print(detail)
    if ok:
        _persist_switch(False)
    return 0 if ok else 1


def _persist_switch(desired: bool) -> None:
    """Write the switch into the table so a reboot keeps it (or loses it) deliberately.

    Read-modify-write of the verbatim ``network`` block: ``read_egress()`` hands back
    what is on disk, so unrelated keys (nodes, mirrors, engine.dns) survive this.
    """
    from plobi_cli.egress_config import set_system_proxy

    ok, issues = set_system_proxy(desired)
    if ok:
        print(f"network.egress.system_proxy.enabled = {str(desired).lower()}（下次开机照这个走）")
    else:
        print(f"⚠️ 系统设置已经改了，但开关没落盘：{'；'.join(issues)}")


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
