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
