"""搜索要走仓里那一条代理解析器（`WP-SEARCH-EXIT`）。

为什么要有这一档：11-10 实测这台机器上 DuckDuckGo 直连超时（`DuckDuckGo search timed out
after 30s`），而 `ddgs` 客户端**不读** `HTTPS_PROXY` 之外的出口表——它只认自己那个
`proxy=` 参数（以及 `DDGS_PROXY` 环境变量）。于是「表里明明接了本机出网引擎」与
「搜得出」之间缺一根线：这根线由本档补上。

钉的是**契约**，不是实现细节：
① provider 必须去问仓里**那一条**解析器（`agent.process_bootstrap._get_proxy_for_base_url`），
   不得自己再读一遍表（裁定 94：第二个窄读者就是「代理修好了却在某个调用方没生效」的开始）；
② 解析器答什么，就把什么交给客户端；
③ 没人答（表里没规则、环境里也没代理）时，交给客户端的是 `None`——行为与从前一致。
"""

from __future__ import annotations

import sys
import types

import pytest

from plugins.web.ddgs import provider as ddgs_provider


class _FakeDDGS:
    """记下客户端拿到了什么参数。"""

    seen: dict = {}

    def __init__(self, **kwargs):
        _FakeDDGS.seen = dict(kwargs)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def text(self, query, max_results=5):
        return [
            {"title": "标题", "href": "https://example.com/a", "body": "摘要"},
            {"title": "标题二", "href": "https://example.com/b", "body": "摘要二"},
        ]


@pytest.fixture()
def fake_ddgs(monkeypatch):
    fake = types.SimpleNamespace(DDGS=_FakeDDGS)
    monkeypatch.setitem(sys.modules, "ddgs", fake)
    return fake


def _answer(monkeypatch, value):
    """让仓里那一条解析器答某个出口。"""
    import agent.process_bootstrap as bootstrap

    monkeypatch.setattr(bootstrap, "_get_proxy_for_base_url", lambda base_url=None: value)


def test_the_client_gets_the_exit_the_resolver_answered(monkeypatch, fake_ddgs):
    _answer(monkeypatch, "http://127.0.0.1:7895")

    hits = ddgs_provider._run_ddgs_search("plobi", 2)

    assert _FakeDDGS.seen["proxy"] == "http://127.0.0.1:7895"
    assert hits[0]["url"] == "https://example.com/a"
    assert hits[0]["position"] == 1


def test_nobody_answered_means_none_and_nothing_else_changes(monkeypatch, fake_ddgs):
    _answer(monkeypatch, None)

    ddgs_provider._run_ddgs_search("plobi", 1)

    assert _FakeDDGS.seen["proxy"] is None
    # 超时参数照旧，不许因为多了 proxy 就改了别的
    assert _FakeDDGS.seen["timeout"] == 10


def test_the_provider_asks_the_one_resolver_not_the_table(monkeypatch, fake_ddgs):
    """它问的是**那一条**解析器；自己再去读一遍表就是裁定 94 说的那种「第二个窄读者」。"""
    asked = []

    import agent.process_bootstrap as bootstrap

    def _spy(base_url=None):
        asked.append(base_url)
        return "http://127.0.0.1:7895"

    monkeypatch.setattr(bootstrap, "_get_proxy_for_base_url", _spy)

    ddgs_provider._run_ddgs_search("plobi", 1)

    assert asked == [None], "问的是「没有指定目的地时该给哪条出口」，不是某个域名"
