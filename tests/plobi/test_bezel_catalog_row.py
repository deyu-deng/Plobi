"""订阅额度那组行：**一家软件一行** —— `bezel` 不再是 provider。

规矩（主人 11-17 18:5x / 11-18）：「bezel 是内部名称。provider 下面适配的是一个一个
软件名，而不是 bezel。」所以这一组行的形状也换了：

* 旧样：目录里补**一行** slug=`bezel`，桌面再把它克隆成每条腿一组；
* 现样：目录里直接是**一家软件一行**（slug=`workbuddy`…），名字是那家软件的品牌名，
  模型只留自己前缀下的 —— 切到 `workbuddy` 不该看见别家的模型。

切分靠**模型 id 的前缀**（`workbuddy/hy3` ⇒ `workbuddy`），不靠任何手抄名单：
网关名册新上一家，前缀一出现就有行，不必等谁去改常量。

本档零网络、零额度：网关那一读的接缝（`plobi_cli.models.hub_selectable_model_ids`）
被替换成假应答，其余 provider 行由 `list_authenticated_providers` 的替身给出。
"""
from __future__ import annotations

import contextlib
from unittest.mock import patch

import pytest

from plobi_cli.inventory import ConfigContext, build_models_payload

#: 网关答的那批 id —— 前缀就是那家软件（模型 id 里本来就有「谁在服务它」）。
HUB_IDS = ["workbuddy/hy3", "opencode-zen-free/gpt-5.5"]

#: 额度冷却中的那条腿：模型不在可选清单里，但它只是「等一会儿」。
WAITING_IDS = ["opencode-zen-free/big-pickle"]
WAITING_VERDICT = {
    "opencode-zen-free/big-pickle": {
        "reason": "quota_exhausted",
        "retry_after_seconds": 1754,
        "available_at": "2026-11-18T23:13:02",
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
        "plobi.agents.registry.bezel_base_url", lambda: "http://127.0.0.1:8000/v1"
    )
    monkeypatch.setattr("plobi.agents.registry.bezel_api_key", lambda: "sk-test")
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
        "plobi.agents.registry.bezel_base_url", lambda: "http://127.0.0.1:8000/v1"
    )
    monkeypatch.setattr("plobi.agents.registry.bezel_api_key", lambda: "sk-test")
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
    """网关那组行 —— 它们的出处是这一处，不是用户配置。"""
    return [
        row
        for row in payload.get("providers") or []
        if row.get("source") == "hub-software"
    ]


def _hub_row(payload: dict, slug: str) -> dict:
    for row in payload.get("providers") or []:
        if str(row.get("slug", "")).lower() == slug:
            return row
    raise AssertionError(f"目录里没有 {slug} 这一行：{[r.get('slug') for r in payload.get('providers') or []]}")


def test_one_row_per_software_and_only_its_own_models(hub_answers):
    """一家软件一行；行里只留自己前缀下的模型（切过去不看见别家的）。"""
    with _providers([]):
        payload = build_models_payload(_ctx(), explicit_only=True)

    workbuddy = _hub_row(payload, "workbuddy")
    assert list(workbuddy["models"]) == ["workbuddy/hy3"]
    assert workbuddy["total_models"] == 1

    opencode = _hub_row(payload, "opencode-zen-free")
    # 「在等」的那条也在自己这一行里 —— 否则它连被标灰的机会都没有
    # （用户 11-08 21:3x：等恢复的标灰保留并写剩余时间）。
    assert list(opencode["models"]) == ["opencode-zen-free/gpt-5.5", "opencode-zen-free/big-pickle"]


def test_the_machine_name_is_not_a_row(hub_answers):
    """`bezel` 只是传输：它不许再作为一行出现在目录里（那是旧形状的病根）。"""
    with _providers([]):
        payload = build_models_payload(_ctx(), explicit_only=True)

    slugs = [str(row.get("slug", "")).lower() for row in payload.get("providers") or []]
    assert "bezel" not in slugs, slugs


def test_the_rows_are_named_after_the_software(hub_answers):
    """裁定 45.4：屏上是软件名（品牌名），不是网关的机器标识。"""
    with _providers([]):
        payload = build_models_payload(_ctx(), explicit_only=True)

    assert _hub_row(payload, "workbuddy")["name"] == "WorkBuddy"
    # 软件名跟着网关目录走：11-2x 起这家的品牌名是「OpenCode Zen Free」（
    # providers.yaml 已改），屏上就不许再显示旧名「OpenCode Free」。
    assert _hub_row(payload, "opencode-zen-free")["name"] == "OpenCode Zen Free"
    for row in _hub_rows(payload):
        assert "bezel" not in str(row.get("name", "")).lower()


