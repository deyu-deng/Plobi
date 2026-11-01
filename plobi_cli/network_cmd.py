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
import sys


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

    if action == "import-subscription":
        return _import_subscription(args)

    if action == "proxy":
        return _proxy(args)

    if action == "switch":
        return _switch(args)

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


def _switch(args: argparse.Namespace) -> int:
    """The bottom-bar switch, from the command line.

    No flags = read it.  Flags = write the table **and** hand it to supervision in the same
    breath: 配了不生效 is the failure this whole surface was built to make impossible, so a
    switch that only edited a file would be a lie with extra steps (裁定 94 — the same
    reading the pane gets, from :mod:`agent.egress.status`).

    The write + supervision + **rollback-on-refusal** live in
    :func:`plobi_cli.egress_config.switch_and_supervise` rather than here: the pane does
    the same three steps in the same order, and a refused TUN that stopped the engine
    while the table still claimed it is exactly the 整机没网 shape this line exists to
    prevent.  One action, one implementation.
    """
    from agent.egress.rules import load_table
    from agent.egress.switch import read as read_switch

    transport = str(getattr(args, "transport", "") or "")
    mode = str(getattr(args, "mode", "") or "")
    exit_to = str(getattr(args, "exit_to", "") or "")
    allow_foreign = bool(getattr(args, "allow_foreign", False))

    if not (transport or mode or exit_to):
        print(" ".join(f"{k}={v}" for k, v in read_switch(load_table(force_reload=True)).items()))
        return 0

    from plobi_cli.egress_config import switch_and_supervise

    rc, lines = switch_and_supervise(
        transport=transport, mode=mode, exit_to=exit_to, allow_foreign=allow_foreign
    )
    for line in lines:
        print(line)
    return rc


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


def _stdin_or_value(text: str) -> str:
    """``--text -`` means "read it from stdin", so a pasted config can be piped in."""
    if text != "-":
        return text
    import sys

    return sys.stdin.read()


def _import_subscription(args: argparse.Namespace) -> int:
    """Pull a subscription and write it into the table.

    One reader for all three sources, so the command line and the pane cannot
    disagree about what a subscription turns into.
    """
    from agent.egress import local_clients, subscription
    from plobi_cli.egress_config import import_subscription

    given = [
        name
        for name, value in (
            ("--url", getattr(args, "url", "")),
            ("--file", getattr(args, "file", "")),
            ("--from-client", getattr(args, "from_client", "")),
            ("--text", getattr(args, "text", "")),
        )
        if value
    ]
    refresh = bool(getattr(args, "refresh", False))
    if len(given) > 1:
        print(f"给一个来源就行，现在给了 {'、'.join(given)}")
        return 2
    if refresh and given:
        print("--refresh 用的是存过的那个链接，别再给来源（要换来源就先不带 --refresh 导一次）")
        return 2

    dry = bool(getattr(args, "dry_run", False))
    try:
        answer = import_subscription(
            url=getattr(args, "url", "") or "",
            path=getattr(args, "file", "") or "",
            client=getattr(args, "from_client", "") or "",
            text=_stdin_or_value(getattr(args, "text", "") or ""),
            apply=not dry,
            catch_all=getattr(args, "catch_all", "") or "",
            pool_name=getattr(args, "pool_name", "") or "POOL",
            keep_groups=bool(getattr(args, "keep_groups", False)),
            refresh=refresh,
            save_url=bool(getattr(args, "save", False)),
        )
    except (subscription.SubscriptionError, local_clients.LocalClientError) as exc:
        print(str(exc))
        return 1
    except Exception as exc:  # noqa: BLE001 — a bad source is an answer, not a traceback
        print(f"{type(exc).__name__}: {exc}")
        return 1

    print(f"来源：{answer['source']}")
    print(f"格式：{answer['format']}；成员 {len(answer['nodes'])} 个、组 {len(answer['groups'])} 个")
    print(f"凭据：{len(answer['secret_env_names'])} 个写进根 .env，表里只留 *_env 名字")
    if answer.get("stored_as"):
        print(f"订阅链接：已存（变量名 {answer['stored_as']}，值不回显）——以后 `--refresh` 就能刷")
    for note in answer["notes"]:
        print(f"— {note}")

    if dry:
        print("--- 没有写任何东西：表、.env 都原样 ---")
        return 0 if answer["ok"] else 1
    if not answer.get("wrote"):
        return 1
    print("表写好了。下一句：`plobi network reload` 让引擎照新的表走。")
    return 0


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
