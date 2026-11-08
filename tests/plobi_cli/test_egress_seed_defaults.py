"""First run: the import completes the table instead of handing back a table that cannot boot.

The failure this pins was measured end to end — a clean install, a pasted subscription, a
saved table, and an engine that refuses to start over three schema details the user has no
way to know: an empty ``rules`` (nothing to enforce), a missing ``engine.mixed_port`` (no
front door for the system proxy), and domain-addressed nodes without ``engine.dns`` (it
cannot resolve what it must dial).  "Import succeeded" plus "engine never came up" is the
most expensive shape of that window-paper bug, so the import seeds them and says so.
"""

from __future__ import annotations

from agent.egress import rules as egress
from agent.egress.mihomo_config import compile_config
from plobi_cli.egress_config import (
    DEFAULT_MIXED_PORT,
    DEFAULT_NAMESERVERS,
    seed_engine_defaults,
)

NODE = {
    "type": "ss",
    "server": "node.example.com",
    "port": 8388,
    "cipher": "aes-128-gcm",
    "password": "secret",
}


def test_a_halfway_table_gets_exactly_the_three_things_it_needs():
    block = {"nodes": {"东京-01": dict(NODE)}}

    seeded, notes = seed_engine_defaults(block)

    assert seeded["engine"]["mixed_port"] == DEFAULT_MIXED_PORT
    assert seeded["engine"]["dns"]["nameserver"] == list(DEFAULT_NAMESERVERS)
    assert seeded["rules"] == [{"match": "*", "via": "proxy:东京-01"}]
    # Everything filled is stated; a silent rewrite of the user's own file is the thing this
    # codebase has been burned by.
    assert len(notes) == 3
    assert all(note for note in notes)


def test_the_seeded_table_compiles_and_the_unseeded_one_does_not():
    """The point of the seed is a compiler that stops throwing, not a nicer dict."""
    block = {"nodes": {"东京-01": dict(NODE)}}

    try:
        compile_config(egress.parse_table(block))
    except egress.EgressConfigError:
        pass
    else:  # pragma: no cover - a compile of the raw block means the seed is unnecessary
        raise AssertionError("the raw table compiled; this test no longer proves anything")

    seeded, _ = seed_engine_defaults(block)
    rendered = compile_config(egress.parse_table(seeded))

    assert f"mixed-port: {DEFAULT_MIXED_PORT}" in rendered
    assert "MATCH,东京-01" in rendered
    assert "nameserver" in rendered


def test_a_complete_table_comes_back_untouched():
    """Never re-decide what someone already decided, including a non-default port."""
    block = {
        "engine": {
            "mixed_port": 7897,
            "dns": {"enable": True, "nameserver": ["192.168.1.1"]},
        },
        "nodes": {"东京-01": dict(NODE)},
        "rules": [{"match": "*.internal", "via": "direct"}, {"match": "*", "via": "proxy:东京-01"}],
    }

    seeded, notes = seed_engine_defaults(block)

    assert seeded == block
    assert notes == []


def test_an_empty_table_is_left_alone():
    """No exit chosen is not the same as an exit forgotten: do not invent one."""
    for block in ({}, {"nodes": {}, "groups": {}}, {"engine": {"mixed_port": 7890}}):
        seeded, notes = seed_engine_defaults(block)

        assert seeded == block
        assert notes == []


def test_the_catch_all_prefers_a_group_over_a_single_member():
    """A region group is the exit the pane means by 默认; one arbitrary node is not."""
    block = {
        "nodes": {"东京-01": dict(NODE), "东京-02": dict(NODE)},
        "groups": {"自动选择": {"type": "url-test", "proxies": ["东京-01", "东京-02"]}},
    }

    seeded, _ = seed_engine_defaults(block)

    assert seeded["rules"] == [{"match": "*", "via": "proxy:自动选择"}]


def test_existing_rules_are_not_touched_even_when_they_have_no_catch_all():
    """A precise table is a decision; the seed only fills what is missing."""
    block = {
        "nodes": {"东京-01": dict(NODE)},
        "rules": [{"match": "*.openai.com", "via": "proxy:东京-01"}],
    }

    seeded, notes = seed_engine_defaults(block)

    assert seeded["rules"] == block["rules"]
    assert not any("兜底" in note for note in notes)


def test_the_engine_port_is_seeded_before_the_catch_all_is_chosen():
    """Order matters: the front-door guard reads ``engine.mixed_port`` to know our own name."""
    block = {
        "nodes": {"东京-01": dict(NODE)},
        "proxies": {"前门": {"url": "http://127.0.0.1:7890"}},
    }

    seeded, _ = seed_engine_defaults(block)

    assert seeded["engine"]["mixed_port"] == DEFAULT_MIXED_PORT
    assert seeded["rules"][0]["via"] != "proxy:前门"


def test_a_table_that_already_points_traffic_at_the_engine_gets_the_permission():
    """设置页整块保存那一趟不带 ``engine.enabled``——界面里没这个字段。

    于是表里已经写着 ``system_proxy.enabled: true``，授权位还是模板默认 false，而
    snapshot() 先读授权位：开关亮了、引擎没起。只有表确实把流量交给引擎时才补，
    没表态的表保持关闭。
    """
    seeded, notes = seed_engine_defaults(
        {"nodes": {"东京-01": dict(NODE)}, "system_proxy": {"enabled": True}}
    )

    assert seeded["engine"]["enabled"] is True
    assert any("engine.enabled" in note for note in notes)

    quiet, quiet_notes = seed_engine_defaults({"nodes": {"东京-01": dict(NODE)}})

    assert quiet["engine"].get("enabled") is not True, "没人碰过的表不该被悄悄打开"
    assert not any("engine.enabled" in note for note in quiet_notes)
