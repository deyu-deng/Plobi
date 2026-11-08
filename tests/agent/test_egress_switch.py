"""The switch: one reading of the three controls, one writer for each (纸 §42).

用户 11-01 把控制面从设置页搬到左下底栏那个 overlay：VPN 开不开、走规则还是全局、TUN 还是
系统代理，再加一格选节点。这一档锁的是**形状**，不是文案：

* 读数只认表（``switch.read``），且 TUN 压在系统代理之上——两种整机承载同时开着，屏幕上
  只有一个词，那个词必须是假的；
* 写也只认表，并且**写完仍是合法的表**：每个动作都过一遍真解析器，所以「拨一下把别的键弄没了」
  在这里当场红，而不是等下次开机；
* 「按规则 / 全局」不碰兜底规则，「选节点」只碰兜底规则——这两句是同一个界面里的两个控件，
  写歪一个整机行为就变了。
"""

from __future__ import annotations

import pytest

from agent.egress import switch, tun
from agent.egress.mihomo_config import compile_config
from agent.egress.rules import EgressConfigError, load_table

_BASE = {
    "nodes": {"🇭🇰 香港W01": {"type": "ss", "server": "hk.example", "port": 1,
                              "cipher": "aes-256-gcm", "password_env": "PLOBI_EGRESS_TEST_PW"}},
    "groups": {"POOL": {"type": "url-test", "include": ["🇭🇰 香港W01"]}},
    "rules": [
        {"match": "geosite:cn", "via": "direct"},
        {"match": "10.0.0.0/8", "via": "direct"},
        {"match": "*", "via": "proxy:POOL"},
    ],
    "engine": {"enabled": True, "mixed_port": 7895, "dns": {"default-nameserver": ["223.5.5.5"]}},
    "system_proxy": {"enabled": True},
}


def _table(**extra):
    raw = {**_BASE, **extra}
    return load_table(config={"network": {"egress": raw}}, force_reload=True)


def _egress(**extra):
    return {**_BASE, **extra}


# ── the reading ──────────────────────────────────────────────────────────────


def test_system_proxy_declared_is_the_carrier_called_system():
    assert switch.read(_table()) == {"vpn": True, "transport": "system", "mode": "rule", "exit": "POOL"}


def test_tun_outranks_the_system_proxy_because_two_carriers_is_one_lie():
    ctl = switch.read(_table(engine={**_BASE["engine"], "tun": dict(tun.default_block(), enabled=True)}))

    assert ctl["transport"] == "tun" and ctl["vpn"] is True


def test_no_carrier_says_off_rather_than_guessing_from_the_engine():
    table = _table(system_proxy={"enabled": False})

    assert switch.read(table)["vpn"] is False and switch.read(table)["transport"] == "off"


def test_a_direct_catch_all_is_not_read_as_an_exit_named_direct():
    table = _table(rules=[{"match": "*", "via": "direct"}])

    assert switch.read(table)["exit"] == ""


def test_global_is_the_classification_gone_not_the_rules_gone():
    table = _table(rules=[{"match": "*", "via": "proxy:POOL"}])

    assert switch.read(table)["mode"] == "global"


# ── the writes: each one survives the real parser ────────────────────────────


def test_switching_to_tun_turns_the_system_proxy_off_in_the_same_breath():
    block = switch.set_transport(_egress(), "tun", tun_block=tun.default_block())

    assert block["engine"]["tun"]["enabled"] is True
    assert block["system_proxy"] == {"enabled": False}, "modes, not a stack of switches"
    #: the shape comes from the guard's own module, so a pane cannot invent an adapter name
    assert block["engine"]["tun"]["name"] == tun.DEFAULT_NAME
    _table(engine=block["engine"], system_proxy=block["system_proxy"])  # parses, so it is writable


def test_switching_back_to_system_keeps_the_tun_shape_but_disables_it(monkeypatch):
    monkeypatch.setenv("PLOBI_EGRESS_TEST_PW", "test-only-value")
    on = switch.set_transport(_egress(), "tun", tun_block=tun.default_block(allow_foreign=True))
    back = switch.set_transport(on, "system", tun_block={})

    assert back["system_proxy"] == {"enabled": True}
    assert back["engine"]["tun"]["enabled"] is False
    assert back["engine"]["tun"]["name"] == tun.DEFAULT_NAME, "拨回来不该把人配好的网卡抹掉"
    assert "tun:" not in compile_config(_table(engine=back["engine"], system_proxy=back["system_proxy"]))


def test_allow_foreign_is_never_a_default():
    assert tun.default_block()["allow-foreign"] is False
    assert tun.default_block(allow_foreign=True)["allow-foreign"] is True


def test_global_removes_the_classification_and_leaves_the_catch_all_last():
    block = switch.set_mode(_egress(), "global")

    assert [r["match"] for r in block["rules"]] == ["10.0.0.0/8", "*"]
    block = switch.set_mode(block, "rule")
    assert [r["match"] for r in block["rules"]] == ["10.0.0.0/8", "geosite:cn", "*"], "插回兜底之前"
    assert block["rules"][-1] == {"match": "*", "via": "proxy:POOL"}
    _table(rules=block["rules"])


