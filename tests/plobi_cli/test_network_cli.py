"""``plobi network status`` prints the one egress reading (WP-EGRESS-ROUTING)."""

from __future__ import annotations

import yaml

from agent.egress import rules as egress
from plobi_cli.network_cmd import cmd_network
from plobi_cli.subcommands.network import build_network_parser


class _Args:
    network_action = "status"


def _home(tmp_path, monkeypatch, egress_block: dict) -> None:
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump({"network": {"egress": egress_block}}), encoding="utf-8"
    )
    monkeypatch.setenv("PLOBI_HOME", str(tmp_path))
    monkeypatch.setattr(egress, "_TABLE_CACHE", None)


def test_the_command_is_registered_and_defaults_to_status(tmp_path):
    """`plobi network` and `plobi network status` both reach the same handler."""
    import argparse

    from plobi_cli.network_cmd import cmd_network as handler

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    build_network_parser(subparsers, cmd_network=handler)

    assert parser.parse_args(["network"]).func is handler
    assert parser.parse_args(["network", "status"]).func is handler


def test_it_says_which_rules_the_runtime_cannot_honor(tmp_path, monkeypatch, capsys):
    _home(
        tmp_path,
        monkeypatch,
        {
            "proxies": {"gw": {"url": "http://127.0.0.1:7895"}},
            "rules": [
                {"match": "geosite:cn", "via": "direct"},
                {"match": "*", "via": "proxy:gw"},
            ],
        },
    )

    assert cmd_network(_Args()) == 0
    out = capsys.readouterr().out

    assert "geosite:cn -> direct" in out
    assert "engine-only" in out, "a rule the table cannot honor must be named, not hidden"
    assert "2 rule(s)" in out and "catch-all proxy:gw" in out


def test_the_reading_comes_from_the_shared_renderer_not_a_second_one(tmp_path, monkeypatch, capsys):
    """裁定 94 again: the CLI is allowed one assembly of these facts."""
    import inspect

    from agent.egress.status import render

    _home(tmp_path, monkeypatch, {"rules": [{"match": "*", "via": "direct"}]})
    expected = render()
    cmd_network(_Args())
    printed = capsys.readouterr().out.splitlines()

    assert printed == expected
    assert "agent.egress.status" in inspect.getsource(cmd_network)
    source = inspect.getsource(cmd_network)
    assert 'get("egress")' not in source and '["egress"]' not in source


def test_no_table_is_answered_instead_of_crashing(tmp_path, monkeypatch, capsys):
    _home(tmp_path, monkeypatch, {})
    assert cmd_network(_Args()) == 0
    assert "not declared" in capsys.readouterr().out


class _Action:
    def __init__(self, action):
        self.network_action = action


def test_the_switch_actions_reach_the_supervisor_and_print_its_answer(monkeypatch, capsys):
    """`start` / `stop` / `reload` are thin: they drive engine and print its report.

    Pinned at the call because the whole product value here is that the CLI does
    not grow its own idea of what "running" means.
    """
    from agent.egress import engine
    from plobi_cli import network_cmd

    calls = []

    def _fake(name):
        def _fn(table, **kw):
            calls.append((name, table.engine_mixed_port))
            state = engine.STOPPED if name == "stop_ours" else engine.OURS
            return engine.EngineReport(state, name, 7895, "/w", "pool", 4242, "204 via x")

        return _fn

    for name in ("ensure", "stop_ours", "reload_engine"):
        monkeypatch.setattr(engine, name, _fake(name))

    for action in ("start", "stop", "reload"):
        assert network_cmd.cmd_network(_Action(action)) == 0

    assert calls == [("ensure", 0), ("stop_ours", 0), ("reload_engine", 0)]
    printed = capsys.readouterr().out
    assert "running-ours" in printed and "stopped" in printed


