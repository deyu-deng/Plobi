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

from pathlib import Path
from typing import Any

from agent.egress import rules as egress

#: Places a node can be named after, and the group that name lands it in.
#:
#: The Chinese word is the authority, not the flag: a real ikuuu list (measured on this
#: box 10-25) names its US nodes "🇺🇲 美国W01", and that flag is the **US Virgin Islands** —
#: matching flags alone would put the US nodes in a group nobody asked for.  Sorted by
#: length so "印度尼西亚" is read before "印度".
_REGION_WORDS: tuple[tuple[str, str], ...] = tuple(sorted(
    (
        ("新加坡", "新加坡"), ("澳大利亚", "澳大利亚"), ("乌克兰", "乌克兰"),
        ("马来西亚", "马来西亚"), ("菲律宾", "菲律宾"), ("阿根廷", "阿根廷"),
        ("俄罗斯", "俄罗斯"), ("加拿大", "加拿大"), ("土耳其", "土耳其"),
        ("印度尼西亚", "印尼"), ("西班牙", "西班牙"), ("南非", "南非"),
        ("印度", "印度"), ("韩国", "韩国"), ("香港", "香港"),
        ("澳门", "澳门"), ("台湾", "台湾"), ("日本", "日本"),
        ("美国", "美国"), ("英国", "英国"), ("德国", "德国"),
        ("法国", "法国"), ("泰国", "泰国"), ("越南", "越南"),
        ("巴西", "巴西"), ("荷兰", "荷兰"), ("印尼", "印尼"),
        ("意大利", "意大利"),
    ),
    key=lambda pair: -len(pair[0]),
))

#: Flags that are unambiguous, used only when the name carries no place word.
_FLAG_REGIONS: dict[str, str] = {
    "🇭🇰": "香港", "🇯🇵": "日本", "🇰🇷": "韩国", "🇸🇬": "新加坡",
    "🇹🇼": "台湾", "🇺🇸": "美国", "🇬🇧": "英国", "🇩🇪": "德国",
    "🇫🇷": "法国", "🇨🇦": "加拿大", "🇦🇺": "澳大利亚", "🇷🇺": "俄罗斯",
    "🇮🇳": "印度", "🇹🇷": "土耳其", "🇳🇱": "荷兰", "🇦🇷": "阿根廷",
    "🇲🇴": "澳门",
}


def candidate_node_count(network_block: Any) -> int:
    """How many members this candidate table would leave — **through the parser**.

    Lives here (the one module allowed to name the `egress` key without interpreting
    it) so a caller like the web surface does not grow its own read of the block —
    that is exactly what `tests/scripts/test_egress_resolver_single_source.py`
    ratchets against (裁定 94: one reader).  `-1` = the parser refused it; the save
    will be rejected on its own merits.
    """
    try:
        return len(egress.parse_table((network_block or {}).get("egress")).nodes)
    except Exception:  # noqa: BLE001 - a refusal is an answer here, not a crash
        return -1


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


def apply_switch(
    *,
    transport: str = "",
    mode: str = "",
    exit_to: str = "",
    allow_foreign: bool = False,
) -> tuple[bool, list[str]]:
    """Drive the bottom-bar switch from the command line — the same three writes the pane makes.

    Why it lives here: this is the one module allowed to name the ``egress`` key, and every
    edit is a read-modify-write through :func:`read_egress`, so switching the machine's
    carrier cannot also wipe the members, the mirrors or ``engine.dns`` the block carries.
    ``transport`` needs the TUN shape from :func:`agent.egress.tun.default_block` — the
    name of an adapter is not something a caller gets to invent (纸 §25).
    """
    from agent.egress import switch, tun

    network = dict(read_egress().get("network") or {})
    block = dict(network.get("egress") or {})
    current_tun = dict((((block.get("engine") or {}) if isinstance(block.get("engine"), dict) else {}).get("tun") or {}))
    try:
        if transport:
            block = switch.set_transport(
                block,
                transport,
                tun_block=tun.default_block(allow_foreign=True if allow_foreign else None, keep=current_tun),
            )
        if mode:
            block = switch.set_mode(block, mode)
        if exit_to:
            block = switch.set_exit(block, exit_to)
    except ValueError as exc:
        return False, [str(exc)]
    network["egress"] = block
    return replace_egress(network)


#: Engine states that mean "the machine still has the exit it had a moment ago".
_SETTLED = ("running-ours", "stopped", "off")


