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

_OK = system_proxy.ProxyPlan(True, "test")


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


@pytest.fixture(autouse=True)
def no_real_watchdog(monkeypatch):
    """接管会起一个脱离子进程；测试里只记账，不真起。"""
    from agent.egress import watchdog

    calls: dict[str, int] = {"start": 0, "stop": 0}
    monkeypatch.setattr(watchdog, "start", lambda **kw: (calls.__setitem__("start", calls["start"] + 1),
                                                         (True, "test: up"))[1])
    monkeypatch.setattr(watchdog, "stop", lambda **kw: (calls.__setitem__("stop", calls["stop"] + 1),
                                                        (True, "test: nothing"))[1])
    monkeypatch.setattr(watchdog, "status", lambda **kw: {"alive": False, "pid": None,
                                                          "age_seconds": None, "detail": "test"})
    return calls


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


def test_enable_backs_up_then_points_the_system_at_the_engine_not_at_a_pac(machine, tmp_path):
    before = dict(machine)
    plan = system_proxy.enable(_table(), home=tmp_path)
    assert plan.ok, plan.reason
    assert machine["ProxyEnable"] == 1
    # 10-30 实测（A/B/A 两轮）：`AutoConfigURL` 一挂上，WinINET 的 HTTPS 就全断。
    # 所以静态那一路是唯一的挂载点，PAC 只作为文件留在盘上。
    assert "AutoConfigURL" not in machine, machine
    assert machine["ProxyServer"] == "127.0.0.1:7895", machine
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
    assert "AutoConfigURL" not in reg, reg
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

def test_a_stale_autoconfigurl_gets_cleared_because_it_breaks_https(machine, tmp_path):
    """挂 PAC 会让 WinINET 的 HTTPS 全断（10-30 实测），所以接管时看到就得摘掉。

    这台机器上真实存在过：10-28 那一版把 `AutoConfigURL` 指到我们的 http PAC，
    google 就打不开了——不是引擎的错，是挂载点的形状错。
    """
    machine["AutoConfigURL"] = "http://127.0.0.1:7896/plobi-egress.pac"

    assert system_proxy.status(_table(), home=tmp_path)["pac_advertised"] is True
    system_proxy.enable(_table(), home=tmp_path)

    assert "AutoConfigURL" not in machine, machine
    assert system_proxy.status(_table(), home=tmp_path)["pac_advertised"] is False


def test_ours_is_read_from_the_static_entry_not_from_memory(machine, tmp_path):
    """`ours` 必须能从注册表读出来：接管在不在，看的是端口指没指我们。"""
    table = _table(system_proxy={"enabled": True})
    assert system_proxy.status(table, home=tmp_path)["ours"] is False

    system_proxy.enable(table, home=tmp_path)
    assert system_proxy.status(table, home=tmp_path)["ours"] is True

    machine["ProxyServer"] = "127.0.0.1:7890"  # 别的程序把系统代理改走了
    assert system_proxy.status(table, home=tmp_path)["ours"] is False

def test_the_takeover_starts_the_watchdog_and_off_stops_it(machine, tmp_path, no_real_watchdog):
    """10-29：引擎掉了两次都没人拉 ⇒ 接管时起看门狗，交还时停掉它。"""
    system_proxy.enable(_table(), home=tmp_path)
    assert no_real_watchdog["start"] == 1, no_real_watchdog

    system_proxy.disable(home=tmp_path)
    assert no_real_watchdog["stop"] == 1, no_real_watchdog

def test_ensure_lights_the_engine_before_it_plans_the_takeover(machine, tmp_path, monkeypatch):
    """10-31 实测：重启后没有引擎在听 ⇒ plan() 以「带不动流量」拒绝接管，而没有任何人再去点引擎。

    开机那条路径必须先起引擎：顺序反了，"接管被拒"就会被读成"这台机器不需要代理"。
    """
    from agent.egress import engine

    calls: list[str] = []
    monkeypatch.setattr(engine, "ensure", lambda table, **kw: calls.append("engine") or None)
    monkeypatch.setattr(system_proxy, "enable", lambda table, **kw: (calls.append("enable"), _OK)[1])

    table = _table(system_proxy={"enabled": True})
    system_proxy.ensure(table, home=tmp_path)

    assert calls == ["engine", "enable"], calls



# ── 交还时那根「尸体」（10-31 实测，11-01 落档）───────────────────────────────

def _closed_loopback_port() -> int:
    """Bind and release, so the port is provably nobody's for the length of one test."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_a_backup_whose_proxy_is_gone_is_handed_back_as_direct(machine, tmp_path):
    """备份里那位常常是**上一个 VPN 客户端自己**的 `127.0.0.1:7890`。它卸载了，端口没人听——
    一字不改地还原，等于把一具尸体交回系统代理：每个读系统代理的应用都报「没网」，
    而这看起来像是 Plobi 的错。判据：端口现在通不通，不是它以前是谁。"""
    port = _closed_loopback_port()
    directory = tmp_path / "egress"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / system_proxy.BACKUP_BASENAME).write_text(
        json.dumps({"ProxyEnable": 1, "ProxyServer": f"127.0.0.1:{port}"}), encoding="utf-8"
    )

    ok, detail = system_proxy.disable(home=tmp_path)

    assert ok, detail
    assert machine["ProxyEnable"] == 0, machine
    assert "ProxyServer" not in machine, machine


def test_a_backup_that_still_carries_traffic_is_restored_byte_for_byte(machine, tmp_path):
    """反向那一半：活着的代理不许被这条 fail-safe 顺手改掉——那是别人的配置。"""
    import socket

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    directory = tmp_path / "egress"
    directory.mkdir(parents=True, exist_ok=True)
    backup = {"ProxyEnable": 1, "ProxyServer": f"127.0.0.1:{port}"}
    (directory / system_proxy.BACKUP_BASENAME).write_text(json.dumps(backup), encoding="utf-8")
    try:
        ok, detail = system_proxy.disable(home=tmp_path)
    finally:
        listener.close()

    assert ok, detail
    assert machine["ProxyServer"] == f"127.0.0.1:{port}", machine


def test_a_remote_proxy_in_the_backup_is_never_second_guessed(machine, tmp_path):
    """只看 loopback：`http://parent.test:8080` 通不通不是这台机器能当场判的，也不归我们判。"""
    assert system_proxy._localhost_port_dead("http://parent.test:8080") is False
    assert system_proxy._localhost_port_dead("") is False
    assert system_proxy._localhost_port_dead("127.0.0.1:1") is True
