"""Importing someone's existing pool from the settings pane (WP-EGRESS-ROUTING, 口径 32).

The pane is allowed to be the only way in — a machine that was installed should not have to be
configured from a command line. That makes two things worth pinning:

* the import answer carries **names, never values**: ``password_env`` / ``uuid_env`` go into the
  table, the cleartext stays in the pool file and ``.env``. Echoing credentials through a local
  HTTP route is the exact shape the ACL work (纸 §23) exists to keep out.
* the parser's group check still bites: a group that names a member which is not under
  ``network.egress.nodes`` is refused with the member named, and that refusal has to reach the
  pane as words (otherwise the import looks like it worked).
"""

from __future__ import annotations

import asyncio
import json

import pytest

from agent.egress import rules as egress
from plobi_cli.egress_config import replace_egress, validate_egress

POOL = """\
mixed-port: 7890
proxies:
  - name: "n01 hk"
    type: ss
    server: 203.0.113.11
    port: 8388
    cipher: aes-256-gcm
    password: "s3cr3t-LEAK"
  - name: "n02 jp"
    type: vmess
    server: 203.0.113.12
    port: 443
    uuid: "11111111-2222-3333-4444-555555555555-LEAK"
proxy-groups:
  - name: POOL
    type: url-test
    proxies: ["n01 hk", "n02 jp"]
"""


@pytest.fixture(autouse=True)
def fresh_table(monkeypatch):
    monkeypatch.setattr(egress, "_TABLE_CACHE", None)
    yield
    monkeypatch.setattr(egress, "_TABLE_CACHE", None)


def _import(path):
    from plobi_cli.web_server import NetworkPoolImport, post_network_import_pool

    return asyncio.run(post_network_import_pool(NetworkPoolImport(path=path)))


def test_the_import_lands_as_names_only_and_a_bad_path_is_an_answer_not_a_crash(tmp_path):
    pool = tmp_path / "config.yaml"
    pool.write_text(POOL, encoding="utf-8")

    answer = _import(str(pool))

    assert answer["ok"] is True, answer["notes"]
    assert answer["egress"]["groups"]["POOL"]["include"] == ["n01 hk", "n02 jp"], answer["egress"]
    wire = json.dumps(answer, ensure_ascii=False)

    assert "s3cr3t-LEAK" not in wire and "555555555555-LEAK" not in wire, "凭据值不许从这条路出去"
    assert any(name.endswith("_PASSWORD") or name.endswith("_UUID") for name in answer["env_names"]), answer["env_names"]

    missing = _import(str(tmp_path / "nope.yaml"))

    assert missing["ok"] is False and missing["notes"], "读不到要说人话，不能给一个 200 空壳"


def test_a_group_naming_an_unknown_member_is_refused_with_that_member_named():
    """导入之后的保存仍走那颗闸：组引用不存在的成员 ⇒ 拒，且拒因原样交给面板显示。"""
    # `validate_egress` / `replace_egress` take the whole **network** block — handing them the
    # inner `egress` dict makes them validate `None`, which "passes" and is exactly how a bad
    # membership could sneak onto disk. Written the way the PUT route writes it.
    block = {"egress": {
        "nodes": {"n01 hk": {"cipher": "aes-256-gcm", "password_env": "PLOBI_EGRESS_N01_HK_PASSWORD", "port": 8388, "server": "203.0.113.11", "type": "ss"}},
        "groups": {"POOL": {"include": ["n01 hk", "ghost node"], "type": "url-test"}},
        "rules": [{"match": "*", "via": "POOL"}],
        "engine": {"enabled": True, "mixed_port": 7895},
    }}
    ok, messages = validate_egress(block)

    assert ok is False, messages
    assert any("ghost node" in line for line in messages), messages

    written, refusals = replace_egress(block)

    assert written is False, "拒因没回到调用方：面板会以为导入成功了"
    assert any("ghost" in line for line in refusals), refusals
