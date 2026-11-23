"""Routing turns ADR-0011's cost discipline into assertions."""

from __future__ import annotations

import json

import pytest

from plobi.routing import (
    CONDUCTOR,
    PRINCIPAL,
    ModelRoute,
    ModelRouter,
    RoutingError,
)


def test_defaults_put_the_flagship_on_l1_and_cheap_models_below(tmp_path):
    router = ModelRouter.load(tmp_path / "missing.json")

    assert router.resolve(CONDUCTOR).model == "kimi-k3"
    assert router.resolve(PRINCIPAL).qualified == "workbuddy/deepseek-v4.1-flash"
    assert router.violations() == []


def test_config_overrides_defaults(tmp_path):
    path = tmp_path / "models.json"
    path.write_text(
        json.dumps({"roles": {CONDUCTOR: {"provider": "openai", "model": "gpt-5.6"}}}),
        encoding="utf-8",
    )

    router = ModelRouter.load(path)
    assert router.resolve(CONDUCTOR).qualified == "openai/gpt-5.6"
    # Unspecified roles keep their defaults.
    assert router.resolve(PRINCIPAL).qualified == "workbuddy/deepseek-v4.1-flash"


# ---------------------------------------------------------------------------
# 2026-11-14：GUI_ONLY_SURFACES 这套概念删除（用户：「GUI SURFACE 又是什么鬼。
# 不要了」），模型行按「真实模型名」上屏。
#
# 2026-11-15：连读侧那三张兼容表（``aigw`` → ``bezel`` 的 provider 别名、
# ``l2_planner`` 退役角色、``workbuddy/deepseek-chat`` 假模型回落）一起删了。
# 依据是用户同日的裁定——内测版没有真实用户、不为老配置付代价；且核查过真机
# 家目录没有生产 models.json，命中全在 pytest 临时家。上面三条测试跟着退场，
# 只剩「模型名显示」与「上游清单校验」这两类还该测。
# ---------------------------------------------------------------------------


def test_display_model_drops_the_upstream_prefix(tmp_path):
    """模型行只留真实模型名；请求层仍是完整复合 id。"""
    route = ModelRoute(role=PRINCIPAL, provider="bezel", model="workbuddy/deepseek-v4.1-flash")

    assert route.display_model == "deepseek-v4.1-flash"
    assert route.upstream == "workbuddy"
    assert route.model == "workbuddy/deepseek-v4.1-flash", "请求层不许被剥前缀"


def test_a_plain_model_name_has_no_upstream(tmp_path):
    route = ModelRoute(role=CONDUCTOR, provider="moonshot", model="kimi-k3")

    assert route.display_model == "kimi-k3"
    assert route.upstream == ""


def test_l2_sharing_l1s_model_is_flagged(tmp_path):
    router = ModelRouter.load(tmp_path / "missing.json")
    router.routes[PRINCIPAL] = ModelRoute(role=PRINCIPAL, provider="moonshot", model="kimi-k3")

    assert any("shares L1's model" in problem for problem in router.violations())


def test_missing_l1_model_is_a_violation(tmp_path):
    router = ModelRouter.load(tmp_path / "missing.json")
    router.routes[CONDUCTOR] = ModelRoute(role=CONDUCTOR)

    assert router.violations() == ["conductor has no model configured"]


def test_unknown_role_raises(tmp_path):
    router = ModelRouter.load(tmp_path / "missing.json")

    with pytest.raises(RoutingError):
        router.resolve("l9_imaginary")


def test_retired_role_keys_are_dropped_and_never_written_back(tmp_path):
    """盘上还剩四代退休档位时，读进来只有两个角色，存回去也不再出现。

    2026-11-19 用户：「怎么还有 role=-projects 的东西呀。不是说了 projects 只是
    principal 里面的一个分类吗？不要做旧数据兼容层了」——``l2_project`` 是 category，
    不是角色；读侧收下它，设置页就会把它列成一个可选角色。
    """
    path = tmp_path / "models.json"
    path.write_text(json.dumps({"roles": {
        "l1_secretary": {"provider": "moonshot", "model": "kimi-k3"},
        "l2_agenda": {"provider": "aigw", "model": "workbuddy/deepseek-v4.1-flash"},
        "l2_planner": {"provider": "aigw", "model": "workbuddy/deepseek-v4.1-flash"},
        "l2_project": {"provider": "aigw", "model": "workbuddy/deepseek-v4.1-flash"},
        CONDUCTOR: {"provider": "moonshot", "model": "kimi-k3"},
    }}), encoding="utf-8")

    router = ModelRouter.load(path)
    assert set(router.routes) == {CONDUCTOR, PRINCIPAL}
    with pytest.raises(RoutingError):
        router.resolve("l2_project")

    router.save(path)
    written = json.loads(path.read_text(encoding="utf-8"))["roles"]
    assert set(written) == {CONDUCTOR, PRINCIPAL}


