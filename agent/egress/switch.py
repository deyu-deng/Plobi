"""The three facts behind the VPN switch, read and written in exactly one place.

用户 11-01 把控制面从设置页搬到底栏那个 overlay：一屏开关（VPN 通不通、走规则还是
全局、TUN 还是系统代理），一屏选节点。四个面（overlay、设置页、`plobi network switch`、
托盘）问的是同一句话，所以**同一句话只许有一处实现**（裁定 94）——这一格就是那处。

三条不改口的形状：

* **读数只认表**。`vpn` / `transport` / `mode` / `exit` 全部从 ``network.egress`` 那一份
  表里读出来，不看进程、不看注册表：注册表与引擎的实际状态是 :mod:`agent.egress.status`
  的另一半（``system_proxy.status`` / ``engine.status``），两者不许合并成一个词——
  「表说要接管」和「现在真的接管着」在两处注册表被改写的机器上是两件事（§34 实测过）。
* **写也只认表**。动作函数吃的都是**存盘的原始块**（``read_egress()`` 给回的那份），
  改完仍由解析器判形状：这一格不建第二套校验，也不发明默认值。
* **「按规则 / 全局」不是两种引擎模式**，是同一张表里那条分类直连在不在。全局 = 把
  ``geosite:cn → direct`` 摘掉（其余规则照旧），所以兜底规则永远还是那条兜底规则。
"""

from __future__ import annotations

from typing import Any

from agent.egress.rules import Table

#: The classification rule that *is* "按规则".  Named once: both readings and both
#: writes look for this exact string, so a pane cannot disagree with the CLI.
RULE_MODE_MATCH = "geosite:cn"

TRANSPORTS = ("tun", "system", "off")
MODES = ("rule", "global")


def transport_of(table: "Table") -> str:
    """Which machine-wide carrier the table asks for: TUN first, then system proxy.

    TUN outranks the proxy on purpose: a machine whose default route is ours does not need
    a static proxy too, and the pair would say two different things about the same traffic.
    """
    if table.engine_tun.get("enabled") is True:
        return "tun"
    if table.system_proxy_enabled:
        return "system"
    return "off"


def mode_of(table: "Table") -> str:
    """"rule" when the CN classification is still in the table, else "global"."""
    return "rule" if any(
        rule.kind == "geosite" and rule.match == RULE_MODE_MATCH for rule in table.rules
    ) else "global"


def exit_of(table: "Table") -> str:
    """What the catch-all sends everything to — the exit the switch is really picking.

    Only a ``proxy:`` target counts: a bare ``direct`` catch-all means the machine has no
    default exit, and reading that as an exit named "direct" would put words on a card
    that the table never wrote.
    """
    if not table.rules:
        return ""
    via = str(table.rules[-1].via or "")
    return via[len("proxy:") :] if via.startswith("proxy:") else ""


def read(table: "Table") -> dict[str, Any]:
    """The control reading, from the loaded table: what the switch shows."""
    transport = transport_of(table)
    return {
        "vpn": transport != "off",
        "transport": transport,
        "mode": mode_of(table),
        "exit": exit_of(table),
    }


def _block(egress: dict[str, Any] | None) -> dict[str, Any]:
    """A copy we may mutate — never the caller's object, the pane keeps using its own."""
    return {k: v for k, v in (egress or {}).items()}


def _engine(block: dict[str, Any]) -> dict[str, Any]:
    engine = block.get("engine")
    out = dict(engine) if isinstance(engine, dict) else {}
    block["engine"] = out
    return out


def set_transport(block: dict[str, Any] | None, transport: str, *, tun_block: dict[str, Any]) -> dict[str, Any]:
    """Ask for one machine-wide carrier, and turn the other one off.

    ikuuu 那类客户端的形状（用户 11-01 两张截图）：TUN 与系统代理是**互斥的两种模式**，
    不是一个可以叠着开的两个开关。叠着开不报错但语义说不清——浏览器走静态代理、别的走
    默认路由，同一台机器两种出口，出问题没人能解释。所以这里一次只留一个。

    Turning TUN off keeps its shape (`name` / `stack` / `allow-foreign`) with
    `enabled: false`: 「先拨到系统代理，再拨回 TUN」不该把人配好的那块网卡抹掉。
    """
    if transport not in TRANSPORTS:
        raise ValueError(f"transport {transport!r} is not one of {', '.join(TRANSPORTS)}")
    out = _block(block)
    engine = _engine(out)
    current = engine.get("tun")
    carried = dict(current) if isinstance(current, dict) else {}

    if transport == "tun":
        engine["tun"] = {**carried, **(tun_block or {}), "enabled": True}
    else:
        engine["tun"] = {**carried, "enabled": False}
    out["system_proxy"] = {"enabled": transport == "system"}
    return out


def set_mode(block: dict[str, Any] | None, mode: str) -> dict[str, Any]:
    """Add or drop the CN-direct classification; the catch-all rule is never touched."""
    if mode not in MODES:
        raise ValueError(f"mode {mode!r} is not one of {', '.join(MODES)}")
    out = _block(block)
    raw = out.get("rules")
    rules = [dict(r) for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []
    kept = [r for r in rules if str(r.get("match", "")).strip() != RULE_MODE_MATCH]

    if mode == "rule":
        # Inserted just before the catch-all: after it, the classification would never be
        # reached, and mihomo would compile a rule list whose last entry isn't MATCH.
        last = kept[-1] if kept and str(kept[-1].get("match", "")).strip() == "*" else None
        entry = {"match": RULE_MODE_MATCH, "via": "direct"}
        kept = (kept[:-1] + [entry, last]) if last else kept + [entry]
    out["rules"] = kept
    return out


def set_exit(block: dict[str, Any] | None, name: str) -> dict[str, Any]:
    """Point the catch-all at a group or a member — the node card doing its one job."""
    target = str(name or "").strip()
    if not target:
        raise ValueError("an exit name is required")
    out = _block(block)
    raw = out.get("rules")
    rules = [dict(r) for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []
    via = f"proxy:{target}"
    if rules and str(rules[-1].get("match", "")).strip() == "*":
        rules[-1]["via"] = via
    elif rules:
        # No catch-all yet: append one rather than re-point a named rule, because "the
        # default exit" has to mean *everything that did not match* — that is the only
        # sentence the card is making.
        rules.append({"match": "*", "via": via})
    else:
        rules = [{"match": "*", "via": via}]
    out["rules"] = rules
    return out
