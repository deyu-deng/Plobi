"""Projecting the egress table onto spawned subprocesses (WP-EGRESS-ROUTING).

A spawned child cannot be handed a rule table — it can only be told "use this
exit, skip these hosts".  These tests pin what that translation preserves and
what it must never leak (a mirror that would hijack the package install policy).
"""

from __future__ import annotations

import pytest
import yaml

from agent.egress import rules as egress
from agent.egress.env_projection import apply_egress_env
from agent.egress.rules import load_table

_PROXY = "http://exit.test:8234"
_TABLE_KEYS = ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY", "no_proxy")


def _table(**egress_keys) -> egress.Table:
    raw = {
        "proxies": {"local": {"url": _PROXY}, "backup": {"url": "socks5://10.9.9.9:1080"}},
        "rules": [
            {"match": "api.github.com", "via": "direct"},
            {"match": "*.internal.test", "via": "proxy:backup"},
            {"match": "10.0.0.0/8", "via": "direct"},
            {"match": "*", "via": "proxy:local"},
        ],
    }
    raw.update(egress_keys)
    return load_table(config={"network": {"egress": raw}})


def test_no_table_changes_nothing(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", _PROXY)
    env = {"HTTPS_PROXY": _PROXY, "PATH": "/bin"}
    apply_egress_env(env, load_table(config={"network": {}}))
    assert env == {"HTTPS_PROXY": _PROXY, "PATH": "/bin"}


def test_the_projection_owns_the_network_keys():
    env = {"NO_PROXY": "leftover.from.parent", "PATH": "/bin"}
    apply_egress_env(env, _table())
    assert env["HTTPS_PROXY"] == env["HTTP_PROXY"] == env["ALL_PROXY"] == _PROXY
    assert "leftover.from.parent" not in env["NO_PROXY"]
    assert env["PATH"] == "/bin"  # unrelated keys untouched


def test_direct_rules_become_the_bypass_list_and_nothing_else_does():
    env: dict[str, str] = {}
    apply_egress_env(env, _table())
    entries = env["NO_PROXY"].split(",")
    assert "api.github.com" in entries
    assert "10.0.0.0/8" in entries  # written through; curl honors it, urllib does not
    # a proxied suffix must never appear as a bypass entry
    assert not [e for e in entries if "internal.test" in e]
    assert env["NO_PROXY"] == env["no_proxy"]


def test_an_all_direct_table_short_circuits_every_exit():
    env = {"HTTPS_PROXY": "http://inherited.test:1", "NO_PROXY": "stale"}
    apply_egress_env(
        env, _table(rules=[{"match": "*", "via": "direct"}])
    )
    assert env["NO_PROXY"] == "*" and env["no_proxy"] == "*"
    assert "HTTPS_PROXY" not in env


# --- the vocabulary ceiling is reported, not silently degraded ---------------


_UNEQUAL_TABLE = {
    "proxies": {"gw": {"url": "http://gw.test:8081"}},
    "rules": [
        {"match": "*.googleapis.com", "via": "proxy:gw"},
        {"match": "*", "via": "direct"},
    ],
}


def _loaded(raw: dict) -> egress.Table:
    return load_table(config={"network": {"egress": raw}})


def test_catch_all_direct_loses_named_exits_for_children_and_says_so(caplog):
    """Reported ceiling (reproduced 2026-10-10 22:5x by this line, then by 额度线).

    A child can only be told "one exit, plus hosts that skip it".  With a direct
    catch-all there is no exit to name, so the proxy rules above it drop.  The
    in-process resolver is right either way — which is why silence here would be
    the dangerous version of this bug.
    """
    import logging

    with caplog.at_level(logging.WARNING, logger="agent.egress.rules"):
        table = _loaded(_UNEQUAL_TABLE)

    assert [r.match for r in table.env_dropped_rules] == ["*.googleapis.com"]
    assert "*.googleapis.com" in caplog.text
    assert "in-process only" in caplog.text

    env: dict[str, str] = {}
    apply_egress_env(env, table)
    assert env["NO_PROXY"] == "*"
    assert "HTTPS_PROXY" not in env  # the loss itself, stated not hidden

    # ... and in-process routing still honours the dropped rule
    assert egress.decide(table, "oauth2.googleapis.com").proxy_url == "http://gw.test:8081"


def test_the_reverse_direction_reports_nothing(caplog):
    """Control group: exit + bypass list is exactly what the env can express."""
    import logging

    with caplog.at_level(logging.WARNING, logger="agent.egress.rules"):
        table = _loaded(
            {
                "proxies": {"gw": {"url": "http://gw.test:8081"}},
                "rules": [
                    {"match": "api.github.com", "via": "direct"},
                    {"match": "*", "via": "proxy:gw"},
                ],
            }
        )
    assert table.env_dropped_rules == ()
    assert "cannot be told" not in caplog.text

    env: dict[str, str] = {}
    apply_egress_env(env, table)
    assert env["HTTPS_PROXY"] == "http://gw.test:8081"
    assert env["NO_PROXY"] == "api.github.com"


def test_a_direct_catch_all_with_only_direct_rules_reports_nothing(caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="agent.egress.rules"):
        table = _loaded({"rules": [{"match": "*", "via": "direct"}]})
    assert table.env_dropped_rules == ()
    assert "cannot be told" not in caplog.text



def test_a_single_exit_is_the_default_and_the_rest_bypass_it():
    env: dict[str, str] = {}
    apply_egress_env(
        env,
        _table(
            rules=[
                {"match": "a.test", "via": "direct"},
                {"match": "*", "via": "proxy:backup"},
            ]
        ),
    )
    assert env["HTTPS_PROXY"] == "socks5://10.9.9.9:1080"
    assert env["NO_PROXY"] == "a.test"


def test_mirrors_reach_tools_that_read_a_registry_variable_and_no_further():
    env: dict[str, str] = {}
    # mirrors only, no rules: rewriting where a tool downloads from must not be
    # mistaken for a routing decision, and must never invent one
    mirrors_only = load_table(
        config={
            "network": {
                "egress": {"mirrors": {"npm": "https://registry.npmmirror.com"}}
            }
        }
    )
    apply_egress_env(env, mirrors_only)
    assert env["npm_config_registry"] == "https://registry.npmmirror.com"
    # the invariant the install policy depends on: no pip-shaped variable, ever
    assert not [k for k in env if "PIP" in k.upper() or "INDEX_URL" in k.upper()]
    assert not [k for k in env if k.upper() in _TABLE_KEYS]


def test_electron_mirror_is_projected_when_declared():
    env: dict[str, str] = {}
    apply_egress_env(
        env, _table(mirrors={"electron": "https://npmmirror.com/mirrors/electron/"})
    )
    assert env["ELECTRON_MIRROR"] == "https://npmmirror.com/mirrors/electron/"


# --- the two real spawn builders, not a hand-built dict ---------------------


@pytest.fixture()
def home_with_table(tmp_path, monkeypatch):
    home = tmp_path / "plobi-home"
    home.mkdir()
    (home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "network": {
                    "egress": {
                        "proxies": {"local": {"url": _PROXY}},
                        "mirrors": {"npm": "https://registry.npmmirror.com"},
                        "rules": [
                            {"match": "api.github.com", "via": "direct"},
                            {"match": "*", "via": "proxy:local"},
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PLOBI_HOME", str(home))
    monkeypatch.setattr(egress, "_TABLE_CACHE", None)
    monkeypatch.setenv("NO_PROXY", "must-not-survive.test")
    return home


def test_plobi_subprocess_env_carries_the_projection(home_with_table):
    from tools.environments.local import plobi_subprocess_env

    env = plobi_subprocess_env()
    assert env["HTTPS_PROXY"] == _PROXY
    assert env["NO_PROXY"] == "api.github.com"
    assert env["npm_config_registry"] == "https://registry.npmmirror.com"
    assert "must-not-survive.test" not in env["NO_PROXY"]


def test_the_terminal_path_carries_the_projection_too(home_with_table):
    """The terminal is where git / npm / pip actually run — same table, same answer.

    Both spawn builders must agree, or a rule works in one surface and silently
    not in the other, which is the shape this project has been bitten by before.
    """
    import os

    from tools.environments.local import _sanitize_subprocess_env, plobi_subprocess_env

    terminal_env = _sanitize_subprocess_env(os.environ.copy(), None)
    builder_env = plobi_subprocess_env()
    for key in _TABLE_KEYS:
        assert terminal_env.get(key) == builder_env.get(key), key
    assert terminal_env["HTTPS_PROXY"] == _PROXY


def test_extra_env_from_a_skill_cannot_smuggle_a_different_exit(home_with_table):
    """Projection runs last, so a forced override cannot silently reroute."""
    from tools.environments.local import _sanitize_subprocess_env

    env = _sanitize_subprocess_env(
        {}, {"_PLOBI_FORCE_HTTPS_PROXY": "http://elsewhere.test:1"}
    )
    assert env["HTTPS_PROXY"] == _PROXY


def test_an_unreadable_config_does_not_break_the_spawn_path(monkeypatch, caplog):
    """The projection lives in a per-spawn code path.

    A child process that cannot start because config plumbing failed (no
    resolvable home, unreadable file) is a new outage rather than a routing
    answer, so the load failure is reported once and the spawn proceeds with
    whatever the environment already said.
    """
    import logging

    import plobi_cli.config as pcfg
    from tools.environments.local import plobi_subprocess_env

    monkeypatch.setattr(egress, "_TABLE_CACHE", None)
    monkeypatch.setattr(egress, "_UNREADABLE_REPORTED", False)

    def _boom():
        raise RuntimeError("Could not determine home directory")

    monkeypatch.setattr(pcfg, "load_config_readonly", _boom)
    with caplog.at_level(logging.WARNING, logger="agent.egress.rules"):
        env = plobi_subprocess_env()

    assert "PATH" in env  # the child still gets a usable environment
    assert "Could not determine home directory" in caplog.text
    assert "network.egress" in caplog.text

    caplog.clear()
    monkeypatch.setattr(pcfg, "load_config_readonly", lambda: (_ for _ in ()).throw(ValueError("again")))
    plobi_subprocess_env()
    assert "again" not in caplog.text  # reported once per process, not per command