def test_stop_reports_a_foreign_engine_and_still_exits_zero(monkeypatch, capsys):
    """Refusing to kill somebody else's proxy is a normal answer, not a failure."""
    from agent.egress import engine
    from plobi_cli import network_cmd

    monkeypatch.setattr(
        engine, "stop_ours", lambda table, **kw: engine.EngineReport(engine.STOPPED, "nothing of ours", 0)
    )
    assert network_cmd.cmd_network(_Action("stop")) == 0
    assert "stopped" in capsys.readouterr().out


def test_a_failed_start_is_a_nonzero_exit(monkeypatch, capsys):
    """`plobi network start` is used by humans and scripts; it has to say 非零 when it did not work."""
    from agent.egress import engine
    from plobi_cli import network_cmd

    monkeypatch.setattr(
        engine, "ensure", lambda table, **kw: engine.EngineReport(engine.FAILED, "not on disk", 7895)
    )
    assert network_cmd.cmd_network(_Action("start")) == 1
    assert "failed" in capsys.readouterr().out


# ── `plobi network switch` — the bottom-bar overlay's command-line twin (纸 §42) ──

_SWITCH_TABLE = {
    "nodes": {"🇭🇰 香港W01": {"type": "ss", "server": "hk.example", "port": 1,
                              "cipher": "aes-256-gcm", "password_env": "PLOBI_T_PW"}},
    "groups": {"POOL": {"type": "url-test", "include": ["🇭🇰 香港W01"]}},
    "rules": [{"match": "geosite:cn", "via": "direct"}, {"match": "*", "via": "proxy:POOL"}],
    "engine": {"enabled": True, "mixed_port": 7895},
    "system_proxy": {"enabled": True},
}


class _SwitchArgs:
    network_action = "switch"
    transport = ""
    mode = ""
    exit_to = ""
    allow_foreign = False


def _quiet_supervision(monkeypatch):
    """The switch hands the table to supervision; a test must not touch this machine."""
    from agent.egress import engine as _engine
    from agent.egress import system_proxy as _system_proxy

    calls: list[str] = []

    class _Report:
        state = _engine.OURS

        def lines(self):
            return ["engine: running-ours (test)"]

    class _Plan:
        ok = True
        reason = "接管了（test）"

        def lines(self):
            return ["system proxy: test"]

    def reload_engine(table=None, **kw):
        calls.append("engine")
        return _Report()

    def ensure(table=None, **kw):
        calls.append("proxy")
        return _Plan()

    monkeypatch.setattr(_engine, "reload_engine", reload_engine)
    monkeypatch.setattr(_system_proxy, "ensure", ensure)
    return calls


def test_no_flags_reads_the_switch_without_writing(tmp_path, monkeypatch, capsys):
    _home(tmp_path, monkeypatch, _SWITCH_TABLE)

    assert cmd_network(_SwitchArgs()) == 0
    said = capsys.readouterr().out
    assert "transport=system" in said and "mode=rule" in said and "exit=POOL" in said, said
    assert (tmp_path / "config.yaml").read_text(encoding="utf-8").count("geosite:cn") == 1


def test_a_transport_change_writes_the_table_and_hands_it_to_supervision(tmp_path, monkeypatch, capsys):
    from plobi_cli import egress_config

    _home(tmp_path, monkeypatch, _SWITCH_TABLE)
    calls = _quiet_supervision(monkeypatch)
    args = _SwitchArgs()
    args.transport = "tun"

    assert cmd_network(args) == 0
    assert calls == ["engine", "proxy"], "拨开关必须当场生效，不是写个文件等人重启"
    saved = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert "tun:" in saved and "PlobiTun" in saved, saved
    assert "enabled: false" in saved.split("system_proxy")[1], "两种承载互斥：TUN 上来就把系统代理交还"
    assert egress_config.read_egress()["network"]["egress"]["nodes"], "换承载不许把成员抹掉"


def test_choosing_an_exit_reaches_the_parser_not_only_the_file(tmp_path, monkeypatch, capsys):
    _home(tmp_path, monkeypatch, _SWITCH_TABLE)
    _quiet_supervision(monkeypatch)
    args = _SwitchArgs()
    args.exit_to = "没有这个组"

    assert cmd_network(args) == 1
    assert "表没写进去" in capsys.readouterr().out


