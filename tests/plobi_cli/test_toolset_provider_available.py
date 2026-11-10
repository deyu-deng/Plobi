"""The Tools screen must not call a backend "Ready" when it can't run.

11-10 实测：Tools 屏的 DuckDuckGo / xAI / Nous 三行都写着「就绪」，可 Agent
手上没有 ``web_search`` —— 凭据闸（``check_fn``）没过，工具压根没进工具表。
那三行之所以写着就绪，是因为屏上只看了「这一行要不要 API 密钥」，而
「不要密钥」不等于「能用」：DuckDuckGo 要的是 ``ddgs`` 包，Nous 要的是登录，
xAI 要的是凭据。

这组测试钉住判据：provider 行的 ``available`` 由**真探针**说了算。
"""

from __future__ import annotations

from plobi_cli import tools_config


def _row(name, backend=None, env_vars=None, **extra):
    row = {
        "name": name,
        "web_backend": backend,
        "env_vars": env_vars or [],
    }
    row.update(extra)
    return row


def test_keyless_row_is_not_ready_when_its_backend_cannot_run(monkeypatch):
    """DuckDuckGo 那一行不需要 API 密钥，但它要 ddgs 包。

    回归护栏：屏上把「这一行不要密钥」当成「就绪」，于是 Agent 手上没有
    web_search，而屏上三行全绿。
    """
    monkeypatch.setattr(
        "tools.web_tools._is_backend_available", lambda backend: False
    )

    assert (
        tools_config._toolset_provider_available(
            "web", _row("DuckDuckGo (ddgs)", backend="ddgs"), {}
        )
        is False
    )


def test_keyless_row_is_ready_when_its_backend_can_run(monkeypatch):
    """同一个 provider，包装上了就是就绪。"""
    monkeypatch.setattr(
        "tools.web_tools._is_backend_available", lambda backend: True
    )

    assert (
        tools_config._toolset_provider_available(
            "web", _row("DuckDuckGo (ddgs)", backend="ddgs"), {}
        )
        is True
    )


def test_keyed_row_follows_its_own_probe(monkeypatch):
    """有 key 的 provider 也一样：探针说了算，不看钥匙那一列。"""
    monkeypatch.setattr(
        "tools.web_tools._is_backend_available", lambda backend: False
    )
    monkeypatch.setattr(
        "plobi_cli.tools_config.get_env_value", lambda key: "sk-test"
    )

    assert (
        tools_config._toolset_provider_available(
            "web",
            _row("Exa", backend="exa", env_vars=[{"key": "EXA_API_KEY"}]),
            {},
        )
        is False
    )


def test_row_without_a_probe_keeps_the_old_answer(monkeypatch):
    """没有探针的行（别的工具集那些）仍旧按钥匙那一列回答。"""
    monkeypatch.setattr(
        "plobi_cli.tools_config.get_env_value", lambda key: ""
    )

    # 无钥匙 ⇒ 老口径就是 True（没有可失败的东西）。
    assert (
        tools_config._toolset_provider_available(
            "tts", _row("Microsoft Edge TTS"), {}
        )
        is True
    )
    # 有钥匙但没填 ⇒ False。
    assert (
        tools_config._toolset_provider_available(
            "tts",
            _row("ElevenLabs", env_vars=[{"key": "ELEVENLABS_API_KEY"}]),
            {},
        )
        is False
    )


def test_nous_managed_row_follows_the_subscription(monkeypatch):
    """Nous 那一行看的是订阅里有没有这一项，不是看有没有 key。"""

    class _Feature:
        available = False
        managed_by_nous = True

    class _Features:
        features = {"web": _Feature()}

    monkeypatch.setattr(
        "plobi_cli.tools_config.get_nous_subscription_features",
        lambda config, force_fresh=False: _Features(),
    )

    assert (
        tools_config._toolset_provider_available(
            "web",
            _row("Nous Subscription", backend="firecrawl", managed_nous_feature="web"),
            {},
        )
        is True
    )
