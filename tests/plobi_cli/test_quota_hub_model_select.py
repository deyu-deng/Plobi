"""订阅额度那条腿的模型，得真的选得上 —— 以及没选时后端自己挑一条。

现场（用户 11-10 18:37「我在这个模型选择框里面死活选不上。magpie的模型是怎么回事呢？」）：
后端给模型框的那份 payload 里，magpie 那 29 个 id 一个不少地挂在 `aigw` 这一行上，
渲染层也照着网关的判决把它们克隆成每条腿一组（用户看得到 OPENCODE-ZEN-FREE 那一组）。
点了却切不过去，红的是一句 **"Unknown provider 'aigw'"**。

根因不在屏上，在后端有**两份 provider 解析器**：

* ``plobi_cli/auth.py`` 读**插件注册表**（``providers/``，每个
  ``plugins/model-providers/*`` 调 ``register_provider``）——``aigw`` 在那里；
* ``plobi_cli/providers.py`` 读 **models.dev ＋ PLOBI_OVERLAYS**——``aigw`` 不在。

于是同一个名字，一半认得一半不认得：凭据那半认得（所以网关能路由），切换那半
不认得（所以每一刀都死在 ``resolve_provider_full`` �� — 模型框里的行带着网关的
slug，点下去却被回一句「没这个 provider」。

本档钉三件事：

1. **插件注册表是第三个来源**（不是第二个、不是取代前两个）：models.dev 与
   overlay 先答，插件注册表兜底；已经答得出的 provider 答案一个字不变。
2. **配置层自己的名字不被插件模板抢走**：``custom`` / ``local`` 归
   ``providers:``／``custom_providers:``，插件里那个 ``custom`` 是没有端点、没有
   钥匙的模板，替它答就是遮住用户自己配的端点。
3. **默认模型空时后端自己挑**：用户 11-10 拍板「存着的默认模型清空，让后端自己
   挑一条网关那批可选 id 里的第一条」。挑的是**可选**，不是**在等**——等恢复的
   那条是标灰的，拿它当默认等于开局就红。

零网络、零额度：凭据解析与模型校验两处接缝换成假应答，网关那一读
（``plobi_cli.models.hub_selectable_model_ids``）也换成假应答。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from plobi_cli.inventory import (
    QUOTA_HUB_SLUG,
    ConfigContext,
    build_models_payload,
)
from plobi_cli.model_switch import switch_model
from plobi_cli.providers import (
    PLOBI_OVERLAYS,
    get_provider,
    normalize_provider,
    resolve_provider_full,
)

#: 网关答的那批可选 id（真机上是 magpie 那 29 个，这里只取两条）。
HUB_SELECTABLE = ["opencode-zen-free/big-pickle", "workbuddy/glm-5.3"]

#: 额度冷却中的那条腿：不在**可选**清单里，只是「等一会儿」。
HUB_WAITING = {
    "workbuddy/glm-5.3": {
        "reason": "quota_exhausted",
        "retry_after_seconds": 1754,
        "available_at": "2026-11-10T23:13:02",
    }
}


# --------------------------------------------------------------------------- #
# 1. 插件注册表：第三个来源
# --------------------------------------------------------------------------- #


def test_the_hub_resolves_through_the_plugin_registry():
    """`aigw` 只由插件注册 ⇒ 这一格必须答得出它的端点与钥匙名。

    端点与钥匙名都从插件那份 profile 读，**不在这里再抄一遍**——抄一遍就有两处
    真源，改一处就漂一处。
    """
    pdef = resolve_provider_full(QUOTA_HUB_SLUG, {}, [])

    assert pdef is not None, "网关那条腿解析不出来 ⇒ --provider aigw 必红"
    assert pdef.id == QUOTA_HUB_SLUG
    assert pdef.base_url.startswith("http://127.0.0.1:"), pdef.base_url
    assert pdef.api_key_env_vars, "钥匙名得从插件那份 profile 来"
    assert pdef.transport == "openai_chat"
    assert pdef.source == "plugin"


def test_the_hub_is_not_an_aggregator():
    """网关这一行不许被当成路由聚合器。

    聚合器会被选择框的「同名去重」扫掉与自己重名的模型——对网关来说，那等于把
    订阅腿的清单掏空。
    """
    pdef = get_provider(QUOTA_HUB_SLUG)

    assert pdef is not None
    assert pdef.is_aggregator is False


def test_a_provider_that_only_a_plugin_registers_resolves():
    """不止网关这一家：凡是只有插件认得的名字，这一份解析器也得认得。"""
    pdef = get_provider("clinepass")

    assert pdef is not None, "插件认得的名字，这一份解析器也得认得"
    assert pdef.source == "plugin"
    assert pdef.base_url.startswith("https://"), pdef.base_url


def test_models_dev_and_overlays_still_win():
    """models.dev 与 overlay 先答 —— 插件注册表是兜底，不是取代。"""
    assert "openrouter" in PLOBI_OVERLAYS

    pdef = get_provider("openrouter")

    assert pdef is not None
    assert pdef.source in {"models.dev", "plobi"}, pdef.source
    assert pdef.is_aggregator is True


def test_the_config_layer_keeps_its_own_names():
    """`custom` / `local` 归配置层：插件里那个 `custom` 是模板，不许替它答。

    插件那份 `custom` 没有端点也没有钥匙；替它答，就是遮住用户自己在
    `providers:` / `custom_providers:` 里写的端点（那两段解析在插件兜底**之后**）。
    """
    assert get_provider("custom") is None
    assert get_provider("local") is None


def test_an_alias_still_resolves_to_the_same_place():
    """别名那一步不许被插件兜底绕过。"""
    assert normalize_provider("opencode-zen") == "opencode"
    assert get_provider("opencode-zen") is not None


# --------------------------------------------------------------------------- #
# 2. 切换那条路：点得动
# --------------------------------------------------------------------------- #


def test_switching_to_a_hub_model_succeeds(monkeypatch):
    """点一个网关的模型 ⇒ 切得过去，不再是「Unknown provider 'aigw'」。"""
    _stub_the_network(monkeypatch)

    result = switch_model(
        HUB_SELECTABLE[0],
        current_provider=QUOTA_HUB_SLUG,
        current_model="",
        explicit_provider=QUOTA_HUB_SLUG,
    )

    assert result.success is True, result.error_message
    assert result.new_model == HUB_SELECTABLE[0]
    assert result.target_provider == QUOTA_HUB_SLUG


def test_the_hub_model_id_survives_the_switch(monkeypatch):
    """`腿/模型` 这个写法要原样活下来——它就是网关清单里的 id。

    网关那批 id 是带斜杠的（`opencode-zen-free/big-pickle`）。改写它就等于
    点了一个网关不认的模型。
    """
    _stub_the_network(monkeypatch)

    result = switch_model(
        HUB_SELECTABLE[0],
        current_provider=QUOTA_HUB_SLUG,
        current_model="",
        explicit_provider=QUOTA_HUB_SLUG,
    )

    assert result.success is True, result.error_message
    assert "/" in result.new_model
    assert result.new_model.count("/") == 1


# --------------------------------------------------------------------------- #
# 3. 默认模型空时：后端自己挑
# --------------------------------------------------------------------------- #


def _hub_answers(monkeypatch):
    """网关在答：接缝换成假应答，钥匙与端点钉死（不读本机 env）。"""
    monkeypatch.setattr(
        "plobi.agents.registry.aigw_base_url", lambda: "http://127.0.0.1:8000/v1"
    )
    monkeypatch.setattr("plobi.agents.registry.aigw_api_key", lambda: "sk-test")
    monkeypatch.setattr(
        "plobi_cli.models.hub_selectable_model_ids",
        lambda api_key, base_url, **kwargs: list(HUB_SELECTABLE),
    )
    monkeypatch.setattr(
        "plobi_cli.models.hub_waiting_models",
        lambda api_key, base_url, **kwargs: dict(HUB_WAITING),
    )


def _ctx(provider: str = QUOTA_HUB_SLUG, model: str = "") -> ConfigContext:
    return ConfigContext(
        current_provider=provider,
        current_model=model,
        current_base_url="",
        user_providers={},
        custom_providers=[],
    )


def test_the_backend_picks_a_model_when_the_config_expresses_none(monkeypatch):
    """默认模型清空 ⇒ 后端自己挑一条网关那批可选 id 里的第一条。"""
    _hub_answers(monkeypatch)

    with patch("plobi_cli.model_switch.list_authenticated_providers", return_value=[]):
        payload = build_models_payload(_ctx(), explicit_only=True)

    assert payload["model"] == HUB_SELECTABLE[0]
    assert payload["provider"] == QUOTA_HUB_SLUG


def test_the_picked_model_is_one_the_hub_offers(monkeypatch):
    """挑出来的那条必须是网关**可选**清单里的一条 —— 不许挑一条在等的。"""
    _hub_answers(monkeypatch)

    with patch("plobi_cli.model_switch.list_authenticated_providers", return_value=[]):
        payload = build_models_payload(_ctx(), explicit_only=True)

    assert payload["model"] in HUB_SELECTABLE
    assert payload["model"] not in HUB_WAITING


def test_a_configured_model_is_never_overridden(monkeypatch):
    """配置里写着模型 ⇒ 原样，谁也不许动它。"""
    _hub_answers(monkeypatch)

    with patch("plobi_cli.model_switch.list_authenticated_providers", return_value=[]):
        payload = build_models_payload(_ctx(model="workbuddy/glm-5.3"), explicit_only=True)

    assert payload["model"] == "workbuddy/glm-5.3"


def test_the_pick_happens_even_when_the_catalogue_already_carries_the_hub(monkeypatch):
    """现实里网关那一行是 provider 清单自己产出来的 —— 挑选不许因此落空。

    那一行带的是**完整**清单（连不可选的那些也在），所以「挑」不能靠数那一行
    有几个模型，得问网关自己「哪些可选」。
    """
    _hub_answers(monkeypatch)

    listed = {
        "slug": QUOTA_HUB_SLUG,
        "name": "订阅额度",
        "is_current": True,
        "is_user_defined": False,
        "models": list(HUB_SELECTABLE) + ["some/leg-the-hub-refuses"],
        "total_models": 3,
        "source": "built-in",
        "authenticated": True,
    }
    with patch("plobi_cli.model_switch.list_authenticated_providers", return_value=[listed]):
        payload = build_models_payload(_ctx(), explicit_only=True)

    assert payload["model"] == HUB_SELECTABLE[0]
    # 那一行还是那一行：它带的是完整清单，不许被挑选改写。
    hub_row = next(r for r in payload["providers"] if r["slug"] == QUOTA_HUB_SLUG)
    assert "some/leg-the-hub-refuses" in hub_row["models"]


def test_another_provider_is_left_alone(monkeypatch):
    """provider 不是网关 ⇒ 不替它挑。

    网关的清单只答得了订阅腿（裁定 46）；别的 provider 该挑谁，是另一件事，
    这里不猜。
    """
    _hub_answers(monkeypatch)

    with patch("plobi_cli.model_switch.list_authenticated_providers", return_value=[]):
        payload = build_models_payload(_ctx(provider="openrouter"), explicit_only=True)

    assert payload["model"] == ""


def test_a_silent_hub_picks_nothing(monkeypatch):
    """网关不应答 ⇒ 不挑。

    挑不出来不是「你没有模型」——那是网关那一帧自己的规矩，这里也得守。
    """
    monkeypatch.setattr(
        "plobi.agents.registry.aigw_base_url", lambda: "http://127.0.0.1:8000/v1"
    )
    monkeypatch.setattr("plobi.agents.registry.aigw_api_key", lambda: "sk-test")
    monkeypatch.setattr(
        "plobi_cli.models.hub_selectable_model_ids",
        lambda api_key, base_url, **kwargs: None,
    )

    with patch("plobi_cli.model_switch.list_authenticated_providers", return_value=[]):
        payload = build_models_payload(_ctx(), explicit_only=True)

    assert payload["model"] == ""


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _stub_the_network(monkeypatch) -> None:
    """切换那条路上的两处网络接缝：凭据解析与模型校验。

    真机上这两处分别去问网关与厂商；本档只关心「解析得出 aigw」这一件事，
    不该去花额度，也不该让答案随机器变。
    """
    monkeypatch.setattr(
        "plobi_cli.runtime_provider.resolve_runtime_provider",
        lambda **kwargs: {
            "provider": QUOTA_HUB_SLUG,
            "api_mode": "chat_completions",
            "base_url": "http://127.0.0.1:8000/v1",
            "api_key": "sk-test",
            "source": "test",
        },
    )
    monkeypatch.setattr(
        "plobi_cli.models.validate_requested_model",
        lambda *args, **kwargs: {
            "accepted": True,
            "persist": True,
            "recognized": True,
            "message": "",
        },
    )


@pytest.fixture(autouse=True)
def _no_side_effects(monkeypatch):
    """这一档不许写配置。"""
    monkeypatch.setattr(
        "plobi_cli.config.save_config",
        lambda *args, **kwargs: pytest.fail("这一档不许写配置"),
    )