def switch_and_supervise(
    *,
    transport: str = "",
    mode: str = "",
    exit_to: str = "",
    allow_foreign: bool = False,
) -> tuple[int, list[str]]:
    """One carrier switch = one machine action: write, supervise, and **take it back if refused**.

    Why the rollback lives at the write instead of in the caller: measured 11-01, the
    command-line half of the bottom-bar switch had none, and the shape of the hole is
    worse than "a setting that did not apply".  :func:`agent.egress.switch.set_transport`
    turns ``system_proxy.enabled`` off in the same breath as it asks for TUN, and
    :func:`agent.egress.engine.reload_engine` **stops the running engine before** the TUN
    guard gets its say — so a refused ``plobi network switch --transport tun`` left the
    table claiming TUN, the registry proxy handed back to whatever was there before, and
    nothing listening: 整机没网 while every reading said 已接管 (纸 §37 的形状).

    The pane has rolled this back since §42; two halves of one action may not disagree
    (裁定 94), so the rule moves to the one place both go through.  A refusal is
    ``state == failed`` — the same predicate the pane uses; a foreign engine on our port
    is a different answer (nothing of ours moved) and is reported, not reverted.

    Returns ``(rc, lines)``: the lines are what the CLI prints, ``rc`` is its exit code.
    """
    from agent.egress import engine as _engine
    from agent.egress import switch as _switch
    from agent.egress import system_proxy as _system_proxy

    def reading(table: Any) -> str:
        return " ".join(f"{key}={value}" for key, value in _switch.read(table).items())

    before = dict(read_egress().get("network") or {})
    ok, issues = apply_switch(transport=transport, mode=mode, exit_to=exit_to, allow_foreign=allow_foreign)
    if not ok:
        return 1, [f"表没写进去：{line}" for line in issues]

    table = egress.load_table(force_reload=True)
    lines = [reading(table)]
    report = _engine.reload_engine(table)
    lines.extend(report.lines())
    plan = _system_proxy.ensure(table)
    if plan is None:
        lines.append("system proxy: 表里没说要接管，注册表不动")
    else:
        lines.extend(plan.lines())

    if report.state != _engine.FAILED:
        return (0 if (report.state in _SETTLED and (plan is None or plan.ok)) else 1), lines

    #: 引擎没接住这一拨 —— 表和机器一起退回原样，顺序与开机那处一致（先有引擎，再交出口）。
    lines.append("引擎没接住这一拨，把表和机器退回原样（屏幕与注册表不许停在一个没人执行的声明上）")
    back_ok, back_issues = replace_egress(before)
    if not back_ok:
        lines.append("⚠️ 退回时表写不回去：" + "；".join(back_issues))
        return 1, lines
    back_table = egress.load_table(force_reload=True)
    #: `ensure` 而不是 `reload_engine`：退回之后那份表与正在服务的配置**逐字节相同**
    #: （`config_sha` 那一格），所以这里该是"确认它在跑"，不是"停一下再起来"。
    #: 一次被拒的拨开关不该让这台机器多断一次网。
    lines.extend(_engine.ensure(back_table).lines())
    back_plan = _system_proxy.ensure(back_table)
    if back_plan is not None:
        lines.extend(back_plan.lines())
    lines.append("已经是原样：" + reading(back_table))
    return 1, lines


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


# ---------------------------------------------------------------------------
# Subscription import: the one reader, and the one writer
# ---------------------------------------------------------------------------


def region_of(name: str) -> str:
    """Which place a node is named after — the group it belongs in.  ``""`` when unknown."""
    text = str(name or "")
    for word, region in _REGION_WORDS:
        if word in text:
            return region
    for flag, region in _FLAG_REGIONS.items():
        if flag in text:
            return region
    return ""


def build_pool_groups(
    node_names: list[str],
    *,
    pool_name: str = "POOL",
    interval: int = 300,
) -> dict[str, dict[str, Any]]:
    """A select group per place, plus one url-test pool over everything imported.

    A subscription fetched by a desktop client often carries **no groups at all** —
    the ikuuu one measured on this box is 46 members and 0 groups, because its
    plugin builds the groups at runtime.  A table with members but no group has
    nothing a rule can point at except a single node, and a rule per node is not a
    working VPN.  So the import builds the two shapes a rule actually needs: a
    place per region to pin a node in, and one pool to hand the catch-all to.
    """
    by_region: dict[str, list[str]] = {}
    for name in node_names:
        region = region_of(name)
        if region:
            by_region.setdefault(region, []).append(name)
    groups: dict[str, dict[str, Any]] = {
        region: {"type": "select", "include": list(members)}
        for region, members in sorted(by_region.items())
    }
    if node_names:
        groups[pool_name] = {"type": "url-test", "include": list(node_names), "interval": interval}
    return groups


