"""Egress rule-table resolution: what the table promises and refuses (WP-EGRESS-ROUTING).

Three layers of truth are tested separately on purpose:

* the parser (``parse_table``) — refuses nonsense before any request is made
* the decision (``decide``) — which rule wins for a given host
* the shared entry point (``_get_proxy_for_base_url``) — that a declared table,
  not the inherited environment, answers once one exists
"""

from __future__ import annotations

import logging

import pytest
import yaml

from agent import process_bootstrap
from agent.egress import rules as egress
from agent.egress.rules import EgressConfigError, decide, load_table, parse_table

#: The exit the table names, deliberately different from the exit the
#: environment names — otherwise "the table won" and "the env won" are the
#: same string and the test proves nothing.
_TABLE_PROXY = "http://table-decides.test:8234"
_ENV_PROXY = "http://inherited-env.test:9"


def _egress(**overrides) -> dict:
    raw = {
        "proxies": {"local": {"url": _TABLE_PROXY}},
        "rules": [
            {"match": "api.github.com", "via": "direct"},
            {"match": "*.internal.test", "via": "proxy:local"},
            {"match": "*", "via": "proxy:local"},
        ],
    }
    raw.update(overrides)
    return raw


def _table(**egress_keys) -> egress.Table:
    return load_table(config={"network": {"egress": _egress(**egress_keys)}})


@pytest.fixture()
def table_installed():
    """Install a table as the process-wide answer, the way config would."""

    def _install(**egress_keys) -> egress.Table:
        table = _table(**egress_keys)
        egress._TABLE_CACHE = table
        return table

    yield _install
    egress._TABLE_CACHE = None


# --- no table means no opinion -----------------------------------------------


