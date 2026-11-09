"""网关那一行必须在后端目录里 —— 桌面选择框就是拿它做接头的。

现场（2026-11-08 本机现核）：网关在 `:8000` 上答了 **58** 个可选 id，而
`model.options`（`explicit_only=1`）的 payload 里**一行 `aigw` 都没有**。后果有两处，
都在同一根上：

* 桌面选择框按 `quotaProviders()`（`store/desktop-quotas.ts`）把网关那一行**克隆**成
  每条腿一组（CodeBuddy / OpenCode Zen…）；没有那一行 ⇒ 订阅模型一条都进不了选择框，
  L2 分身的模型框于是永远是「无模型」；
* 切换时 `hubProviderSlug()` 去目录里找那一行，找不到 ⇒ 报 `not_offered`
  （「该通道当前未在网关中列出」）—— 而网关明明在列 58 条。那句话在撒谎。

修法不是让用户去 `config.yaml` 里手写 `providers.aigw`：网关是这台机器自己的本地服务
（裁定 46：额度清单只有一份真源、来自网关；裁定 81：钥匙由网关自己的配置声明），
它不需要用户声明才可路由，但它的**那一行**必须在目录里。

本档零网络、零额度：网关那一读的接缝（`plobi_cli.models.hub_selectable_model_ids`）
被替换成假应答，其余 provider 行由 `list_authenticated_providers` 的替身给出。
"""
from __future__ import annotations

import contextlib
from unittest.mock import patch

import pytest

from plobi_cli.inventory import (
    QUOTA_HUB_SLUG,
    ConfigContext,
    build_models_payload,
)

#: 网关答的那批 id —— 前缀是卡 id，渲染层就是按这个前缀接头的。
HUB_IDS = ["workbuddy/hy3", "opencode_api/gpt-5.5"]

#: 额度冷却中的那条腿：模型不在可选清单里，但它只是「等一会儿」。
WAITING_IDS = ["opencode_api/big-pickle"]
WAITING_VERDICT = {
    "opencode_api/big-pickle": {
        "reason": "quota_exhausted",
        "retry_after_seconds": 1754,
        "available_at": "2026-11-08T23:13:02",
    }
}


def _ctx() -> ConfigContext:
    return ConfigContext(
        current_provider="",
        current_model="",
        current_base_url="",
        user_providers={},
        custom_providers=[],
    )


@pytest.fixture
def hub_answers(monkeypatch):
    """网关在答：接缝换成假应答，钥匙与端点也钉死（不读本机 env）。"""
    monkeypatch.setattr(
        "plobi.agents.registry.aigw_base_url", lambda: "http://127.0.0.1:8000/v1"
    )
    monkeypatch.setattr("plobi.agents.registry.aigw_api_key", lambda: "sk-test")
    monkeypatch.setattr(
        "plobi_cli.models.hub_selectable_model_ids",
        lambda api_key, base_url, **kwargs: list(HUB_IDS),
    )
    monkeypatch.setattr(
        "plobi_cli.models.hub_waiting_models",
        lambda api_key, base_url, **kwargs: dict(WAITING_VERDICT),
    )


@pytest.fixture
def hub_silent(monkeypatch):
    """网关不应答（没起、被拒、端点不通）：接缝回 ``None``。"""
    monkeypatch.setattr(
        "plobi.agents.registry.aigw_base_url", lambda: "http://127.0.0.1:8000/v1"
    )
    monkeypatch.setattr("plobi.agents.registry.aigw_api_key", lambda: "sk-test")
    monkeypatch.setattr(
        "plobi_cli.models.hub_selectable_model_ids",
        lambda api_key, base_url, **kwargs: None,
    )


@contextlib.contextmanager
def _providers(rows: list[dict]):
    """其余 provider 行由替身给出（不读真目录、不联网）。"""
    with patch(
        "plobi_cli.model_switch.list_authenticated_providers", return_value=rows
    ):
        yield


def _hub_rows(payload: dict) -> list[dict]:
    return [
        row
        for row in payload.get("providers") or []
        if str(row.get("slug", "")).lower() == QUOTA_HUB_SLUG
    ]


def test_the_hub_row_is_in_the_catalogue_when_the_hub_answers(hub_answers):
    """网关答了，那一行就得在目录里 —— 它带的是网关那批可选 id，加上在等的那些。

    「在等」的那批不在网关的**可选**清单里（那正是「在等」的意思），但它们要
    在这一行里 —— 否则它们连被标灰的机会都没有（用户 11-08 21:3x：等恢复的标灰
    保留并写剩余时间）。
    """
    with _providers([]):
        payload = build_models_payload(_ctx(), explicit_only=True)

    rows = _hub_rows(payload)
    assert len(rows) == 1, payload.get("providers")
    row = rows[0]
    assert list(row["models"]) == HUB_IDS + WAITING_IDS
    assert row["total_models"] == len(HUB_IDS) + len(WAITING_IDS)


