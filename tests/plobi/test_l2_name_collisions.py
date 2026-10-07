"""R-071 —— 只差大小写的分身名撞同一个 profile 家 / 保留名撞车（用户实测）。

钉的是三条不变量，不钉具体名单：

* 登记新键不许撞上只差大小写的已有键（那等于给同一个家登记两条，秘书谁也问不到）；
  但**读**已成对的存量键、改它自己的字段不许报错——本刀要的是不再新增，不是替用户改数据。
* 一个名字归到「同一个家」的多条命中时，``agent_ask`` 要收敛成一条，而不是报歧义。
* profile 名撞上保留名（分身叫 ``Plobi`` → 小写就是保留的 ``plobi``）时，错误必须
  能行动：说清是哪条、哪一格、改成什么，而不是把底座那句 reserved 原样丢给人。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _load_agent_ask():
    """Load ``plugins/plobi-north-star/agent_ask.py`` by file path.

    The directory name is hyphenated, so it can't be imported by dotted name;
    ``resolve_target`` / ``_collapse_same_home`` are the pure functions this
    file needs and pull no plugin-package surface at module load.
    """
    name = "plobi_test_r071_agent_ask"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, REPO / "plugins" / "plobi-north-star" / "agent_ask.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


AA = _load_agent_ask()


@pytest.fixture()
def fresh_registry(tmp_path, monkeypatch):
    """一个只读 tmp 家的名册，而且**它的 profile 家也钉在 tmp 上**。

    ``agent_askability``（R-072 那份唯一判据）要看盘上有没有这个家，所以家不钉进来的
    话，测试摆的每一行都是「还没落地」，``resolve_target`` 连撞名那一段都走不到——
    这条钉法与 ``tests/plobi/test_agent_ask.py`` 的 ``roster`` 同一套（profile 根按
    HOME 锚定，见 AGENTS.md §Profiles 规则 6）。
    """
    from plobi.agents import registry as registry_mod

    monkeypatch.delenv("PLOBI_PROJECTS_CONFIG", raising=False)
    plobi_home = tmp_path / ".plobi"
    plobi_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("PLOBI_HOME", str(plobi_home))
    return registry_mod.AgentRegistry.load(tmp_path / "projects.yaml")


def _land(tmp_path, *names: str) -> None:
    """把这几个 profile 家真的落到盘上——「登记 / 新建过」的证据。"""
    from plobi_cli.profiles import normalize_profile_name

    for name in names:
        (tmp_path / ".plobi" / "profiles" / normalize_profile_name(name)).mkdir(
            parents=True, exist_ok=True
        )


def _project(name: str, *, description: str = "用户自己写的一句话", registered: bool = True):
    from plobi.agents.registry import AgentEntry

    return AgentEntry(
        name=name,
        role="l2_project",
        category="projects",
        description=description,
        registered=registered,
    )


# --------------------------------------------------------------------------- #
# 登记规则：新键不许只差大小写地撞上已有键
# --------------------------------------------------------------------------- #


def test_find_case_twin_spells_the_collision(fresh_registry):
    fresh_registry.agents["Nymo"] = _project("Nymo", description="用户写的 Nymo 分身")
    # 另一个大小写写法就是它的孪生；原键自己不和自己的孪生；没登记过的没有孪生。
    assert fresh_registry.find_case_twin("nymo") == "Nymo"
    assert fresh_registry.find_case_twin("Nymo") == ""
    assert fresh_registry.find_case_twin("Other") == ""
    assert fresh_registry.find_case_twin("") == ""


def test_new_key_colliding_only_by_case_is_rejected_without_adding_a_row(fresh_registry):
    from plobi.agents.registry import RegistryError

    fresh_registry.upsert(_project("Nymo"))
    with pytest.raises(RegistryError) as exc:
        fresh_registry.upsert(_project("nymo"))
    # 报错要能行动：两条键都得点名，让人知道撞了谁。
    message = str(exc.value)
    assert "Nymo" in message and "nymo" in message
    # 被拒的那条不许偷偷落进名册——名册仍是只有一条。
    assert set(fresh_registry.agents) == {"Nymo"}


def test_a_genuinely_different_name_is_still_allowed(fresh_registry):
    fresh_registry.upsert(_project("Nymo"))
    fresh_registry.upsert(_project("Framelet"))  # 只差大小写以外，正常登记
    assert {"Nymo", "Framelet"} <= set(fresh_registry.agents)


def test_reading_an_existing_case_pair_and_editing_its_own_row_still_work(fresh_registry):
    """存量家已经躺着一对只差大小写的记录——读它 / 改它不许报错（本刀不替用户改数据）。"""
    from plobi.agents.registry import RegistryError

    # 绕过 upsert 的新键闸门，模拟用户 projects.yaml 里已有的成对键。
    fresh_registry.agents["Nymo"] = _project("Nymo")
    fresh_registry.agents["nymo"] = _project("nymo", description="")
    assert fresh_registry.find_case_twin("Nymo") == "nymo"

    # 改已有键（键相同）不撞闸门：哪怕它有个大小写孪生躺在旁边。
    edited = _project("Nymo", description="用户又改了一次")
    fresh_registry.upsert(edited)  # 不抛 RegistryError
    assert fresh_registry.get("Nymo").description == "用户又改了一次"

    # 存盘 / 读盘也不该把这对键合并成一条（真实家只读，这里只验机制）。
    path = fresh_registry.save()
    from plobi.agents.registry import AgentRegistry

    reloaded = AgentRegistry.load(path)
    assert {"Nymo", "nymo"} == set(reloaded.agents)


# --------------------------------------------------------------------------- #
# 归一：同一个 profile 家的多条命中收敛成一条，而不是报歧义
# --------------------------------------------------------------------------- #


def test_same_home_hits_collapse_to_one_row(fresh_registry, tmp_path):
    from plobi.agents.registry import L2_PLACEHOLDER_DESCRIPTION_PREFIX

    # 同一个家（小写 profile 名都是 nymo）两条：用户登记过的那条 + 播种机投影那条。
    # 投影那条盖的是 ``registered=False``——这就是播种机实际写进盘上的那一格
    # （``ensure_mind_project_agents``），「优先用户自己那条」靠的是这个章，
    # 不是靠读它那句话说得像机器话（R-072 拆掉的就是后者）。
    configured = _project("Nymo", description="用户的 Nymo 项目分身")
    placeholder = _project(
        "nymo",
        description=f"{L2_PLACEHOLDER_DESCRIPTION_PREFIX}Nymo",
        registered=False,
    )
    fresh_registry.agents["Nymo"] = configured
    fresh_registry.agents["nymo"] = placeholder
    _land(tmp_path, "Nymo")  # 两条落到同一个家，所以一起算落地

    # 问 "nymo" / "Nymo" 都只该对上一个家 → 一条，优先用户自己那条。
    for asked in ("nymo", "Nymo", "NYMO"):
        resolved = AA.resolve_target(asked, registry=fresh_registry)
        assert resolved.name == "Nymo"


def test_genuinely_different_homes_stay_ambiguous(fresh_registry, tmp_path):
    """两条命中来自两个**不同**的家（profile 名不同）→ 仍该报歧义，且名单带上能拿去改的注册键。"""
    from plobi.agents.registry import AgentEntry

    a = AgentEntry(
        name="alpha", role="l2_project", category="projects",
        display_name="共用人名", description="project",
    )
    b = AgentEntry(
        name="beta", role="l2_project", category="projects",
        display_name="共用人名", description="project",
    )
    fresh_registry.agents["alpha"] = a
    fresh_registry.agents["beta"] = b
    _land(tmp_path, "alpha", "beta")  # 两个都问得到，撞名才有得判

    with pytest.raises(AA.AskRefusal) as exc:
        AA.resolve_target("共用人名", registry=fresh_registry)
    who = str(exc.value)
    assert "alpha" in who and "beta" in who


# --------------------------------------------------------------------------- #
# 保留名撞车：给能行动的错误（告诉他改什么），别只报 reserved
# --------------------------------------------------------------------------- #


def test_reserved_home_name_yields_an_actionable_blocker():
    from plobi.agents.registry import profile_name_blocker, suggested_profile_name

    entry = _project("Plobi")  # 小写化就是保留名 plobi
    blocker = profile_name_blocker(entry)
    assert blocker
    assert "Plobi" in blocker  # 点名是哪条分身
    assert "reserved" in blocker.lower()  # 说清撞了什么
    suggestion = suggested_profile_name(entry)
    assert suggestion and suggestion in blocker  # 给一个能填的替代名

    # 正常名字不该被误判成建不出家。
    assert profile_name_blocker(_project("nymo")) == ""


def test_agent_ask_refuses_a_分身_whose_home_can_never_be_created(fresh_registry):
    # 分身登记了，但它的家永远建不出来（撞保留名）→ 当面问要拒，并给能行动的话。
    fresh_registry.agents["Plobi"] = _project("Plobi")
    with pytest.raises(AA.AskRefusal) as exc:
        AA.resolve_target("Plobi", registry=fresh_registry)
    message = str(exc.value.message)
    assert "reserved" in message.lower() and "Plobi" in message
