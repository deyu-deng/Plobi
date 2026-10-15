"""The machine-wide half of the egress table (WP-EGRESS-ROUTING §20).

Plobi-as-VPN is a system setting, so these tests pin the two things that make it
safe rather than impressive: **what it refuses to do**, and **that `off` gives back
exactly what it took**.  No registry, no real machine — `supported`, `read_current`
and `_write_registry` are the seams.
"""

from __future__ import annotations

import json

import pytest

from agent.egress import system_proxy
from agent.egress.rules import EgressConfigError, load_table

TABLE_RAW = {
    "proxies": {"gw": {"url": "http://127.0.0.1:7895"}},
    "rules": [
        {"match": "geosite:cn", "via": "direct"},
        {"match": "store.steampowered.com", "via": "direct"},
        {"match": "*.internal.test", "via": "direct"},
        {"match": "10.0.0.0/8", "via": "direct"},
        {"match": "fd00::/8", "via": "direct"},
        {"match": "*", "via": "proxy:gw"},
    ],
    "engine": {"enabled": True, "mixed_port": 7895},
}


def _table(**extra):
    raw = {**TABLE_RAW, **extra}
    return load_table(config={"network": {"egress": raw}}, force_reload=True)


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A fake Windows box: registry in a dict, home in tmp_path, engine carrying."""
    written: dict[str, object] = {"ProxyEnable": 0, "ProxyServer": "http://parent.test:8080"}

    def fake_write(values: dict[str, object]) -> None:
        # A real registry write deletes the values the restore dict omits — the fake
        # has to do the same or `off` would look clean while leaving our keys behind.
        for name in system_proxy.REG_VALUES:
            if name not in values or values[name] is None:
                written.pop(name, None)
            else:
                written[name] = values[name]

    monkeypatch.setattr(system_proxy, "supported", lambda: True)
    monkeypatch.setattr(system_proxy, "read_current", lambda: dict(written))
    monkeypatch.setattr(system_proxy, "_write_registry", fake_write)
    monkeypatch.setattr("agent.egress.engine.usable_exit", lambda url, **kw: (url, "test gate"))
    return written


# ── the PAC ────────────────────────────────────────────────────────────────


def test_the_pac_says_only_what_never_enters_the_proxy_and_hands_the_rest_over():
    text = system_proxy.pac_text(_table(), 7895)
    assert 'if (host.toLowerCase() === "store.steampowered.com") return "DIRECT";' in text
    assert 'if (dnsDomainIs(host, ".internal.test")) return "DIRECT";' in text
    assert 'if (isInNet(host, "10.0.0.0", "255.0.0.0")) return "DIRECT";' in text
    assert 'return "PROXY 127.0.0.1:7895";' in text
    # geosite is the engine's vocabulary, not PAC's: PAC must not fake a predicate for it.
    predicates = [line for line in text.splitlines() if line.lstrip().startswith("if (")]
    assert not [line for line in predicates if "geosite" in line], predicates


def test_a_range_pac_cannot_evaluate_is_commented_not_guessed():
    text = system_proxy.pac_text(_table(), 7895)
    assert "// network.egress rule fd00::/8 -> direct: not expressible in PAC" in text
    assert "fd00" not in "".join(line for line in text.splitlines() if "isInNet" in line)


# ── what enabling refuses to do ─────────────────────────────────────────────


def test_no_table_no_takeover(machine, tmp_path):
    empty = load_table(config={"network": {"egress": {"rules": []}}}, force_reload=True)
    plan = system_proxy.plan(empty, home=tmp_path)
    assert not plan.ok and "没有声明" in plan.reason


def test_no_declared_listen_port_is_refused(machine, tmp_path):
    raw = {k: v for k, v in TABLE_RAW.items() if k != "engine"}
    table = load_table(config={"network": {"egress": raw}}, force_reload=True)
    plan = system_proxy.plan(table, home=tmp_path)
    assert not plan.ok and "mixed_port" in plan.reason


def test_an_exit_that_cannot_carry_traffic_refuses_the_whole_machine(tmp_path, monkeypatch):
    monkeypatch.setattr(system_proxy, "supported", lambda: True)
    monkeypatch.setattr(system_proxy, "read_current", lambda: {"ProxyEnable": 0})
    monkeypatch.setattr("agent.egress.engine.usable_exit", lambda url, **kw: (None, "nothing is listening"))
    plan = system_proxy.plan(_table(), home=tmp_path)
    assert not plan.ok
    assert "带不动流量" in plan.reason, plan.reason


def test_an_unreadable_current_setting_blocks_the_takeover(machine, tmp_path, monkeypatch):
    monkeypatch.setattr(system_proxy, "read_current", lambda: {"unreadable": "OSError 5"})
    plan = system_proxy.plan(_table(), home=tmp_path)
    assert not plan.ok and "读不到" in plan.reason


def test_a_corrupt_backup_blocks_rather_than_overwriting_our_only_way_back(machine, tmp_path):
    directory = tmp_path / "egress"
    directory.mkdir(parents=True)
    (directory / system_proxy.BACKUP_BASENAME).write_text("{not json", encoding="utf-8")
    plan = system_proxy.plan(_table(), home=tmp_path)
    assert not plan.ok and "备份" in plan.reason


# ── enabling, and giving it all back ────────────────────────────────────────


def test_enable_backs_up_then_points_the_system_at_our_pac(machine, tmp_path):
    before = dict(machine)
    plan = system_proxy.enable(_table(), home=tmp_path)
    assert plan.ok, plan.reason
    assert machine["ProxyEnable"] == 1
    assert machine["AutoConfigURL"].startswith("file:///")
    assert machine["AutoConfigURL"].endswith(system_proxy.PAC_BASENAME)
    egress_dir = tmp_path / "egress"  # _home(home) is <home>/egress, not <home>
    assert (egress_dir / system_proxy.PAC_BASENAME).exists()

    backup = json.loads((egress_dir / system_proxy.BACKUP_BASENAME).read_text(encoding="utf-8"))
    assert backup == before, "the backup must be what the system actually said, verbatim"

    ok, detail = system_proxy.disable(home=tmp_path)
    assert ok, detail
    assert machine == before, f"off must restore byte for byte, got {machine}"
    assert not (egress_dir / system_proxy.BACKUP_BASENAME).exists()


def test_a_second_enable_does_not_burn_the_original_backup(machine, tmp_path):
    system_proxy.enable(_table(), home=tmp_path)
    egress_dir = tmp_path / "egress"
    first = (egress_dir / system_proxy.BACKUP_BASENAME).read_text(encoding="utf-8")
    machine.update({"ProxyEnable": 1, "ProxyServer": ""})  # what our own write looks like
    system_proxy.enable(_table(), home=tmp_path)
    assert (egress_dir / system_proxy.BACKUP_BASENAME).read_text(encoding="utf-8") == first
    system_proxy.disable(home=tmp_path)
    assert machine["ProxyServer"] == "http://parent.test:8080", "the parent's proxy comes back, not ours"


def test_off_refuses_to_touch_a_proxy_we_never_owned(machine, tmp_path):
    ok, detail = system_proxy.disable(home=tmp_path)
    assert not ok and "不是我们开的" in detail
    assert machine == {"ProxyEnable": 0, "ProxyServer": "http://parent.test:8080"}


def test_ensure_hands_the_machine_back_when_the_table_switch_goes_off(machine, tmp_path):
    table = _table(system_proxy={"enabled": True})
    assert table.system_proxy_enabled is True
    assert system_proxy.ensure(table, home=tmp_path).ok
    assert system_proxy.status(table, home=tmp_path)["ours"] is True

    off = _table(system_proxy={"enabled": False})
    assert system_proxy.ensure(off, home=tmp_path).ok
    assert system_proxy.status(off, home=tmp_path)["ours"] is False, "the table is the source of truth"
    assert machine["ProxyServer"] == "http://parent.test:8080"


def test_ensure_says_nothing_when_the_table_has_no_opinion(machine, tmp_path):
    assert system_proxy.ensure(_table(), home=tmp_path) is None


def test_the_switch_defaults_off_and_only_accepts_a_mapping():
    assert load_table(config={"network": {"egress": TABLE_RAW}}, force_reload=True).system_proxy_enabled is False
    with pytest.raises(EgressConfigError):
        load_table(config={"network": {"egress": {**TABLE_RAW, "system_proxy": "yes"}}}, force_reload=True)


def test_status_reads_the_registry_not_our_own_memory(machine, tmp_path):
    info = system_proxy.status(_table(), home=tmp_path)
    assert info["ours"] is False and info["declared"] is False
    lines = system_proxy.status_lines(info)
    assert any("不是 Plobi" in line for line in lines), lines

    system_proxy.enable(_table(system_proxy={"enabled": True}), home=tmp_path)
    info = system_proxy.status(_table(system_proxy={"enabled": True}), home=tmp_path)
    assert info["ours"] is True and info["backup_exists"] is True
    assert any("Plobi 在管" in line for line in system_proxy.status_lines(info))


def test_a_declared_but_inactive_takeover_is_pointed_out(machine, tmp_path):
    table = _table(system_proxy={"enabled": True})
    lines = system_proxy.status_lines(system_proxy.status(table, home=tmp_path))
    assert any("plobi network proxy on" in line for line in lines), lines


# ── 丙（用户 10-15 拍）：PAC 之外还要写静态代理，因为不是所有客户端会执行 PAC ──

def test_the_takeover_writes_a_static_proxy_as_well_because_pac_is_not_universal(machine, tmp_path):
    """`.NET` / 一批 CLI 只读静态代理，不跑 PAC——对它们「接管」曾经等于「直连」。"""
    p = system_proxy.enable(_table(), home=tmp_path, apply=False)

    reg = p.registry
    assert reg["ProxyEnable"] == 1
    assert reg["ProxyServer"] == "127.0.0.1:7895", reg
    assert reg["AutoConfigURL"].endswith("plobi-egress.pac"), reg
    override = reg["ProxyOverride"].split(";")
    for want in ("localhost", "127.*", "10.*", "*.internal.test", "store.steampowered.com"):
        assert want in override, override


def test_a_bypass_the_static_grammar_cannot_hold_is_named_not_dropped(machine, tmp_path):
    """`ProxyOverride` 没有 CIDR 也没有 IPv6；收窄必须**说出来**，不能悄悄发生。"""
    p = system_proxy.enable(_table(), home=tmp_path, apply=False)

    assert "fd00::/8" in p.override_unexpressible, p.override_unexpressible
    said = " ".join(p.lines())
    assert "ProxyOverride 表达不了" in said and "fd00::/8" in said, p.lines()


def test_byte_boundary_networks_become_prefix_wildcards_and_the_rest_are_named(tmp_path, machine):
    table = _table(rules=[
        {"match": "192.168.0.0/16", "via": "direct"},
        {"match": "172.16.0.0/12", "via": "direct"},
        {"match": "*", "via": "proxy:gw"},
    ])

    entries, unexpressible = system_proxy.override_entries(table)

    assert "192.168.*" in entries, entries
    assert "172.16.0.0/12" in unexpressible, unexpressible


def test_off_gives_back_the_static_values_it_wrote(machine, tmp_path):
    before = dict(machine)
    system_proxy.enable(_table(), home=tmp_path)
    assert machine["ProxyServer"] == "127.0.0.1:7895" and machine["ProxyEnable"] == 1

    ok, detail = system_proxy.disable(home=tmp_path)

    assert ok, detail
    assert machine == before, (machine, before)
    assert "ProxyOverride" not in machine, machine


@pytest.mark.skipif("sys.platform != 'win32'")
def test_read_current_reads_the_registry_and_never_invents_values():
    """口径 27 的第二半：报"没接管"之前先证明那次注册表读**成功**了。"""
    said = system_proxy.read_current()

    assert isinstance(said, dict)
    assert not ({"unreadable", "unsupported"} & set(said)), said
    for key in said:
        assert key in system_proxy.REG_VALUES, (key, said)
    #: the claim this whole slice rests on: `ours` is computed from what the registry says,
    #: never from "we enabled it a minute ago".
    info = system_proxy.status(_table(system_proxy={"enabled": False}))
    assert info["supported"] is True and isinstance(info["ours"], bool), info
