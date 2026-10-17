"""Routing turns ADR-0011's cost discipline into assertions."""

from __future__ import annotations

import json

import pytest

from plobi.routing import L1_SECRETARY, L2_AGENDA, ModelRoute, ModelRouter, RoutingError


def test_defaults_put_the_flagship_on_l1_and_cheap_models_below(tmp_path):
    router = ModelRouter.load(tmp_path / "missing.json")

    assert router.resolve(L1_SECRETARY).model == "kimi-k3"
    assert router.resolve(L2_AGENDA).qualified == "aigw/workbuddy/deepseek-chat"
    assert router.violations() == []


def test_config_overrides_defaults(tmp_path):
    path = tmp_path / "models.json"
    path.write_text(
        json.dumps({"roles": {L1_SECRETARY: {"provider": "openai", "model": "gpt-5.6"}}}),
        encoding="utf-8",
    )

    router = ModelRouter.load(path)
    assert router.resolve(L1_SECRETARY).qualified == "openai/gpt-5.6"
    # Unspecified roles keep their defaults.
    assert router.resolve(L2_AGENDA).qualified == "aigw/workbuddy/deepseek-chat"


def test_l1_may_not_point_at_a_gui_only_surface(tmp_path):
    router = ModelRouter.load(tmp_path / "missing.json")
    router.routes[L1_SECRETARY] = ModelRoute(role=L1_SECRETARY, provider="marvis", model="gui")

    problems = router.violations()
    assert any("GUI-only" in problem for problem in problems)

    with pytest.raises(RoutingError):
        router.assert_valid()


def test_aigw_workbuddy_model_is_not_a_gui_surface(tmp_path):
    """MVP §8.2 L3a: L2 via aigw workbuddy/* is API quota, not HID."""
    route = ModelRoute(
        role=L2_AGENDA, provider="aigw", model="workbuddy/deepseek-chat"
    )
    assert route.is_gui_surface is False
    router = ModelRouter.load(tmp_path / "missing.json")
    assert router.violations() == []


def test_l2_sharing_l1s_model_is_flagged(tmp_path):
    router = ModelRouter.load(tmp_path / "missing.json")
    router.routes[L2_AGENDA] = ModelRoute(role=L2_AGENDA, provider="moonshot", model="kimi-k3")

    assert any("shares L1's model" in problem for problem in router.violations())


def test_missing_l1_model_is_a_violation(tmp_path):
    router = ModelRouter.load(tmp_path / "missing.json")
    router.routes[L1_SECRETARY] = ModelRoute(role=L1_SECRETARY)

    assert router.violations() == ["l1_secretary has no model configured"]


def test_unknown_role_raises(tmp_path):
    router = ModelRouter.load(tmp_path / "missing.json")

    with pytest.raises(RoutingError):
        router.resolve("l9_imaginary")


def test_custom_roles_survive_a_round_trip(tmp_path):
    path = tmp_path / "models.json"
    router = ModelRouter.load(path)
    router.routes["l2_srtp"] = ModelRoute(role="l2_srtp", provider="deepseek", model="deepseek-chat")
    router.save(path)

    reloaded = ModelRouter.load(path)
    assert reloaded.resolve("l2_srtp").qualified == "deepseek/deepseek-chat"


def test_config_path_honours_env_override(tmp_path, monkeypatch):
    from plobi.routing.models import config_path

    monkeypatch.setenv("PLOBI_MODELS_CONFIG", str(tmp_path / "custom.json"))
    assert config_path() == tmp_path / "custom.json"


def test_corrupt_config_falls_back_to_defaults(tmp_path):
    path = tmp_path / "models.json"
    path.write_text("{ not json", encoding="utf-8")

    assert ModelRouter.load(path).resolve(L1_SECRETARY).model == "kimi-k3"


# ---------------------------------------------------------------------------
# 裁定 166 决定一：路由表只存共享层那一份，读侧「共享 → 自家复印件 → 出厂默认」
# ---------------------------------------------------------------------------


def _two_homes(tmp_path, *, shared_roles, profile_roles):
    """造一个「顶层家 + 一个分身家」的两层落点，两边各放一张不同的表。"""
    root = tmp_path / "plobi-home"
    profile = root / "profiles" / "kit"
    (root / "plobi").mkdir(parents=True)
    (profile / "plobi").mkdir(parents=True)
    (root / "plobi" / "models.json").write_text(
        json.dumps({"roles": shared_roles}), encoding="utf-8")
    (profile / "plobi" / "models.json").write_text(
        json.dumps({"roles": profile_roles}), encoding="utf-8")
    return root, profile


def test_the_shared_table_wins_over_the_profile_copy(tmp_path, monkeypatch):
    """配一次大家能用＝读侧必须先看共享那一份。

    顺序反过来（自家那份赢）就是现核那个病灶：23 份复印件各读各的，顶层改一次谁都不变。
    """
    from plobi.routing.models import config_path

    root, profile = _two_homes(
        tmp_path,
        shared_roles={L2_AGENDA: {"provider": "shared", "model": "shared-m"}},
        profile_roles={L2_AGENDA: {"provider": "stale", "model": "stale-m"}},
    )
    monkeypatch.delenv("PLOBI_MODELS_CONFIG", raising=False)
    monkeypatch.setenv("PLOBI_HOME", str(profile))

    assert config_path() == root / "plobi" / "models.json"
    assert ModelRouter.load().resolve(L2_AGENDA).qualified == "shared/shared-m"


def test_save_writes_one_file_not_a_new_copy(tmp_path, monkeypatch):
    """写侧不跟随读侧的回落：默认只写共享那一份，否则「存一次」＝「复印一次」。"""
    from plobi.routing.models import shared_config_path

    root, profile = _two_homes(
        tmp_path,
        shared_roles={L1_SECRETARY: {"provider": "moonshot", "model": "kimi-k3"}},
        profile_roles={},
    )
    monkeypatch.delenv("PLOBI_MODELS_CONFIG", raising=False)
    monkeypatch.setenv("PLOBI_HOME", str(profile))

    target = ModelRouter.load().save()

    assert target == shared_config_path() == root / "plobi" / "models.json"
    assert json.loads((root / "plobi" / "models.json").read_text(encoding="utf-8"))["roles"]
    assert json.loads((profile / "plobi" / "models.json").read_text(encoding="utf-8")) == {"roles": {}}, \
        "save() 写到分身家去了＝复印机没关"


def test_a_fresh_home_falls_back_to_the_shared_copy(tmp_path, monkeypatch):
    """共享那份不存在时回落到自家（旧数据兼容），两条路都不许把路由表读成空。"""
    from plobi.routing.models import config_path

    root = tmp_path / "plobi-home"
    profile = root / "profiles" / "kit"
    (profile / "plobi").mkdir(parents=True)
    (profile / "plobi" / "models.json").write_text(
        json.dumps({"roles": {L2_AGENDA: {"provider": "legacy", "model": "legacy-m"}}}),
        encoding="utf-8")
    monkeypatch.delenv("PLOBI_MODELS_CONFIG", raising=False)
    monkeypatch.setenv("PLOBI_HOME", str(profile))

    assert config_path() == profile / "plobi" / "models.json"
    assert ModelRouter.load().resolve(L2_AGENDA).qualified == "legacy/legacy-m"
