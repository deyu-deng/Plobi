"""P1：卡片清单只认他们的名册，登录入口按他们报的每一条通道生成。

两件事都要能证伪：

1. 一台**什么都没装**的机器也得看见全套卡片 —— 随包名册是第三真相源，
   少了它卡片清单就退回「本机装了谁」（用户 11-24 看到的正是 9 家而非 22 家）；
2. 一个插件两条通道（浏览器 / 贴 key）就得给两条入口 —— 只给一条等于替用户
   砍掉它一半的能力，而 `oauth` 与 `api` 的差别不只是文案：一条不要终端，
   一条必须有人敲键。
"""

from __future__ import annotations

import asyncio
import json

import httpx

from bezel.providers.apps.magpie import MagpieProvider, MagpieProviderLeg
from bezel.providers.apps.magpie import api as magpie_api


def _http() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(503, json={}))
    )


def _provider(http: httpx.AsyncClient) -> MagpieProvider:
    return MagpieProvider({"timeout": 30}, http)


def test_the_card_list_comes_from_the_plugin_set_we_ship(tmp_path, monkeypatch):
    """随包名册 ∪ 本机名册 = 卡片清单；随包才有的那几家标 from_set。"""
    shipped = tmp_path / "magpie-plugins"
    shipped.mkdir(parents=True)
    (shipped / "plugin-providers.json").write_text(
        json.dumps(
            [
                {
                    "id": "qoder",
                    "name": "Qoder",
                    "spec": "@magpie-community/opencode-qoder-auth@latest",
                    "methods": [{"type": "oauth", "label": "Sign in with Qoder"}],
                },
                {
                    "id": "devin",
                    "name": "Devin",
                    "spec": "@magpie-community/opencode-devin-auth@0.1.37",
                    "methods": [
                        {"type": "oauth", "label": "Devin (browser)"},
                        {"type": "api", "label": "Devin API key"},
                    ],
                },
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(magpie_api.PACKAGED_SET_ENV, str(shipped))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "nothing-here"))

    prov = _provider(_http())
    prov._their_providers = lambda serving=None: {
        "workbuddy": {"id": "workbuddy", "name": "WorkBuddy"}
    }
    specs = asyncio.run(prov.provider_specs())

    by_id = {spec["id"]: spec for spec in specs}
    assert set(by_id) == {"workbuddy", "qoder", "devin"}, "并集才是清单，随包那几家不能被吞"
    assert by_id["devin"]["from_set"] is True
    assert by_id["workbuddy"]["from_set"] is False
    assert [m["label"] for m in by_id["devin"]["methods"]] == [
        "Devin (browser)",
        "Devin API key",
    ], "他们报的每条通道都要原样带到卡上"


def test_every_lane_they_offer_is_a_way_in(tmp_path, monkeypatch):
    """一条 oauth + 一条 api ⇒ 两条入口，各自的 argv 与终端需求都不一样。"""
    monkeypatch.setenv(magpie_api.PACKAGED_SET_ENV, magpie_api.PACKAGED_SET_OFF)
    http = _http()
    prov = _provider(http)
    leg = MagpieProviderLeg(
        {"base_url": "http://127.0.0.1:3425"},
        http,
        {
            "id": "devin",
            "upstream": "devin",
            "name": "Devin",
            "login": "oauth",
            "methods": [
                {"type": "oauth", "label": "Devin (browser)"},
                {"type": "api", "label": "Devin API key", "placeholder": "ak_…"},
            ],
        },
        umbrella=prov,
    )

    card = leg.card_spec()
    assert [m["label"] for m in card["methods"]] == ["Devin (browser)", "Devin API key"]

    default = leg.login_plan()
    assert default["argv"] == ["plugin", "login", "devin", "Devin (browser)"]
    assert default["tty"] is False, "浏览器那条不需要终端"

    key = leg.login_plan("Devin API key")
    assert key["argv"] == ["plugin", "login", "devin", "Devin API key"]
    assert key["tty"] is True, "贴 key 那条要有人敲键"

    assert leg.login_plan("api")["argv"][-1] == "Devin API key", "按类型词也得能选中那条"


def test_an_unknown_lane_falls_back_instead_of_naming_it(tmp_path, monkeypatch):
    """用户给了一条我们不认识的名字：退回默认通道，不把乱码丢给他们的 CLI。"""
    monkeypatch.setenv(magpie_api.PACKAGED_SET_ENV, magpie_api.PACKAGED_SET_OFF)
    http = _http()
    prov = _provider(http)
    leg = MagpieProviderLeg(
        {"base_url": "http://127.0.0.1:3425"},
        http,
        {
            "id": "devin",
            "upstream": "devin",
            "methods": [
                {"type": "oauth", "label": "Devin (browser)"},
                {"type": "api", "label": "Devin API key"},
            ],
        },
        umbrella=prov,
    )
    plan = leg.login_plan("不存在的通道")
    assert plan["argv"] == ["plugin", "login", "devin", "Devin (browser)"]


def test_a_leg_without_a_plugin_has_nothing_to_install(tmp_path, monkeypatch):
    """没有插件的腿：`ensure_plugin` 不该当成失败，页面照常往下走登录。"""
    monkeypatch.setenv(magpie_api.PACKAGED_SET_ENV, magpie_api.PACKAGED_SET_OFF)
    http = _http()
    prov = _provider(http)
    leg = MagpieProviderLeg(
        {"base_url": "http://127.0.0.1:3425"},
        http,
        {"id": "qoder", "upstream": "qoder"},
        umbrella=prov,
    )
    assert asyncio.run(leg.ensure_plugin()) == (True, "")
