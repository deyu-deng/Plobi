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
