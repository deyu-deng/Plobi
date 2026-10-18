"""配置只有一份：分身家那份复印件**读侧不再赢**，每分身不同的两样改从名册那一行读时叠
（``WP-AGENT-CONFIG-SINGLE-SOURCE`` 第二半＝ADR-0013 拆除表 ``config.yaml`` 那一行）。

为什么这一族要单独建档：这台机器现核 **24 个分身家各有一份 ``config.yaml``**，而
``plobi_cli/config.py::get_config_path()`` 改前是「自家有就读自家」。复印件赢的那二十四个
后果就是用户那句「我在设置里配一次，为什么每个分身还要再配一遍」——所以这里的每一条都
写成**可区分的读数**，不写形容词：

1. 那个家**即使躺着一份 ``config.yaml``**，生效值也不来自它（这条同时护住"隔离之后某个
   上游开关又在家下重建一份"那种回潮——重建的那份进不了生效值）。
2. 用户在顶层配一次 ⇒ 任意分身解析到同一个值；给某个分身写名册行上的覆盖 ⇒
   **只有它变**，其他家逐字不变（ADR-0013 完成定义第 1 条）。
3. 覆盖住在名册里，不住在家里：改名册那一行当场生效，不用重启进程（缓存签名带名册 mtime）。
4. 这条链**只有一个答案**。``plobi_constants.get_config_path()`` 以前是裸的
   ``get_plobi_home() / "config.yaml"``，而 ``agent/skill_utils.py`` 走的正是它——两个答案
   并存时，"停读"会让分身连技能都看不见。这里钉两者同源。
5. 写侧那扇门（``atomic_config_write``）收到「分身家的 config.yaml」时改道到那一份；
   而**不是分身家**的路径必须原样落盘（负例，防止改道扩到别人身上）。

零额度：全程不碰网络、不发模型，只读写临时家。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from plobi_cli import config as cfg


@pytest.fixture()
def root(tmp_path, monkeypatch):
    """一个临时顶层家：顶层 ``config.yaml`` 一份 + 名册一份 + 三个分身家。"""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    base = tmp_path / ".plobi"
    (base / "plobi").mkdir(parents=True)
    for name in ("l2-agenda", "l2-aura", "l2-kit"):
        (base / "profiles" / name).mkdir(parents=True)
    (base / "config.yaml").write_text(
        "model:\n  provider: aigw\n  default: workbuddy/configured-once\n", encoding="utf-8"
    )
    monkeypatch.setenv("PLOBI_HOME", str(base))
    monkeypatch.setenv("PLOBI_PROJECTS_CONFIG", str(base / "plobi" / "projects.yaml"))
    monkeypatch.delenv("PLOBI_MODELS_CONFIG", raising=False)
    _write_roster(base, {})
    _clear()
    yield base
    _clear()


def _clear() -> None:
    cfg._LOAD_CONFIG_CACHE.clear()
    cfg._RAW_CONFIG_CACHE.clear()
    cfg._LAST_EXPANDED_CONFIG_BY_PATH.clear()


def _write_roster(base: Path, rows: dict) -> None:
    from plobi.agents.registry import REGISTRY_VERSION

    Path(os.environ["PLOBI_PROJECTS_CONFIG"]).write_text(
        yaml.safe_dump(
            {"version": REGISTRY_VERSION, "agents": rows}, sort_keys=False, allow_unicode=True
        ),
        encoding="utf-8",
    )


def _row(profile: str, **extra) -> dict:
    base = {"name": profile, "role": "l2_project", "profile": profile, "category": "projects"}
    base.update(extra)
    return base


def _effective(home_dir: Path) -> dict:
    """那个家**运行时实际读到的**那份配置（解析函数由被测代码自己出，不手拼）。"""
    os.environ["PLOBI_HOME"] = str(home_dir)
    _clear()
    return cfg.load_config()


def _effective_model(home_dir: Path) -> dict:
    return _effective(home_dir)["model"]


# ---------------------------------------------------------------------------
# 1. 复印件翻不回第二真源
# ---------------------------------------------------------------------------


def test_a_profile_home_does_not_read_its_own_copy_even_when_the_file_is_there(root):
    """判据原文：把 ``PLOBI_HOME`` 指进 ``profiles/l2-agenda`` 读配置，改后必须读到顶层值。

    故意在家里留一份**内容不同**的 ``config.yaml``（``stale/copy``）：读侧若还认自家，
    拿到的就是这个词；认顶层那一份才拿得到 ``workbuddy/configured-once``。这一条也是
    「隔离之后某个上游开关又在家下重建一份」不再要紧的那颗钉子。
    """
    profile_home = root / "profiles" / "l2-agenda"
    (profile_home / "config.yaml").write_text(
        "model:\n  provider: copyist\n  default: stale/copy\n", encoding="utf-8"
    )

    os.environ["PLOBI_HOME"] = str(profile_home)
    _clear()
    resolved = cfg.get_config_path()
    assert resolved == root / "config.yaml"
    assert _effective_model(profile_home)["default"] == "workbuddy/configured-once"
    # 那份复印件一个字没动（本刀只让读侧忽略它，改写/删除都不是这一把的活）
    assert "stale/copy" in (profile_home / "config.yaml").read_text(encoding="utf-8")


def test_the_top_level_home_still_reads_its_own_file(root):
    """负例：顶层家不是分身家，它读的就是自己那一份——别把忽略扩到顶层身上。"""
    os.environ["PLOBI_HOME"] = str(root)
    _clear()

    assert cfg.get_config_path() == root / "config.yaml"
    assert _effective_model(root)["default"] == "workbuddy/configured-once"


# ---------------------------------------------------------------------------
# 2. 配一次大家生效；单独选只有它变
# ---------------------------------------------------------------------------


def test_one_configured_model_reaches_every_agent(root):
    """ADR-0013 完成定义第 1 条的前半：顶层配那一次 ⇒ 每个分身解析到同一个值。"""
    _write_roster(root, {
        "agenda": _row("l2-agenda", role="l2_agenda", name="agenda"),
        "aura": _row("l2-aura"),
        "kit": _row("l2-kit"),
    })

    values = [_effective_model(root / "profiles" / p) for p in ("l2-agenda", "l2-aura", "l2-kit")]

    assert values == [{"provider": "aigw", "default": "workbuddy/configured-once"}] * 3


def test_a_roster_override_moves_only_that_agent(root):
    """判据追加的那一条：给某个分身写名册行上的 ``model:`` ⇒ **只有它变**，其他家逐字不变。

    注意这里改的是名册那一行，不是任何家的文件——以前这一格是
    ``_write_profile_config`` 往那个家写一节，于是"单独选一个 Agent 的模型"这个动作
    本身就在造复印件。
    """
    _write_roster(root, {
        "agenda": _row("l2-agenda", role="l2_agenda", name="agenda"),
        "aura": _row("l2-aura"),
        "kit": _row("l2-kit"),
    })
    homes = {p: root / "profiles" / p for p in ("l2-agenda", "l2-aura", "l2-kit")}
    before = {p: _effective_model(h) for p, h in homes.items()}
    assert len({repr(v) for v in before.values()}) == 1, before  # 起点：三家同一个值

    rows = yaml.safe_load(Path(os.environ["PLOBI_PROJECTS_CONFIG"]).read_text(encoding="utf-8"))
    rows["agents"]["aura"]["provider"] = "zhipu"
    rows["agents"]["aura"]["model"] = "glm-4.x"
    _write_roster(root, rows["agents"])

    after = {p: _effective_model(h) for p, h in homes.items()}
    assert after["l2-aura"] != before["l2-aura"]
    assert after["l2-aura"]["default"] == "glm-4.x"
    assert after["l2-agenda"] == before["l2-agenda"]
    assert after["l2-kit"] == before["l2-kit"]


def test_two_agents_with_overrides_do_not_fold_into_one_role_route(root):
    """裁定 172 那条判据：覆盖按 **Agent 身份**解析，不许折进共享 role 那张路由。

    现核名册 24 行里 ``l2_project`` 占 23 行，而 :meth:`AgentRegistry.router` 把每行覆盖按
    **role** 盖进那张表（``routes[entry.role] = ...``，后写的赢）。所以「两个项目分身各选
    各的模型」若走 ``router()`` 就会双双变成同一个值——这条就是不让那一步回潮。
    """
    _write_roster(root, {
        "aura": _row("l2-aura", provider="moonshot", model="kimi-a"),
        "kit": _row("l2-kit", provider="zhipu", model="glm-b"),
    })

    aura = _effective_model(root / "profiles" / "l2-aura")
    kit = _effective_model(root / "profiles" / "l2-kit")

    assert aura["default"] == "kimi-a", aura
    assert kit["default"] == "glm-b", kit


def test_an_unresolvable_provider_override_is_not_applied(root, caplog):
    """那道可解析闸门跟着搬进读侧：底座认不出的名字不许叠给这个分身（宁可不给）。

    原话来自 ``_write_profile_config`` 落笔前那道闸——写进一个解析不出来的 provider
    = 这个分身一启动就一条 ``Unknown provider`` 红条。现在不写、也不叠，回到顶层那一份。
    """
    import logging
    from plobi_cli.auth import AuthError, resolve_provider

    ghost = "no-such-provider-single-source-check"
    with pytest.raises(AuthError) as excinfo:  # 前提自证：权威确实拒这个名字
        resolve_provider(ghost)
    assert excinfo.value.code == "invalid_provider"

    _write_roster(root, {"aura": _row("l2-aura", provider=ghost, model="a-model")})

    with caplog.at_level(logging.WARNING, logger="plobi.agents.registry"):
        model = _effective_model(root / "profiles" / "l2-aura")

    assert model["default"] == "workbuddy/configured-once"
    assert any(ghost in r.getMessage() for r in caplog.records), "被拦下要说不出为什么"


# ---------------------------------------------------------------------------
# 3. 覆盖住在名册里：改名册当场生效（缓存签名那颗钉子）
# ---------------------------------------------------------------------------


def test_editing_the_roster_takes_effect_without_restarting(root):
    """不清缓存、不重启进程：只改名册那一行，下一次 ``load_config`` 就是新值。

    ``load_config`` 的缓存按文件签名判命中。以前生效靠"往分身家写文件"，写的那份文件的
    mtime 自然把缓存顶掉；改成读时叠之后，**名册那份签名**必须进签名，否则
    「给这个 Agent 换个模型」要等后端重启才看得见。
    """
    _write_roster(root, {"aura": _row("l2-aura", provider="moonshot", model="kimi-k3")})
    home = root / "profiles" / "l2-aura"
    os.environ["PLOBI_HOME"] = str(home)
    _clear()
    assert cfg.load_config()["model"]["default"] == "kimi-k3"

    rows = yaml.safe_load(Path(os.environ["PLOBI_PROJECTS_CONFIG"]).read_text(encoding="utf-8"))
    rows["agents"]["aura"]["model"] = "kimi-k9"
    _write_roster(root, rows["agents"])

    assert cfg.load_config()["model"]["default"] == "kimi-k9"  # 这一句没清缓存


# ---------------------------------------------------------------------------
# 4. 这条链只有一个答案
# ---------------------------------------------------------------------------


def test_a_model_override_on_one_row_leaves_the_other_23_rows_verbatim(root):
    """裁定 172 判据的原文形状：24 行名册（``l2_project`` 占 23 行），给 ``Aura`` 那一行写
    ``model:`` ⇒ **其余 23 行解析结果逐字不变**。只测「Aura 变了」不算，所以这里逐行数。

    这条同时过两条消费方，因为把覆盖折进 ``routes[entry.role]`` 那个做法伤的就是两条：

    ① 路由本身 —— :meth:`AgentRegistry.route_for` 按**那一行**解析，
       :meth:`AgentRegistry.router` 现在只剩「这一类默认用什么」；
    ② 配置生效值 —— ``load_config`` 读时叠的那条链（同一个 route_for 的产物）。

    以前这里是 ``router()`` 直接把每一行覆盖盖到 ``routes[role]`` 上、后写的赢 ⇒ 一次
    「给 Aura 单选」= 23 个 Agent 一起换，ADR-0013 完成定义第 1 条结构上不成立。
    """
    from plobi.agents.registry import AgentRegistry

    rows = {"secretary": {"name": "secretary", "role": "l1_secretary", "profile": "default",
                          "category": "butler"}}
    names = (["aura"] + [f"proj{n}" for n in range(23)])
    for name in names:
        (root / "profiles" / name).mkdir(parents=True, exist_ok=True)
        rows[name] = _row(name)
    _write_roster(root, rows)

    def snapshot() -> dict:
        reg = AgentRegistry.load()
        out = {}
        for name in names:
            entry = reg.get(name)
            out[name] = {
                "route": f"{reg.route_for(entry).provider}/{reg.route_for(entry).model}",
                "model": _effective_model(root / "profiles" / name),
            }
        return out

    before = snapshot()
    assert len({v["route"] for v in before.values()}) == 1, before  # 起点：23 行同一个类值

    rows = yaml.safe_load(Path(os.environ["PLOBI_PROJECTS_CONFIG"]).read_text(encoding="utf-8"))
    rows["agents"]["aura"]["provider"] = "moonshot"
    rows["agents"]["aura"]["model"] = "kimi-chosen"
    _write_roster(root, rows["agents"])

    after = snapshot()
    assert after["aura"]["route"] == "moonshot/kimi-chosen", after["aura"]
    assert after["aura"]["model"]["default"] == "kimi-chosen"
    changed = [n for n in names if after[n] != before[n]]
    assert changed == ["aura"], f"其余 23 行必须逐字不变，实际变了：{changed}"
    # 类表本身没被任何一行动过：`router()` 只回答「这一类默认用什么」
    from plobi.agents.registry import AgentRegistry as _AR

    class_route = _AR.load().router().resolve("l2_project")
    assert f"{class_route.provider}/{class_route.model}" == list(
        {v["route"] for k, v in before.items() if k != "aura"}
    )[0]


def test_the_low_level_resolver_gives_the_same_answer(root):
    """``plobi_constants.get_config_path()`` 与 ``plobi_cli.config.get_config_path()`` 同源。

    这条是本刀现查出来的坑，不是假想：底层那份以前是裸的
    ``get_plobi_home() / "config.yaml"``，而 ``agent/skill_utils.py``（技能可见性）、
    ``plobi_logging.py``、``plobi_time.py`` 用的都是它。读链只改 ``plobi_cli`` 那一格
    的话，分身一隔离就读不到 ``skills.external_dirs`` = **技能整个看不见**。
    """
    from plobi_constants import get_config_path as constants_get_config_path

    profile_home = root / "profiles" / "l2-agenda"
    os.environ["PLOBI_HOME"] = str(profile_home)
    _clear()

    assert constants_get_config_path() == cfg.get_config_path() == root / "config.yaml"


def test_skills_external_dirs_survive_the_isolation(root):
    """同上一条的下游证据：顶层那份绑了共享技能根之后，分身仍解析得到它。

    判据用被测代码自己的 :func:`agent.skill_utils.get_external_skills_dirs`，不手拼路径。
    """
    from agent.skill_utils import _external_dirs_cache_clear, get_external_skills_dirs

    shared = root / "skills"
    shared.mkdir()
    (root / "config.yaml").write_text(
        yaml.safe_dump(
            {"model": {"provider": "aigw", "default": "workbuddy/configured-once"},
             "skills": {"external_dirs": [str(shared)]}},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    profile_home = root / "profiles" / "l2-agenda"
    (profile_home / "config.yaml").write_text(
        "skills:\n  external_dirs: [" + str(profile_home / "skills") + "]\n", encoding="utf-8"
    )
    os.environ["PLOBI_HOME"] = str(profile_home)
    _clear()
    _external_dirs_cache_clear()
    try:
        assert get_external_skills_dirs() == [shared.resolve()]
    finally:
        _external_dirs_cache_clear()


# ---------------------------------------------------------------------------
# 5. 写侧那扇门：分身家不收配置，别处照旧
# ---------------------------------------------------------------------------


def test_a_write_targeted_at_a_profile_home_lands_in_the_one_config(root):
    """上游十几处手拼 ``<活跃家>/config.yaml`` 的开关（slash 命令、onboarding 那几枚标记……）
    在分身后端里跑时不再造复印件：``atomic_config_write`` 把它改道到那一份。

    没有这一条，"停读"只撑到用户第一次勾开关——那一勾会在家下重建一份 ``config.yaml``，
    份数读数当场回潮。
    """
    profile_home = root / "profiles" / "l2-agenda"

    cfg.atomic_config_write(profile_home / "config.yaml", {"onboarding": {"seen": {"x": True}}})

    assert not (profile_home / "config.yaml").exists()
    top = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    assert top["onboarding"]["seen"]["x"] is True
    assert top["model"]["default"] == "workbuddy/configured-once"  # 原来那节没被顶掉


def test_a_write_to_a_non_profile_home_stays_put(tmp_path):
    """负例：改道只认「那是个分身家」。把家指到普通目录（测试夹具、自定义 ``PLOBI_HOME``）
    时落点不许变——否则这一道门变成了到处改路。
    """
    plain = tmp_path / "somewhere-else"
    plain.mkdir()

    cfg.atomic_config_write(plain / "config.yaml", {"marker": {"kept": True}})

    assert (plain / "config.yaml").is_file()
    assert yaml.safe_load((plain / "config.yaml").read_text(encoding="utf-8"))["marker"]["kept"]
    assert not (tmp_path / "config.yaml").exists()
