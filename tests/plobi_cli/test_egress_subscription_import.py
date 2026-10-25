"""订阅进表：从订阅到表，从本机客户端的缓存到表 (WP-EGRESS-ROUTING)。

这一格钉住四件事，每件都是这台机器上真实发生过的事：

* **订阅没有组，表里也必须有组可指**。ikuuu 那份缓存实测 46 个成员、**0 个组**（组是它的插件
  在运行时生成的）。成员进表而组没有 ⇒ 兜底规则只能指一个节点，那不叫 VPN。所以导入要自己
  按名字里的地方建 select 组，再建一个 url-test 的 POOL。
* **凭据不进表**。表里只有 `*_env` 名字，值落根 `.env`；返回值里一个值都不许有。
* **同名以进来的为准，不同名的保留**：更新订阅不该把用户自己加的节点抹掉。
* **兜底规则指着引擎自己的前门，要被点名**：引擎不能把流量交给它自己的接收入口（编译器拒绝
  那种配置），所以导入要么改它，要么说清楚。

也钉住那条形状：答案里 `nodes`/`groups` 是**映射**（表自己的形状，面板自己编辑的形状）。
早先返回的是 `[{name, ...}, ...]` 这样的列表，面板按名字取键，取到的是 "0"、"1"、"2"。
"""

from __future__ import annotations

import json

import pytest

from plobi_cli import egress_config

#: 照 ikuuu 缓存的实测形状抄的：46 个成员、0 个组、名字带国旗与地名。
IKUUU_LIKE = """\
proxies:
  - name: '🇭🇰 香港W01'
    type: ss
    server: hk01.entry.example
    port: 19272
    cipher: aes-256-gcm
    password: 37414d03-3c26-388c-ab35-e34982f33abd
    udp: true
  - name: '🇺🇲 美国W01 | IEPL'
    type: ss
    server: us01.entry.example
    port: 20010
    cipher: aes-256-gcm
    password: 37414d03-3c26-388c-ab35-e34982f33abd
    udp: true
  - name: '乌克兰W01'
    type: vmess
    server: ua01.entry.example
    port: 20070
    uuid: 11111111-2222-3333-4444-555555555555
    alterId: '0'
    network: ws
  - name: '🇯🇵 日本W01 | IEPL'
    type: ss
    server: jp01.entry.example
    port: 473
    cipher: aes-256-gcm
    password: 37414d03-3c26-388c-ab35-e34982f33abd
    udp: true
"""


@pytest.fixture
def env_writer(monkeypatch):
    """Capture what would go to `.env` instead of touching this machine's real secrets file."""
    written: dict[str, str] = {}

    import plobi_cli.config as cfg

    monkeypatch.setattr(cfg, "save_env_value_secure", lambda k, v: written.__setitem__(k, v))
    return written


@pytest.fixture
def table(monkeypatch):
    """A table on disk that only exists inside this test."""
    state: dict = {"network": {}}

    monkeypatch.setattr(egress_config, "read_egress", lambda: {"network": state["network"]})

    def _replace(network_block):
        state["network"] = network_block
        return True, []

    monkeypatch.setattr(egress_config, "replace_egress", _replace)
    return state


def test_a_subscription_without_groups_still_leaves_the_table_something_a_rule_can_point_at(env_writer):
    answer = egress_config.read_subscription(text=IKUUU_LIKE, write_secrets=True)

    assert answer["ok"] is True, answer["notes"]
    assert len(answer["nodes"]) == 4, answer["nodes"]
    assert answer["groups"]["POOL"]["type"] == "url-test", answer["groups"]
    assert len(answer["groups"]["POOL"]["include"]) == 4, answer["groups"]["POOL"]
    # 名字里的地方各一个 select 组；🇺🇲 是美属维尔京群岛，所以美国是按"美国"这两个字认的。
    assert answer["groups"]["香港"]["include"] == ["🇭🇰 香港W01"], answer["groups"]
    assert answer["groups"]["美国"]["include"] == ["🇺🇲 美国W01 | IEPL"], answer["groups"]
    assert answer["groups"]["乌克兰"]["include"] == ["乌克兰W01"], answer["groups"]

    wire = json.dumps(answer, ensure_ascii=False)

    assert "37414d03-3c26-388c-ab35-e34982f33abd" not in wire, "凭据值不许出现在返回值里"
    assert "11111111-2222-3333-4444-555555555555" not in wire
    assert answer["nodes"]["🇭🇰 香港W01"]["password_env"], answer["nodes"]
    assert sorted(env_writer) == sorted(answer["secret_env_names"]), (env_writer, answer)


