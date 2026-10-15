"""The TUN guard (WP-EGRESS-ROUTING §24).

Nothing here creates an adapter or asks for elevation.  What is under test is the
refusal — the machine-wide route is the one thing on this box that, taken wrongly,
leaves the user with no working network until a reboot, so the guard is the product
and the emission is what it permits.
"""

from __future__ import annotations

import pytest

from agent.egress import tun
from agent.egress.engine import FAILED, ensure
from agent.egress.mihomo_config import compile_config
from agent.egress.rules import EgressConfigError, load_table

_PORT = 7899

_BASE = {
    # The exit is *upstream* of the engine, never its own mixed-port — the compiler
    # refuses that self-loop, and it refused my first draft of this file.
    "proxies": {"gw": {"url": "http://127.0.0.1:7898"}},
    "rules": [{"match": "*", "via": "proxy:gw"}],
    "engine": {"enabled": True, "mixed_port": _PORT, "binary": ""},
}

_OUR_TUN = {"enabled": True, "name": "plobi-tun", "stack": "system"}


def _table(**extra):
    egress = {**_BASE, **extra}
    return load_table(config={"network": {"egress": egress}}, force_reload=True)


def _tun_table():
    return _table(engine={**_BASE["engine"], "tun": dict(_OUR_TUN)})


@pytest.fixture
def quiet_guard(monkeypatch):
    """A machine the guard would let through: Windows, elevated, nobody else's TUN."""
    monkeypatch.setattr(tun, "supported", lambda: True)
    monkeypatch.setattr(tun, "is_admin", lambda: (True, ""))
    monkeypatch.setattr(tun, "default_route_holders", lambda: ([{"ifIndex": 12, "name": "Wi-Fi",
                                                                 "description": "Intel Wi-Fi"}], ""))
    monkeypatch.setattr(tun, "tun_class_adapters", lambda: ([], ""))
    return None


# ── the table's shape ────────────────────────────────────────────────────────

def test_no_tun_block_means_no_tun():
    assert _table().engine_tun == {}


def test_turned_off_is_said_not_guessed():
    table = _table(engine={**_BASE["engine"], "tun": {"enabled": False}})
    assert table.engine_tun["enabled"] is False


@pytest.mark.parametrize(
    "raw,want",
    [
        ({"enabled": True, "stack": "system"}, "name is required"),
        ({"name": "plobi-tun", "stack": "system"}, "needs an explicit"),
        ({"enabled": True, "name": "plobi-tun"}, "stack"),
        ({"enabled": True, "name": "plobi-tun", "stack": "mixed"}, "not one of"),
        ({"enabled": True, "name": "../etc", "stack": "system"}, "not a usable adapter name"),
        (["not", "a", "mapping"], "must be a mapping"),
    ],
)
def test_a_half_written_tun_block_is_refused_at_the_table(raw, want):
    """Nothing is defaulted here: an unnamed adapter or an unchosen stack is a mistake
    that ends up owning somebody's default route."""
    with pytest.raises(EgressConfigError) as exc:
        _table(engine={**_BASE["engine"], "tun": raw})
    assert want in str(exc.value), str(exc.value)


def test_underscore_spelling_reaches_the_engine_as_the_engine_wants_it():
    table = _table(engine={**_BASE["engine"], "tun": {"enabled": True, "name": "plobi-tun",
                                                      "stack": "gvisor", "strict_route": True}})
    assert table.engine_tun["strict-route"] is True, table.engine_tun


# ── the compiler emits only what the table stated ────────────────────────────

def test_the_compiled_config_carries_the_tun_block_verbatim():
    said = compile_config(_tun_table())
    assert "tun:" in said and "name: plobi-tun" in said and "stack: system" in said


def test_a_table_without_tun_gets_no_tun_block():
    assert "tun:" not in compile_config(_table())


def test_a_disabled_tun_block_is_not_emitted():
    table = _table(engine={**_BASE["engine"], "tun": {"enabled": False}})
    assert "tun:" not in compile_config(table)


# ── the guard ────────────────────────────────────────────────────────────────

def test_a_platform_we_have_not_implemented_is_refused(monkeypatch):
    monkeypatch.setattr(tun.sys, "platform", "linux")
    verdict = tun.preflight(_tun_table())
    assert verdict["ok"] is False and "Windows" in verdict["reasons"][0]