def test_an_empty_table_leaves_the_env_only_path_untouched(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", _ENV_PROXY)
    monkeypatch.setattr(egress, "_TABLE_CACHE", None)
    table = load_table(config={"network": {}})
    assert table.active is False
    assert decide(table, "anything.test") is None
    assert process_bootstrap._get_proxy_for_base_url("https://api.openai.com/v1") == _ENV_PROXY


def test_an_empty_table_still_honours_the_bypass_list(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", _ENV_PROXY)
    monkeypatch.setenv("NO_PROXY", "internal.test")
    monkeypatch.setattr(egress, "_TABLE_CACHE", None)
    assert process_bootstrap._get_proxy_for_base_url("https://a.internal.test/") is None
    assert process_bootstrap._get_proxy_for_base_url("https://api.openai.com/") == _ENV_PROXY


# --- a table wins over the inherited environment ----------------------------


def test_the_table_answers_even_when_the_environment_says_otherwise(
    monkeypatch, table_installed
):
    monkeypatch.setenv("HTTPS_PROXY", _ENV_PROXY)
    table_installed()
    assert process_bootstrap._get_proxy_for_base_url("https://api.github.com/user") is None
    assert process_bootstrap._get_proxy_for_base_url("https://random.test/v1") == _TABLE_PROXY


def test_a_catch_all_direct_rule_overrides_an_inherited_proxy(monkeypatch, table_installed):
    monkeypatch.setenv("HTTPS_PROXY", _ENV_PROXY)
    table_installed(rules=[{"match": "*", "via": "direct"}])
    assert process_bootstrap._get_proxy_for_base_url("https://api.openai.com/v1") is None


def test_a_caller_with_no_destination_still_gets_the_tables_answer(monkeypatch, table_installed):
    """``resolve_proxy``-style callers pass no URL at all; they must not fall
    back to the environment once a table exists."""
    monkeypatch.setenv("HTTPS_PROXY", _ENV_PROXY)
    table_installed()
    assert process_bootstrap._get_proxy_for_base_url(None) == _TABLE_PROXY

    table_installed(rules=[{"match": "*", "via": "direct"}])
    assert process_bootstrap._get_proxy_for_base_url(None) is None


@pytest.mark.parametrize(
    "host,winner",
    [
        ("x.internal.test", "*.internal.test"),
        ("deep.y.internal.test", "*.internal.test"),
        ("internal.test", "*.internal.test"),
        # the class of false positive base_url_hostname was written to kill:
        # a host that merely ends with the matcher's text is not inside it, so
        # it is answered by the catch-all, never by the suffix rule
        ("notinternal.test", "*"),
        ("internal.test.evil", "*"),
        ("api.github.com", "api.github.com"),
        ("api.github.com.evil", "*"),
    ],
)
def test_suffix_matching_covers_subdomains_and_no_lookalikes(host, winner):
    decision = decide(_table(), host)
    assert decision.rule.match == winner, f"{host} hit {decision.rule.match}"


def test_rule_order_is_the_precedence_there_is_no_specificity_score():
    """A host answered by an earlier wildcard never reaches the more specific
    rule below it — so the table is read top-down, exactly like the engine's."""
    table = _table(
        rules=[
            {"match": "a.internal.test", "via": "proxy:local"},
            {"match": "*.internal.test", "via": "direct"},
            {"match": "*", "via": "proxy:local"},
        ]
    )
    assert decide(table, "a.internal.test").proxy_url == _TABLE_PROXY
    assert decide(table, "a.internal.test").rule.match == "a.internal.test"
    assert decide(table, "b.internal.test").proxy_url is None
    # reorder the same two rules and the answer for a.internal.test flips
    flipped = _table(
        rules=[
            {"match": "*.internal.test", "via": "direct"},
            {"match": "a.internal.test", "via": "proxy:local"},
            {"match": "*", "via": "proxy:local"},
        ]
    )
    assert decide(flipped, "a.internal.test").proxy_url is None


def test_ip_literals_match_cidr_rules_and_domains_never_do():
    table = _table(
        rules=[
            {"match": "10.20.0.0/16", "via": "direct"},
            {"match": "*", "via": "proxy:local"},
        ]
    )
    assert decide(table, "10.20.30.40").proxy_url is None
    assert decide(table, "10.21.0.1").proxy_url == _TABLE_PROXY
    # a domain is never swallowed by a CIDR rule, however it reads
    assert decide(table, "10.20.0.0.evil.test").proxy_url == _TABLE_PROXY


def test_case_and_trailing_dot_are_normalized_before_matching():
    table = _table(
        rules=[{"match": "API.GitHub.com", "via": "direct"}, {"match": "*", "via": "proxy:local"}]
    )
    assert decide(table, "Api.GitHub.COM").proxy_url is None
    assert decide(table, "api.github.com.").proxy_url is None


# --- what it cannot do, said by name ----------------------------------------


def test_geosite_rules_are_deferred_and_say_so(caplog):
    with caplog.at_level(logging.WARNING, logger="agent.egress.rules"):
        table = _table(
            rules=[
                {"match": "geosite:github", "via": "direct"},
                {"match": "*", "via": "proxy:local"},
            ]
        )
    assert [r.match for r in table.engine_only_rules] == ["geosite:github"]
    # ... and traffic really does fall through to the catch-all
    assert decide(table, "github.com").proxy_url == _TABLE_PROXY
    assert "engine-only" in caplog.text
    assert "geosite:github" in caplog.text


def test_enabling_the_engine_silences_the_deferral_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="agent.egress.rules"):
        _table(
            rules=[
                {"match": "geosite:github", "via": "direct"},
                {"match": "*", "via": "proxy:local"},
            ],
            engine={"enabled": True, "mixed_port": 7899},
        )
    assert "engine-only" not in caplog.text


def test_a_pypi_mirror_is_refused_because_it_would_hijack_the_install_policy():
    with pytest.raises(EgressConfigError) as err:
        _table(mirrors={"pypi": "https://mirror.test/simple"})
    message = str(err.value)
    assert "PIP_INDEX_URL" in message and "lazy_deps" in message


def test_only_tools_that_honor_a_registry_variable_can_be_mirrored():
    with pytest.raises(EgressConfigError, match="not a supported mirror target"):
        _table(mirrors={"git": "https://mirror.test"})
    assert _table(mirrors={"npm": "https://registry.npmmirror.com"}).mirrors == {
        "npm": "https://registry.npmmirror.com"
    }


# --- malformed tables are refused at load, not at request time ---------------


def test_a_table_must_answer_for_everything_so_a_catch_all_is_required():
    with pytest.raises(EgressConfigError, match="catch-all"):
        _table(rules=[{"match": "a.test", "via": "direct"}])


def test_a_rule_naming_an_undeclared_proxy_is_named_in_the_error():
    with pytest.raises(EgressConfigError) as err:
        _table(rules=[{"match": "a.test", "via": "proxy:typo"}, {"match": "*", "via": "direct"}])
    assert "typo" in str(err.value)


@pytest.mark.parametrize(
    "entry,expect",
    [
        ({"match": "", "via": "direct"}, "match is required"),
        ({"match": "a.test"}, "via is required"),
        ({"match": "a.test", "via": "socks:1.2.3.4"}, "must be 'direct' or 'proxy:"),
        ({"match": "a.test", "via": "proxy:"}, "names no proxy"),
        ({"match": "*.com", "via": "direct"}, "needs a domain, not a TLD"),
        ({"match": "su*b.com", "via": "direct"}, "interior wildcards"),
        ({"match": "10.0.0.0/33", "via": "direct"}, "not a CIDR"),
        ({"match": "nodash", "via": "direct"}, "not a host"),
    ],
)
def test_each_malformed_shape_reports_its_own_key(entry, expect):
    with pytest.raises(EgressConfigError) as err:
        parse_table({"rules": [entry, {"match": "*", "via": "direct"}]})
    assert expect in str(err.value)
    assert "network.egress.rules[0]" in str(err.value)


def test_a_proxy_can_live_in_the_environment_and_the_table_still_resolves_it(monkeypatch):
    name = "PLOBI_TEST_EGRESS_PROXY_URL"
    monkeypatch.setenv(name, _TABLE_PROXY)
    table = load_table(
        config={
            "network": {
                "egress": {
                    "proxies": {"local": {"url_env": name}},
                    "rules": [{"match": "*", "via": "proxy:local"}],
                }
            }
        }
    )
    assert table.proxies["local"] == _TABLE_PROXY


def test_an_url_env_naming_an_unset_variable_fails_instead_of_running_routeless():
    with pytest.raises(EgressConfigError) as err:
        parse_table(
            {
                "proxies": {"local": {"url_env": "PLOBI_TEST_NO_SUCH_PROXY_VAR"}},
                "rules": [{"match": "*", "via": "proxy:local"}],
            }
        )
    assert "is not set" in str(err.value)


def test_a_proxy_declaration_cannot_be_both_inline_and_indirect():
    with pytest.raises(EgressConfigError, match="pick one"):
        parse_table(
            {
                "proxies": {"local": {"url": _TABLE_PROXY, "url_env": "OTHER"}},
                "rules": [{"match": "*", "via": "proxy:local"}],
            }
        )


def test_socks_aliases_are_normalized_before_a_client_ever_sees_them():
    table = parse_table(
        {
            "proxies": {"s": {"url": "socks://127.0.0.1:1080"}},
            "rules": [{"match": "*", "via": "proxy:s"}],
        }
    )
    assert table.proxies["s"].startswith("socks5://")


@pytest.mark.parametrize("scheme", ["ftp", "ss", "vless", "h"])
def test_an_unsupported_proxy_scheme_is_refused(scheme):
    with pytest.raises(EgressConfigError, match="scheme"):
        parse_table(
            {"proxies": {"p": {"url": f"{scheme}://h:1"}}, "rules": [{"match": "*", "via": "proxy:p"}]}
        )


# --- the real resolution chain, not a mocked one ----------------------------


def test_e2e_written_config_file_reaches_the_http_client(tmp_path, monkeypatch):
    home = tmp_path / "plobi-home"
    home.mkdir()
    (home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "network": {
                    "egress": {
                        "proxies": {"local": {"url": _TABLE_PROXY}},
                        "mirrors": {"npm": "https://registry.npmmirror.com"},
                        "rules": [
                            {"match": "api.anthropic.com", "via": "direct"},
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
    monkeypatch.setenv("HTTPS_PROXY", _ENV_PROXY)

    table = load_table(force_reload=True)
    assert table.active and table.mirrors["npm"] == "https://registry.npmmirror.com"
    assert process_bootstrap._get_proxy_for_base_url("https://api.anthropic.com/v1") is None
    assert process_bootstrap._get_proxy_for_base_url("https://api.openai.com/v1") == _TABLE_PROXY

    # The builder must ask the one resolver, with the URL it was handed.
    # httpx 0.28 exposes no readable proxy attribute, so the contract worth
    # pinning is that no second decision happens between table and client.
    asked: list[str] = []
    monkeypatch.setattr(
        process_bootstrap,
        "_get_proxy_for_base_url",
        lambda url: asked.append(url) or None,
    )
    client = process_bootstrap.build_keepalive_http_client(base_url="https://api.openai.com/v1")
    try:
        assert asked == ["https://api.openai.com/v1"]
    finally:
        client.close()