def _refusing_supervision(monkeypatch, *, first_state: str = "failed"):
    """First supervision pass refuses, the second (the rollback) succeeds.

    The refusal is what the TUN guard really returns on this box (non-admin shell,
    Tailscale on the machine): `EngineReport(FAILED, "TUN 被守卫拒了：…")` — and
    `reload_engine` has already stopped the running engine by then.
    """
    from agent.egress import engine as _engine
    from agent.egress import system_proxy as _system_proxy

    seen: list[tuple[str, str]] = []

    class _Report:
        def __init__(self, state: str, detail: str):
            self.state = state
            self.detail = detail

        def lines(self):
            return [f"engine: {self.state} — {self.detail}"]

    class _Plan:
        ok = True
        reason = "接管了（test）"

        def lines(self):
            return ["system proxy: test"]

    state = {"n": 0}

    def reload_engine(table=None, **kw):
        from agent.egress.switch import read as read_switch

        state["n"] += 1
        seen.append(("engine", str(read_switch(table)["transport"])))
        if state["n"] == 1:
            return _Report(first_state, "TUN 被守卫拒了：这个进程不是管理员")
        return _Report(_engine.OURS, "engine reloaded (test)")

    def ensure(table=None, **kw):
        from agent.egress.switch import read as read_switch

        seen.append(("proxy", str(read_switch(table)["transport"])))
        return _Plan()

    monkeypatch.setattr(_engine, "reload_engine", reload_engine)
    monkeypatch.setattr(_system_proxy, "ensure", ensure)
    return seen


def test_a_refused_tun_puts_the_table_and_the_machine_back(tmp_path, monkeypatch, capsys):
    """被守卫拒掉的那一拨不许留在表里：引擎已经停了，注册表也交还了。

    Reproduced shape (11-01): `set_transport("tun")` writes `system_proxy.enabled: false`
    in the same breath, and `reload_engine` stops our engine **before** the guard refuses —
    so without this rollback the command line left the machine with no engine, no system
    proxy and a table claiming TUN. The pane has done this since §42; the CLI did not.
    """
    from plobi_cli import egress_config

    _home(tmp_path, monkeypatch, _SWITCH_TABLE)
    seen = _refusing_supervision(monkeypatch)
    args = _SwitchArgs()
    args.transport = "tun"

    assert cmd_network(args) == 1
    said = capsys.readouterr().out
    assert "退回原样" in said, said
    #: 拨出去 → 退回：两次都交给监督，退回那一次读的必须是**旧的**那份表。
    assert seen == [("engine", "tun"), ("proxy", "tun"), ("engine", "system"), ("proxy", "system")], seen

    egress_config.read_egress()
    saved = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert "PlobiTun" not in saved or "enabled: false" in saved.split("tun:")[1].split("system_proxy")[0], saved
    assert "enabled: true" in saved.split("system_proxy")[1], "系统代理要交还回来，否则整机没出口"
    assert egress_config.read_egress()["network"]["egress"]["nodes"], "退回也不许把成员抹掉"


def test_a_foreign_engine_is_reported_not_reverted(tmp_path, monkeypatch, capsys):
    """别人占着端口时我们的声明没有生效，但**我们什么都没动** —— 那是另一个答案。

    Same predicate the pane uses (`state == failed`): a foreign engine on our port is not
    a refusal of this write, so the table keeps what the user asked for and the reading
    says why nothing moved.
    """
    _home(tmp_path, monkeypatch, _SWITCH_TABLE)
    _refusing_supervision(monkeypatch, first_state="running-foreign")
    args = _SwitchArgs()
    args.transport = "tun"

    assert cmd_network(args) == 1
    said = capsys.readouterr().out
    assert "退回原样" not in said, said
    saved = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert "PlobiTun" in saved, saved