def test_the_explicit_filter_does_not_drop_the_hub_row(hub_answers):
    """这一行不是「用户配过的 provider」，但它不许被那个过滤器扫掉。

    过滤器问的是「用户配过这一家吗」；网关不是这个问题的答案——它是产品自己的本地
    服务，用户从没在 `config.yaml` 里写过它，可它照样是订阅模型唯一的路由。
    """
    with _providers([]):
        payload = build_models_payload(_ctx(), explicit_only=True)

    assert _hub_rows(payload), "explicit_only 把网关那一行扫掉了"


def test_the_hub_row_carries_a_working_credential(hub_answers):
    """渲染层按 `authenticated === false` 丢行；这一行答了，就得带着凭据。"""
    with _providers([]):
        payload = build_models_payload(_ctx(), explicit_only=True)

    row = _hub_rows(payload)[0]
    assert row["authenticated"] is True
    assert row.get("auth_type") == "api_key"


def test_the_hub_row_shows_no_internal_vocabulary(hub_answers):
    """裁定 45.4：屏上不许出现内部代号。名字是软件那一侧的叫法，slug 是路由值。"""
    with _providers([]):
        payload = build_models_payload(_ctx(), explicit_only=True)

    row = _hub_rows(payload)[0]
    assert row["name"] == "订阅额度"
    assert QUOTA_HUB_SLUG not in row["name"].lower()


def test_a_model_that_is_only_waiting_is_in_the_row_with_its_verdict(hub_answers):
    """等恢复的模型要进这一行，且带上原因与剩余时间。

    它不在网关的**可选**清单里（那正是「在等」的意思），所以它只能从另一条
    读法进来；判词挂在 `capabilities[model].unavailable` 上 —— 那是唯一一条
    能把「为什么现在不能选、还要等多久」带到屏幕上的通道。
    """
    with _providers([]):
        payload = build_models_payload(_ctx(), explicit_only=True, capabilities=True)

    row = _hub_rows(payload)[0]
    assert WAITING_IDS[0] in row["models"]
    assert row["capabilities"][WAITING_IDS[0]]["unavailable"] == WAITING_VERDICT[WAITING_IDS[0]]


def test_a_waiting_model_does_not_claim_a_pickable_row(hub_answers):
    """普通行不该带「不可用」的标记 —— 只有冷却中的那批带。"""
    with _providers([]):
        payload = build_models_payload(_ctx(), explicit_only=True, capabilities=True)

    row = _hub_rows(payload)[0]
    for model in HUB_IDS:
        assert "unavailable" not in row["capabilities"].get(model, {})


def test_a_silent_hub_leaves_no_row_and_claims_nothing(hub_silent):
    """网关读不到 ⇒ 没有那一行；但绝不能因此把别的 provider 说成没有。

    「我们没问到」与「那里没有」是两件事（hub 那一帧也是这么分的）。
    """
    other = {"slug": "openrouter", "name": "OpenRouter", "models": ["m1"],
             "total_models": 1, "is_current": True, "is_user_defined": False,
             "source": "built-in", "authenticated": True}
    with _providers([other]):
        payload = build_models_payload(_ctx())

    assert not _hub_rows(payload)
    kept = [row for row in payload["providers"] if row["slug"] == "openrouter"]
    assert len(kept) == 1, payload["providers"]
    assert list(kept[0]["models"]) == ["m1"]


def test_a_configured_hub_row_is_not_duplicated(hub_answers):
    """用户自己在 `config.yaml` 里写过 `providers.aigw` 时，目录里只留一行。

    那一行是用户自己的声明（可能指着别处），不许被我们再补一行同名的那把它盖掉。
    """
    configured = {"slug": QUOTA_HUB_SLUG, "name": "订阅额度", "models": ["workbuddy/hy3"],
                  "total_models": 1, "is_current": False, "is_user_defined": True,
                  "source": "user-config", "authenticated": True}

    with _providers([configured]):
        payload = build_models_payload(_ctx(), explicit_only=True)

    rows = _hub_rows(payload)
    assert len(rows) == 1, [row["slug"] for row in payload["providers"]]
    assert rows[0]["source"] == "user-config"
