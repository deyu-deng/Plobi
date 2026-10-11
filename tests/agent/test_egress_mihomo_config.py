"""The mihomo projection: does the table survive translation into engine grammar? (WP-EGRESS-ROUTING)

The engine is optional and off by default, but the compiler ships now so the
table has exactly one meaning wherever it is projected.  Nothing here starts or
downloads a binary — and a config that was never accepted by the engine it
targets is reported as unverified, not as passing.
"""

from __future__ import annotations

import pytest
import yaml

from agent.egress import rules as egress
from agent.egress.mihomo_config import compile_config, verify_with_binary
from agent.egress.rules import EgressConfigError, Rule, Table, load_table

_MIXED = 7900


def _table() -> Table:
    return load_table(
        config={
            "network": {
                "egress": {
                    "proxies": {
                        "local": {"url": "http://127.0.0.1:8234"},
                        "creds": {"url": "http://pete:p%40ss@exit.test:8443"},
                        "sock": {"url": "socks5://10.9.9.9:1080"},
                    },
                    "rules": [
                        {"match": "geosite:github", "via": "direct"},
                        {"match": "api.github.com", "via": "direct"},
                        {"match": "*.internal.test", "via": "proxy:sock"},
                        {"match": "10.0.0.0/8", "via": "proxy:creds"},
                        {"match": "::1/128", "via": "proxy:local"},
                        {"match": "*", "via": "proxy:local"},
                    ],
                    "engine": {"enabled": True, "mixed_port": _MIXED},
                }
            }
        }
    )


def _render(table: Table | None = None, **kwargs) -> dict:
    return yaml.safe_load(compile_config(table or _table(), **kwargs))


def test_rule_order_and_every_matcher_survive_the_translation():
    doc = _render()
    assert doc["mixed-port"] == _MIXED
    assert doc["mode"] == "rule"
    assert doc["rules"] == [
        "GEOSITE,github,DIRECT",
        "DOMAIN,api.github.com,DIRECT",
        "DOMAIN-SUFFIX,internal.test,sock",
        "IP-CIDR,10.0.0.0/8,creds,no-resolve",
        "IP-CIDR6,::1/128,local,no-resolve",
        "MATCH,local",
    ]


def test_proxies_are_decomposed_into_the_fields_the_engine_reads():
    by_name = {p["name"]: p for p in _render()["proxies"]}
    assert by_name["local"] == {"name": "local", "type": "http", "server": "127.0.0.1", "port": 8234}
    assert by_name["sock"]["type"] == "socks5"
    # credentials arrive decoded, not percent-encoded, and never as one opaque URL
    assert by_name["creds"]["username"] == "pete"
    assert by_name["creds"]["password"] == "p@ss"
    assert by_name["creds"]["port"] == 8443


def test_the_listen_port_is_taken_from_the_table_unless_given_explicitly():
    assert _render()["mixed-port"] == _MIXED
    assert _render(mixed_port=7911)["mixed-port"] == 7911


def test_no_inherited_proxy_address_is_baked_into_the_rendered_config(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
    rendered = compile_config(_table())
    assert "7890" not in rendered


def test_the_control_plane_is_only_rendered_when_it_is_asked_for():
    assert "external-controller" not in _render()
    assert "secret" not in _render()
    doc = _render(external_controller="127.0.0.1:9090", secret="s3cret")
    assert doc["external-controller"] == "127.0.0.1:9090" and doc["secret"] == "s3cret"


def test_a_table_that_points_the_engine_at_itself_is_refused():
    """The one VERIFIED verdict that means nothing (reproduced 16:40, v1.19.32).

    Same `host:port` reads as "who to hand traffic to" for a spawned process and
    as "where to forward" for the engine — so the compiled config routes into the
    engine's own listener.  `mihomo -t` returns rc=0 "test is successful" for it;
    only the compiler can refuse it, and the supervision slice depends on that,
    because otherwise starting the engine is starting a dead loop.
    """
    self_loop = load_table(
        config={
            "network": {
                "egress": {
                    "proxies": {"gw": {"url": "http://127.0.0.1:7895"}},
                    "rules": [{"match": "api.github.com", "via": "direct"},
                              {"match": "*", "via": "proxy:gw"}],
                    "engine": {"enabled": True, "mixed_port": 7895},
                }
            }
        }
    )
    with pytest.raises(EgressConfigError) as err:
        compile_config(self_loop)
    message = str(err.value)
    assert "gw" in message and "7895" in message and "mixed-port" in message
    assert "test is successful" in message  # the reason this is not left to -t


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "LOCALHOST", "[::1]", "0.0.0.0"])
def test_every_loopback_spelling_of_the_same_port_is_caught(host):
    # IPv6 needs brackets in a URL; `urlparse("http://::1:7897")` yields no host
    # at all, which would have made this test prove the wrong thing.
    table = load_table(
        config={
            "network": {
                "egress": {
                    "proxies": {"gw": {"url": f"http://{host}:7897"}},
                    "rules": [{"match": "*", "via": "proxy:gw"}],
                    "engine": {"enabled": True, "mixed_port": 7897},
                }
            }
        }
    )
    with pytest.raises(EgressConfigError, match="own mixed-port"):
        compile_config(table)


def test_the_same_port_on_another_machine_is_not_self_reference():
    """Control group: this guard is about the socket, not about the number."""
    table = load_table(
        config={
            "network": {
                "egress": {
                    "proxies": {"gw": {"url": "http://10.0.0.5:7895"}},
                    "rules": [{"match": "*", "via": "proxy:gw"}],
                    "engine": {"enabled": True, "mixed_port": 7895},
                }
            }
        }
    )
    doc = _render(table)
    assert doc["mixed-port"] == 7895
    assert doc["proxies"][0]["server"] == "10.0.0.5"