def test_the_table_keeps_the_node_the_user_added_by_hand(env_writer, table):
    table["network"] = {
        "egress": {
            "nodes": {"我自己加的": {"type": "ss", "server": "mine.example", "port": 1, "cipher": "aes-256-gcm"}},
            "groups": {"我自己加的组": {"type": "select", "include": ["我自己加的"]}},
            "rules": [{"match": "*", "via": "direct"}],
        }
    }

    answer = egress_config.import_subscription(text=IKUUU_LIKE)

    assert answer["wrote"] is True, answer["notes"]
    assert "我自己加的" in answer["nodes"], answer["nodes"]
    assert "我自己加的组" in answer["groups"], answer["groups"]
    assert answer["added_nodes"] == sorted(
        ["🇭🇰 香港W01", "🇺🇲 美国W01 | IEPL", "乌克兰W01", "🇯🇵 日本W01 | IEPL"]
    ) or set(answer["added_nodes"]) == {
        "🇭🇰 香港W01", "🇺🇲 美国W01 | IEPL", "乌克兰W01", "🇯🇵 日本W01 | IEPL"
    }, answer["added_nodes"]
    # 表里仍然只有 *_env 名字。
    assert "37414d03-3c26-388c-ab35-e34982f33abd" not in json.dumps(
        table["network"], ensure_ascii=False
    )


def test_the_catch_all_that_points_at_the_engines_own_door_is_called_out(env_writer, table):
    table["network"] = {
        "egress": {
            "proxies": {"gw": {"url": "http://127.0.0.1:7895"}},
            "engine": {"enabled": True, "mixed_port": 7895},
            "rules": [{"match": "geosite:cn", "via": "direct"}, {"match": "*", "via": "proxy:gw"}],
        }
    }

    said = egress_config.import_subscription(text=IKUUU_LIKE)

    assert any("前门" in note for note in said["notes"]), said["notes"]
    assert table["network"]["egress"]["rules"][-1]["via"] == "proxy:gw", "没让用户点名的规则不许动"

    moved = egress_config.import_subscription(text=IKUUU_LIKE, catch_all="POOL")

    assert moved["wrote"] is True, moved["notes"]
    assert table["network"]["egress"]["rules"][-1] == {"match": "*", "via": "proxy:POOL"}
    assert table["network"]["egress"]["rules"][0]["via"] == "direct", "只有最后一条是兜底"


def test_a_dry_run_writes_nothing_at_all(env_writer, table):
    table["network"] = {"egress": {"rules": [{"match": "*", "via": "direct"}]}}

    answer = egress_config.import_subscription(text=IKUUU_LIKE, apply=False)

    assert answer["wrote"] is False, answer
    assert env_writer == {}, "预演不许写凭据"
    assert table["network"]["egress"] == {"rules": [{"match": "*", "via": "direct"}]}


def test_the_answer_is_keyed_by_name_so_the_pane_can_use_it(env_writer):
    answer = egress_config.read_subscription(text=IKUUU_LIKE, write_secrets=False)

    assert isinstance(answer["nodes"], dict), type(answer["nodes"])
    assert isinstance(answer["groups"], dict), type(answer["groups"])
    assert "🇭🇰 香港W01" in answer["nodes"], answer["nodes"]
    # 面板按名字取键：列表形状会取到 "0"、"1"、"2"。
    assert all(isinstance(key, str) for key in answer["nodes"]), answer["nodes"]


def test_the_pane_endpoint_answers_in_the_table_shape(env_writer):
    import asyncio

    from plobi_cli.web_server import NetworkSubscriptionImport, post_network_import_subscription

    answer = asyncio.run(
        post_network_import_subscription(NetworkSubscriptionImport(text=IKUUU_LIKE))
    )

    assert answer["ok"] is True, answer["notes"]
    assert set(answer["nodes"]) == {
        "🇭🇰 香港W01", "🇺🇲 美国W01 | IEPL", "乌克兰W01", "🇯🇵 日本W01 | IEPL"
    }, answer["nodes"]
    assert answer["groups"]["POOL"]["include"], answer["groups"]

    refused = asyncio.run(
        post_network_import_subscription(NetworkSubscriptionImport(text="这不是订阅，是一段中文"))
    )

    assert refused["ok"] is False and refused["nodes"] == {}, refused
    assert refused["notes"] and "认不出来" in refused["notes"][0], refused


def test_a_local_client_that_is_not_there_says_one_sentence():
    from agent.egress import local_clients

    with pytest.raises(local_clients.LocalClientError) as unknown:
        local_clients.read_cache("不存在的客户端")

    assert "不认识的客户端" in str(unknown.value), str(unknown.value)

    assert "ikuuu" in local_clients.clients(), local_clients.clients()
    assert any("ikuuu" in line for line in local_clients.describe()), local_clients.describe()