def test_no_elevation_is_refused_without_asking_for_it(monkeypatch, quiet_guard):
    monkeypatch.setattr(tun, "is_admin", lambda: (False, ""))
    verdict = tun.preflight(_tun_table())
    assert verdict["ok"] is False
    assert any("管理员" in r and "UAC" in r for r in verdict["reasons"]), verdict["reasons"]


def test_somebody_elses_tun_means_we_do_not_take_the_default_route(monkeypatch, quiet_guard):
    monkeypatch.setattr(
        tun, "tun_class_adapters",
        lambda: ([{"name": "tun0", "description": "Wintun Userspace Tunnel", "status": "Up"}], ""),
    )
    verdict = tun.preflight(_tun_table())
    assert verdict["ok"] is False
    assert any("tun0" in r and "默认路由" in r for r in verdict["reasons"]), verdict["reasons"]


def test_our_own_adapter_being_present_is_not_a_conflict(monkeypatch, quiet_guard):
    """Re-arming after a reload: ours is already there, so the guard must not refuse itself."""
    monkeypatch.setattr(
        tun, "tun_class_adapters",
        lambda: ([{"name": "plobi-tun", "description": "Wintun Userspace Tunnel", "status": "Up"}], ""),
    )
    assert tun.preflight(_tun_table())["ok"] is True


def test_an_unreadable_routing_table_is_not_read_as_safe(monkeypatch, quiet_guard):
    """`unknown` never means `allowed` when the action is taking the default route."""
    monkeypatch.setattr(tun, "default_route_holders", lambda: ([], "Get-NetRoute : Access denied"))
    verdict = tun.preflight(_tun_table())
    assert verdict["ok"] is False and verdict["probe_errors"], verdict


def test_a_table_that_asked_for_nothing_is_answered_without_probing(monkeypatch):
    calls = []
    monkeypatch.setattr(tun, "default_route_holders", lambda: calls.append(1) or ([], ""))
    verdict = tun.preflight(_table())
    assert verdict["ok"] is False and verdict["requested"] is False
    assert calls == [], "an unrequested TUN must not cost two PowerShell probes"


# ── supervision: the gate runs before anything is written or started ─────────

@pytest.fixture
def inert(monkeypatch, tmp_path):
    state = {"spawn": [], "up": set()}
    monkeypatch.setattr("agent.egress.engine.listening", lambda port, **kw: port in state["up"])
    monkeypatch.setattr("agent.egress.engine.probe_engine",
                        lambda port, **kw: (True, f"204 via 127.0.0.1:{port}") if port in state["up"] else (False, "no listener"))

    def _spawn(snap, log_path):
        state["spawn"].append(str(snap.workdir))
        state["up"].add(snap.port)
        return 4242, 1000.0

    monkeypatch.setattr("agent.egress.engine._spawn", _spawn)
    monkeypatch.setattr("agent.egress.engine.verify_with_binary", lambda b, p: ("VERIFIED", ""))
    binary = tmp_path / "mihomo.exe"
    binary.write_text("not a binary", encoding="utf-8")
    state["binary"] = str(binary)
    return state


def test_a_refused_tun_never_writes_a_config_and_never_spawns(monkeypatch, tmp_path, inert):
    table = _table(engine={**_BASE["engine"], "binary": inert["binary"], "tun": dict(_OUR_TUN)})
    monkeypatch.setattr(tun, "supported", lambda: True)
    monkeypatch.setattr(tun, "is_admin", lambda: (False, ""))
    monkeypatch.setattr(tun, "default_route_holders", lambda: ([], ""))
    monkeypatch.setattr(tun, "tun_class_adapters", lambda: ([], ""))

    rep = ensure(table, home=tmp_path / "home", wait_s=0)

    assert rep.state == FAILED, rep
    assert "管理员" in rep.detail, rep.detail
    assert inert["spawn"] == [], "a refused TUN must not reach the spawn path"
    assert not (tmp_path / "home" / "egress" / "runtime" / "config.yaml").exists(), \
        "nothing is written when the guard says no"