def test_choosing_an_exit_only_moves_the_catch_all():
    block = switch.set_exit(_egress(), "🇭🇰 香港W01")

    assert block["rules"][0]["match"] == "geosite:cn" and block["rules"][-1] == {
        "match": "*",
        "via": "proxy:🇭🇰 香港W01",
    }
    assert switch.read(_table(rules=block["rules"]))["exit"] == "🇭🇰 香港W01"


def test_a_table_with_no_rules_still_gets_a_default_exit_to_point_at():
    block = switch.set_exit({"engine": _BASE["engine"]}, "POOL")

    assert block["rules"] == [{"match": "*", "via": "proxy:POOL"}]
    _table(rules=block["rules"])


def test_an_empty_exit_name_is_refused_rather_than_writing_a_bare_proxy():
    with pytest.raises(ValueError):
        switch.set_exit(_egress(), "  ")


def test_a_transport_nobody_named_is_refused():
    with pytest.raises(ValueError):
        switch.set_transport(_egress(), "vpn", tun_block={})


def test_the_three_actions_compose_into_one_table_read():
    """One write per click is the point: 三次动作之后读数就是那三次动作。"""
    block = switch.set_transport(_egress(), "tun", tun_block=tun.default_block(allow_foreign=True))
    block = switch.set_mode(block, "global")
    block = switch.set_exit(block, "🇭🇰 香港W01")

    ctl = switch.read(_table(engine=block["engine"], system_proxy=block["system_proxy"], rules=block["rules"]))
    assert ctl == {"vpn": True, "transport": "tun", "mode": "global", "exit": "🇭🇰 香港W01"}


def test_a_refused_table_is_still_a_refusal_after_the_switch(EgressConfigError_=EgressConfigError):
    """`set_*` does not validate — the writer does; this pins that a switch cannot smuggle
    a block the parser would reject, because `apply_switch` writes through `replace_egress`."""
    block = switch.set_exit(_egress(), "不存在的名字")
    with pytest.raises(EgressConfigError_):
        _table(rules=block["rules"])


# ── the surface contract: the pane binds to these two keys, it does not re-derive ──


def test_the_status_snapshot_hands_over_the_switch_and_the_block(monkeypatch):
    """A pane that recomputed "which carrier is on" from `rules` would be 裁定 94's bug.

    So: the reading and the writable shape leave through `snapshot`, and nothing else in the
    snapshot changes when they do — this is the half the overlay depends on.
    """
    from agent.egress import status

    class _Report:
        state = "running-ours"
        detail = "started by Plobi"
        mixed_port = 7895
        config_source = "compiled"
        pid = 1
        probe = "204"
        notes = ()

        def lines(self):
            return []

    monkeypatch.setattr(status.engine, "status", lambda table=None, home=None: _Report())
    monkeypatch.setattr(status.engine, "recent_downgrades", lambda *a, **kw: [])
    monkeypatch.setattr(status.system_proxy, "status", lambda table=None, home=None: {"ours": True})
    monkeypatch.setattr(status.system_proxy, "desktop_view", lambda table: {})

    snap = status.snapshot(_table())

    assert snap["switch"] == {"vpn": True, "transport": "system", "mode": "rule", "exit": "POOL"}
    assert snap["tun"]["requested"] is False, "no OS probes without a request"
    assert snap["tun"]["block"]["name"] == tun.DEFAULT_NAME
    assert snap["tun"]["block"]["stack"] == tun.DEFAULT_STACK


def test_the_switch_is_also_the_permission_the_engine_checks_first():
    """开关就是那次「允许本机多一个路由权威」的选择，engine.enabled 得跟着走。

    分家时的实测形状：底栏开关亮着、托盘报「未启用」、引擎从没起来。
    engine.py 的 snapshot() 第一道闸读的就是 engine.enabled，而它只在模板里有默认
    false，没有任何界面能把它写成 true——所以这个开关当时点什么都没用。
    """
    on = switch.set_transport(_egress(), "system", tun_block={})
    assert on["engine"]["enabled"] is True

    tun_on = switch.set_transport(_egress(), "tun", tun_block=tun.default_block())
    assert tun_on["engine"]["enabled"] is True

    off = switch.set_transport(on, "off", tun_block={})
    assert off["engine"]["enabled"] is False, "关掉开关就是把授权一起收回"


#: 升级上来的旧表与设置页写出的表都没有 ``engine.enabled`` 这一格。
_ENGINE_BEFORE_THE_PERMISSION = {"mixed_port": 7895, "dns": {"default-nameserver": ["223.5.5.5"]}}


def test_an_absent_permission_key_follows_the_carrier_the_table_already_chose():
    """缺键不等于说不：表里已经写着把流量交给引擎，就是那次授权。

    读成 ``get("enabled", False)`` 就分不开「用户说了不」和「没人写过这一格」，
    于是升级上来的表全是后者——开关亮着、托盘说未启用、引擎一次都没起过。
    """
    table = _table(engine=dict(_ENGINE_BEFORE_THE_PERMISSION), system_proxy={"enabled": True})
    assert table.engine_enabled is True

    quiet = _table(engine=dict(_ENGINE_BEFORE_THE_PERMISSION), system_proxy={"enabled": False})
    assert quiet.engine_enabled is False, "没表态、也没有承载 ⇒ 就是关着"

    refused = _table(
        engine={**_ENGINE_BEFORE_THE_PERMISSION, "enabled": False},
        system_proxy={"enabled": True},
    )
    assert refused.engine_enabled is False, "显式写下的 false 是有人真的说了不"