def _own_listener_names(block: dict[str, Any]) -> set[str]:
    """The proxy names that point at the engine's own listener — the front door.

    A rule that hands traffic to the front door looks right to a reader (the front
    door *is* where clients go) and is fatal to the engine (it would forward into
    its own accept queue).  So the import says so out loud instead of silently
    leaving a table that cannot compile.
    """
    from agent.egress.mihomo_config import is_own_listener

    engine = block.get("engine") or {}
    try:
        port = int(engine.get("mixed_port") or 0)
    except (TypeError, ValueError):
        return set()
    if not port:
        return set()
    names: set[str] = set()
    for name, entry in (block.get("proxies") or {}).items():
        url = str((entry or {}).get("url") or "")
        if url and is_own_listener(url, port):
            names.add(str(name))
    return names


def _node_entry(node: dict[str, Any]) -> dict[str, Any]:
    """One member in the table's shape: the mapping's key is the name, not a field."""
    return {str(k): v for k, v in node.items() if k != "name"}


def _group_entry(group: dict[str, Any]) -> dict[str, Any]:
    """One group in the table's shape.  The health-check URL is dropped: the table's
    groups do not carry one (the compiler sets its own), so keeping it would be a
    value in the table that nothing reads."""
    entry: dict[str, Any] = {
        "type": str(group.get("type") or "select"),
        "include": list(group.get("include") or []),
    }
    interval = int(group.get("interval") or 0)
    if interval:
        entry["interval"] = interval
    return entry


def read_subscription(
    *,
    url: str = "",
    text: str = "",
    path: str = "",
    client: str = "",
    write_secrets: bool = True,
    pool_name: str = "POOL",
    keep_groups: bool = False,
) -> dict[str, Any]:
    """Pull a subscription from wherever it lives and unfold it into table shape.

    Four sources, one reader: a URL, a pasted body, a file, or **the cache of a
    desktop client that has no export button**.  The last one exists because on this
    box the user's provider ships a client and no way to copy the subscription link
    — but the client fetched the same Clash yaml we need and cached it, so the
    bytes are already here.

    The answer carries **names, never values**: credentials go to the root ``.env``
    (when ``write_secrets``) and the table keeps ``*_env`` names.  ``nodes`` and
    ``groups`` are mappings — the table's own shape — because that is what the
    runtime reads and what the pane edits.
    """
    from agent.egress import local_clients, subscription

    notes: list[str] = []
    source = ""
    body = ""
    if client:
        body, source = local_clients.read_cache(client)
    elif path:
        try:
            body = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            raise subscription.SubscriptionError(
                f"读不了 {path}：{type(exc).__name__}: {exc}"
            ) from None
        source = str(path)
    elif url:
        body = subscription.fetch(url)
        source = url
    elif text:
        body = text
        source = "（粘贴的文本）"
    else:
        raise subscription.SubscriptionError(
            "给我一个来源：--url / --file / --from-client，或者直接粘一段配置文本"
        )

    parsed = subscription.parse(body)
    members, written, notes2 = subscription.attach_env_names(parsed["nodes"], write=write_secrets)
    notes += list(parsed["notes"]) + list(notes2)

    nodes = {str(m["name"]): _node_entry(m) for m in members}
    incoming_groups = {str(g["name"]): _group_entry(g) for g in parsed["groups"]}
    built = build_pool_groups(list(nodes), pool_name=pool_name)
    if incoming_groups and keep_groups:
        notes.append(
            f"订阅自带 {len(incoming_groups)} 个组，按选择**没有导入**——"
            f"保留表里现有的分组，成员照更新（要换成它那套就去掉 keep_groups）"
        )
        # The parser's group-level warnings describe groups we are about to throw away
        # (`组 '…' 引用的 DIRECT … 已去掉`) — showing them would send the user looking
        # for a problem that does not exist in the table we are keeping.
        notes = [n for n in notes if not n.startswith("组 ")]
        incoming_groups = {}
    elif incoming_groups:
        notes.append(f"订阅自带 {len(incoming_groups)} 个组")
    else:
        notes.append(
            f"订阅没有组：按名字里的地方建了 {len(built) - 1} 个 select 组，"
            f"外加一个 url-test 的 {pool_name}（兜底规则指它）"
        )

    return {
        "ok": bool(nodes),
        "format": str(parsed["format"]),
        "source": source,
        "nodes": nodes,
        "groups": {**built, **incoming_groups},
        "secret_env_names": list(written),
        "notes": notes,
    }