def test_a_passing_guard_starts_and_the_tun_block_is_in_what_we_wrote(monkeypatch, tmp_path, inert, quiet_guard):
    table = _table(engine={**_BASE["engine"], "binary": inert["binary"], "tun": dict(_OUR_TUN)})

    rep = ensure(table, home=tmp_path / "home", wait_s=0)

    assert inert["spawn"], rep
    written = (tmp_path / "home" / "egress" / "runtime" / "config.yaml").read_text(encoding="utf-8")
    assert "tun:" in written and "name: plobi-tun" in written
    #: ① 的正面：放行也要把两条机器读数留下，否则"没人占着默认路由"只是某个人的一次肉眼。
    said = " ".join(rep.notes)
    assert "默认路由: 1 条" in said and "Wi-Fi" in said, rep.notes
    assert "场上别人的 tunnel/虚拟网卡: 无" in said, rep.notes


def test_the_two_machine_readings_are_printed_even_when_it_refuses(monkeypatch, quiet_guard):
    """Same reason, refusal side: the reader has to see *who* we are refusing to fight."""
    monkeypatch.setattr(tun, "is_admin", lambda: (False, ""))
    monkeypatch.setattr(
        tun, "tun_class_adapters",
        lambda: ([{"name": "Wintun-WG", "description": "Wintun Userspace Tunnel", "status": "Up"}], ""),
    )

    lines = tun.status_lines(tun.preflight(_tun_table()))

    assert any("默认路由: 1 条" in l and "Wi-Fi" in l for l in lines), lines
    assert any("Wintun-WG" in l for l in lines), lines
    assert lines[0].startswith("tun: 守卫拒绝开启"), lines


def test_an_unreadable_route_table_is_said_as_unreadable(monkeypatch, quiet_guard):
    monkeypatch.setattr(tun, "default_route_holders", lambda: ([], "Get-NetRoute : Access denied"))

    lines = tun.status_lines(tun.preflight(_tun_table()))

    assert any("默认路由: 读不到" in l and "Access denied" in l for l in lines), lines


def test_a_failed_adapter_probe_never_prints_as_nobody_is_out_there(monkeypatch, quiet_guard):
    """10-15 09:0x 我用真机抓到的形状：那行曾经印成「无（探针成功）」，而探针是失败的。"""
    monkeypatch.setattr(tun, "tun_class_adapters", lambda: ([], "ExpressionsMustBeFirstInPipeline"))

    lines = tun.status_lines(tun.preflight(_tun_table()))

    assert any("读不到" in l and "ExpressionsMustBeFirstInPipeline" in l for l in lines), lines
    assert not any("无（探针成功）" in l for l in lines), lines
    assert tun.preflight(_tun_table())["ok"] is False


@pytest.mark.skipif("sys.platform != 'win32'")
def test_the_two_real_probes_actually_parse_on_this_box():
    """The mocked tests cannot catch a malformed PowerShell script — this one runs the real thing.

    Read-only: `Get-NetRoute` / `Get-NetAdapter -IncludeHidden`.  A probe that errors is
    not a green light, so asserting the error string is empty is the whole point.
    """
    holders, err_h = tun.default_route_holders()
    adapters, err_a = tun.tun_class_adapters()

    assert err_h == "", err_h
    assert err_a == "", err_a
    assert isinstance(holders, list) and isinstance(adapters, list)
    if holders:
        assert {"ifIndex", "name", "description"} <= set(holders[0]), holders[0]


def test_a_pool_workdir_cannot_be_handed_a_tun_by_us(monkeypatch, tmp_path, inert, quiet_guard):
    """The engine running from somebody else's directory: we do not rewrite that file."""
    pool = tmp_path / "pool" / "config.yaml"
    pool.parent.mkdir()
    pool.write_text("mixed-port: 7895\n", encoding="utf-8")
    table = _table(engine={**_BASE["engine"], "binary": inert["binary"],
                           "mixed_port": 7895, "include_files": [str(pool)],
                           "tun": dict(_OUR_TUN)})

    rep = ensure(table, home=tmp_path / "home", wait_s=0)

    assert rep.state == FAILED, rep
    assert "pool" in rep.detail or "别人维护" in rep.detail, rep.detail
    assert "not ours to rewrite" in " ".join(rep.notes), rep.notes