def test_the_explicit_filter_does_not_drop_these_rows(hub_answers):
    """它们不是「用户配过的 provider」，但不许被那个过滤器扫掉。

    过滤器问的是「用户配过这一家吗」；网关不是这个问题的答案 —— 它是产品自己的本地
    服务，用户从没在 `config.yaml` 里写过它，可它照样是订阅模型唯一的路由。
    """
    with _providers([]):
        payload = build_models_payload(_ctx(), explicit_only=True)

    assert {row["slug"] for row in _hub_rows(payload)} == {"workbuddy", "opencode-zen-free"}


def test_the_rows_carry_a_working_credential(hub_answers):
    """渲染层按 `authenticated === false` 丢行；网关答了，这些行就得带着凭据。"""
    with _providers([]):
        payload = build_models_payload(_ctx(), explicit_only=True)

    for row in _hub_rows(payload):
        assert row["authenticated"] is True
        assert row.get("auth_type") == "api_key"


def test_a_model_that_is_only_waiting_is_in_the_row_with_its_verdict(hub_answers):
    """等恢复的模型要进自己那一行，且带上原因与剩余时间。

    它不在网关的**可选**清单里（那正是「在等」的意思），所以它只能从另一条读法进来；
    判词挂在 `capabilities[model].unavailable` 上 —— 那是唯一一条能把「为什么现在
    不能选、还要等多久」带到屏幕上的通道。
    """
    with _providers([]):
        payload = build_models_payload(_ctx(), explicit_only=True, capabilities=True)

    opencode = _hub_row(payload, "opencode-zen-free")
    assert WAITING_IDS[0] in opencode["models"]
    assert opencode["capabilities"][WAITING_IDS[0]]["unavailable"] == WAITING_VERDICT[WAITING_IDS[0]]
    # 别家那一行不许沾上这条判词。
    workbuddy = _hub_row(payload, "workbuddy")
    assert WAITING_IDS[0] not in workbuddy["capabilities"]
    for model in workbuddy["models"]:
        assert "unavailable" not in workbuddy["capabilities"].get(model, {})


def test_a_software_the_roster_never_heard_of_still_gets_a_row(hub_answers, monkeypatch):
    """切分靠**模型前缀**，不靠手抄名单（第二处那根病根）。

    网关名册新上一家、软件名还没进生成表时，旧实现会静默丢掉这条腿（只打一行
    warning）；现在它照样成行 —— 名字退回 slug，至少用户能看见、能选。
    """
    monkeypatch.setattr(
        "plobi_cli.models.hub_selectable_model_ids",
        lambda api_key, base_url, **kwargs: ["brand-new-app/some-model"],
    )
    with _providers([]):
        payload = build_models_payload(_ctx(), explicit_only=True)

    row = _hub_row(payload, "brand-new-app")
    assert list(row["models"]) == ["brand-new-app/some-model"]
    assert row["name"] == "brand-new-app"
    assert row["authenticated"] is True


def test_a_silent_hub_leaves_no_row_and_claims_nothing(hub_silent):
    """网关读不到 ⇒ 没有这些行；但绝不能因此把别的 provider 说成没有。

    「我们没问到」与「那里没有」是两件事（hub 那一帧也是这么分的）。
    """
    other = {"slug": "openrouter", "name": "OpenRouter", "models": ["m1"],
             "total_models": 1, "is_current": True, "is_user_defined": False,
             "source": "built-in", "authenticated": True}
    with _providers([other]):
        payload = build_models_payload(_ctx())

    assert _hub_rows(payload) == []
    kept = [row for row in payload["providers"] if row["slug"] == "openrouter"]
    assert len(kept) == 1, payload["providers"]
    assert list(kept[0]["models"]) == ["m1"]


def test_a_configured_software_row_is_merged_not_duplicated(hub_answers):
    """用户自己在 `config.yaml` 里写过 `providers.workbuddy` 时，目录里只留一行。

    那一行是用户自己的声明（可能指着别处），不许被我们再补一行同名的盖掉；网关答出的
    模型并进它，但 `source` 仍归用户。
    """
    configured = {"slug": "workbuddy", "name": "WorkBuddy", "models": ["workbuddy/pinned"],
                  "total_models": 1, "is_current": False, "is_user_defined": True,
                  "source": "user-config", "authenticated": True}

    with _providers([configured]):
        payload = build_models_payload(_ctx(), explicit_only=True)

    rows = [row for row in payload["providers"] if row["slug"] == "workbuddy"]
    assert len(rows) == 1, [row["slug"] for row in payload["providers"]]
    assert rows[0]["source"] == "user-config"
    assert "workbuddy/hy3" in rows[0]["models"], "网关答出的模型没有并进用户那一行"