def import_subscription(
    *,
    url: str = "",
    text: str = "",
    path: str = "",
    client: str = "",
    apply: bool = True,
    write_secrets: bool = True,
    catch_all: str = "",
    pool_name: str = "POOL",
    keep_groups: bool = False,
    refresh: bool = False,
    save_url: bool = False,
) -> dict[str, Any]:
    """Read a subscription and **write it into the table**.

    Merge semantics match the pane's, so the two surfaces agree: a name that comes
    in wins, a name that does not is kept.  "更新我的订阅" must not delete the node
    the user added by hand, and must not leave the old member of a renamed node
    behind forever either.

    ``catch_all`` points the table's last rule at a group — the engine's own
    catch-all cannot be the engine's own listener (the compiler refuses that), so a
    table that wants the pool to carry foreign traffic says so here, out loud.

    ``apply=False`` writes **nothing at all** — not the table, not the credentials.
    """
    from agent.egress import subscription

    if refresh:
        url = subscription.saved_url()
        if not url:
            raise subscription.SubscriptionError(
                f"没有存过订阅链接（`{subscription.SUB_URL_ENV}` 在根 `.env` 里是空的）——"
                f"先用 --url 导一次并加 --save，之后就能只点「更新」"
            )
        # 「更新」= 换成员，不换我排版好的分组（用户 10-28 拍）。
        keep_groups = True

    answer = read_subscription(
        url=url, text=text, path=path, client=client,
        write_secrets=write_secrets and apply, pool_name=pool_name, keep_groups=keep_groups,
    )
    if not answer["ok"]:
        answer["wrote"] = False
        return answer

    network = dict(read_egress().get("network") or {})
    block = dict(network.get("egress") or {})
    nodes = dict(block.get("nodes") or {})
    groups = dict(block.get("groups") or {})

    added_nodes = [name for name in answer["nodes"] if name not in nodes]
    added_groups = [name for name in answer["groups"] if name not in groups]
    nodes.update(answer["nodes"])
    groups.update(answer["groups"])

    rules = list(block.get("rules") or [])
    if catch_all:
        if not rules:
            answer["notes"].append(f"表里还没有规则，没有兜底规则可指 —— {catch_all} 建好了，但没人指它")
        else:
            last = dict(rules[-1])
            last["via"] = f"proxy:{catch_all}"
            rules[-1] = last
            answer["notes"].append(f"兜底规则改成了 proxy:{catch_all}")
    elif rules and str(rules[-1].get("via", "")).startswith("proxy:"):
        target = str(rules[-1].get("via"))[len("proxy:"):]
        if target in _own_listener_names(block):
            answer["notes"].append(
                f"兜底规则还指着 proxy:{target}——那是引擎自己的前门（引擎不能把流量交给它自己的"
                f"接收入口），要让它带流量就把兜底指到 {pool_name}"
            )

    block["nodes"] = nodes
    block["groups"] = groups
    if rules:
        block["rules"] = rules
    network["egress"] = block

    if not apply:
        answer["wrote"] = False
        answer["added_nodes"] = added_nodes
        answer["added_groups"] = added_groups
        answer["nodes"] = nodes
        answer["groups"] = groups
        return answer

    ok, issues = replace_egress(network)
    answer["wrote"] = ok
    answer["added_nodes"] = added_nodes
    answer["added_groups"] = added_groups
    answer["nodes"] = nodes
    answer["groups"] = groups
    if not ok:
        answer["ok"] = False
        answer["notes"].append("表没写下去：" + "；".join(issues))
    else:
        answer["notes"].append(
            f"表里现在有 {len(nodes)} 个成员、{len(groups)} 个组"
            f"（新增 {len(added_nodes)} 个成员、{len(added_groups)} 个组）"
        )
        if save_url:
            # Only after the table actually took it: a stored link that points at a
            # subscription this table rejected would make 「更新」 fail forever.
            stored = subscription.save_url(url)
            answer["stored_as"] = stored
            answer["notes"].append(
                f"订阅链接存进了根 `.env`（`{stored}`，值不回显）——以后只点「更新」就能刷"
            )
    return answer