def test_a_distinct_listen_port_still_compiles():
    """The healthy shape the engine projection needs: engine listens here,
    the named exit is something else (or the engine's own upstream)."""
    table = load_table(
        config={
            "network": {
                "egress": {
                    "proxies": {"upstream": {"url": "http://127.0.0.1:7895"}},
                    "rules": [{"match": "*", "via": "proxy:upstream"}],
                    "engine": {"enabled": True, "mixed_port": 7899},
                }
            }
        }
    )
    assert _render(table)["rules"] == ["MATCH,upstream"]


def test_a_table_without_rules_has_nothing_to_compile():
    inert = load_table(config={"network": {}})
    with pytest.raises(EgressConfigError, match="no egress rules"):
        compile_config(inert)


@pytest.mark.parametrize("port", [0, -1])
def test_a_half_declared_engine_is_refused_instead_of_guessing_a_port(port):
    with pytest.raises(EgressConfigError, match="listen port"):
        compile_config(_table(), mixed_port=port)


def test_a_rule_naming_an_undeclared_exit_is_refused_at_compile_too():
    table = Table(
        rules=(Rule(match="*", via="proxy:ghost", kind="all", value="*"),),
        proxies={"real": "http://h:1"},
    )
    with pytest.raises(EgressConfigError, match="ghost"):
        compile_config(table, mixed_port=_MIXED)


def test_an_exit_nobody_routes_to_is_still_declared():
    """Mid-edit tables are normal; dropping a declared exit would break the
    engine's own hot-reload diff for no benefit."""
    table = load_table(
        config={
            "network": {
                "egress": {
                    "proxies": {"used": {"url": "http://h:1"}, "idle": {"url": "http://i:2"}},
                    "rules": [{"match": "*", "via": "proxy:used"}],
                }
            }
        }
    )
    assert {p["name"] for p in _render(table, mixed_port=_MIXED)["proxies"]} == {"used", "idle"}


def test_verification_reports_unverified_rather_than_passing_without_a_binary(tmp_path):
    written = tmp_path / "config.yaml"
    written.write_text(compile_config(_table()), encoding="utf-8")

    verdict, why = verify_with_binary("", str(written))
    assert verdict == "UNVERIFIED" and "binary is empty" in why

    verdict, why = verify_with_binary("mihomo-not-installed-here", str(written))
    assert verdict == "UNVERIFIED" and "not found" in why

    verdict, why = verify_with_binary("mihomo-not-installed-here", str(tmp_path / "gone.yaml"))
    assert verdict == "UNVERIFIED"


def test_verification_refuses_a_file_the_engine_would_never_read(tmp_path):
    """Reproduced on this box with Mihomo Meta v1.19.32 (2026-10-11 15:56).

    ``-t -d <dir>`` validates ``<dir>/config.yaml`` and, when that file is
    missing, **creates a 16-byte empty one and reports success** — so rendering
    our config under any other name and trusting the exit code would certify
    bytes the engine never looked at.
    """
    wrong_name = tmp_path / "mihomo-config.yaml"
    wrong_name.write_text(compile_config(_table()), encoding="utf-8")

    verdict, why = verify_with_binary("mihomo", str(wrong_name))
    assert verdict == "UNVERIFIED"
    assert "config.yaml" in why and "report success" in why
    # the trap must not have left a stray validated file behind either
    assert not (tmp_path / "config.yaml").exists()


def test_membership_lives_in_an_included_file_and_stays_out_of_the_table():
    """ss:// and vmess:// node pools are not forward proxies.

    The table names the exit (``http://127.0.0.1:<mixed-port>``) and references
    the engine's own file; the alternative would be cleartext node credentials in
    the config users hand-edit.
    """
    table = load_table(
        config={
            "network": {
                "egress": {
                    "proxies": {"engine": {"url": "http://127.0.0.1:7895"}},
                    "rules": [{"match": "*", "via": "proxy:engine"}],
                    "engine": {
                        "enabled": True,
                        "mixed_port": _MIXED,
                        "include_files": ["D-here-is-not-hardcoded/egress/config.yaml"],
                    },
                }
            }
        }
    )
    doc = _render(table, mixed_port=_MIXED)
    assert doc["include"] == ["D-here-is-not-hardcoded/egress/config.yaml"]
    # the rendered config carries routing, not the pool
    assert doc["proxies"] == [
        {"name": "engine", "type": "http", "server": "127.0.0.1", "port": 7895}
    ]


@pytest.mark.parametrize("bad", ["some/path.yaml", {"a": 1}, 5])
def test_a_malformed_include_list_is_refused_at_load(bad):
    with pytest.raises(EgressConfigError, match="include_files"):
        load_table(
            config={
                "network": {
                    "egress": {
                        "proxies": {"e": {"url": "http://127.0.0.1:1"}},
                        "rules": [{"match": "*", "via": "proxy:e"}],
                        "engine": {"include_files": bad},
                    }
                }
            }
        )


def test_an_empty_entry_in_the_include_list_is_named_not_dropped():
    with pytest.raises(EgressConfigError, match="empty path"):
        load_table(
            config={
                "network": {
                    "egress": {
                        "proxies": {"e": {"url": "http://127.0.0.1:1"}},
                        "rules": [{"match": "*", "via": "proxy:e"}],
                        "engine": {"include_files": ["ok.yaml", "  "]},
                    }
                }
            }
        )