def test_a_dirty_key_in_routes_is_not_persisted(tmp_path):
    """就算有人往 ``routes`` 里塞了退休键，``save()`` 也只写这两个角色。"""
    path = tmp_path / "models.json"
    router = ModelRouter.load(tmp_path / "missing.json")
    router.routes["l2_srtp"] = ModelRoute(role="l2_srtp", provider="deepseek", model="deepseek-chat")

    router.save(path)
    assert set(json.loads(path.read_text(encoding="utf-8"))["roles"]) == {CONDUCTOR, PRINCIPAL}


def test_config_path_honours_env_override(tmp_path, monkeypatch):
    from plobi.routing.models import config_path

    monkeypatch.setenv("PLOBI_MODELS_CONFIG", str(tmp_path / "custom.json"))
    assert config_path() == tmp_path / "custom.json"


def test_corrupt_config_falls_back_to_defaults(tmp_path):
    path = tmp_path / "models.json"
    path.write_text("{ not json", encoding="utf-8")

    assert ModelRouter.load(path).resolve(CONDUCTOR).model == "kimi-k3"


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
        shared_roles={PRINCIPAL: {"provider": "shared", "model": "shared-m"}},
        profile_roles={PRINCIPAL: {"provider": "stale", "model": "stale-m"}},
    )
    monkeypatch.delenv("PLOBI_MODELS_CONFIG", raising=False)
    monkeypatch.setenv("PLOBI_HOME", str(profile))

    assert config_path() == root / "plobi" / "models.json"
    assert ModelRouter.load().resolve(PRINCIPAL).qualified == "shared/shared-m"


def test_save_writes_one_file_not_a_new_copy(tmp_path, monkeypatch):
    """写侧不跟随读侧的回落：默认只写共享那一份，否则「存一次」＝「复印一次」。"""
    from plobi.routing.models import shared_config_path

    root, profile = _two_homes(
        tmp_path,
        shared_roles={CONDUCTOR: {"provider": "moonshot", "model": "kimi-k3"}},
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
        json.dumps({"roles": {PRINCIPAL: {"provider": "legacy", "model": "legacy-m"}}}),
        encoding="utf-8")
    monkeypatch.delenv("PLOBI_MODELS_CONFIG", raising=False)
    monkeypatch.setenv("PLOBI_HOME", str(profile))

    assert config_path() == profile / "plobi" / "models.json"
    assert ModelRouter.load().resolve(PRINCIPAL).qualified == "legacy/legacy-m"


def test_a_model_upstream_does_not_list_is_a_violation():
    """给了上游清单就按清单查：这才是 ``workbuddy/deepseek-chat`` 该被抓住的地方。

    这次漏检的根因不是没写校验，是**从来没有把上游真实模型列表喂进来**——
    ``violations()`` 只看结构（非空、别撞 L1），一个语法合法的假 id 能一路绿。
    """
    router = ModelRouter(routes={
        CONDUCTOR: ModelRoute(role=CONDUCTOR, provider="moonshot", model="kimi-k3"),
        PRINCIPAL: ModelRoute(role=PRINCIPAL, provider="bezel", model="workbuddy/deepseek-chat"),
    })

    assert router.violations() == [], "没给清单时不许凭空判死（路由层不联网）"
    problems = router.violations(known_models=["workbuddy/deepseek-v4.1-flash", "workbuddy/hy4free"])
    assert any("workbuddy/deepseek-chat" in p for p in problems)


def test_a_model_upstream_lists_passes_the_catalogue_check():
    router = ModelRouter(routes={
        CONDUCTOR: ModelRoute(role=CONDUCTOR, provider="moonshot", model="kimi-k3"),
        PRINCIPAL: ModelRoute(role=PRINCIPAL, provider="bezel", model="workbuddy/deepseek-v4.1-flash"),
    })

    assert router.violations(known_models=["workbuddy/deepseek-v4.1-flash"]) == []
