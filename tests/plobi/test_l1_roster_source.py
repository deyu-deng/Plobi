"""R-070 / R-072 —— 「我手下有谁」看名册上盖的那一章，不看 description 的长相。

用户 2026-10-07 的原话：「为什么它每次什么事情都去调用日程秘书呀……我希望的是只要
新建了 Agent，L2 配套的东西都自动建立好并登记好，L1 就可以看到一个名单。」

裁定 69 把基本单位定成 Agent，于是：

* ``roster`` = ``AgentEntry.registered`` 为真的行——新建 / 登记那一步盖过章的手下；
* ``mind_candidates`` = 播种机投影下来的占位（``registered=False``）；

两类必须**分开**，不许混成一个名单让秘书照着念——但不许为了分类把任何一条偷偷丢掉。
R-072 要修的是**判据**：以前 ``is_mind_placeholder`` 拿 ``description`` 的前缀猜
「这句是不是用户自己写的」，那句话一抖（用户补了说明、播种机多缀一段 raw 名、措辞
改了）手下就从名单里消失。现在判据是盘上一格明确的布尔，猜不抖。
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def isolated_registry(tmp_path, monkeypatch):
    """一个指向临时 projects.yaml、且 Mind 探不到的隔离名册。"""
    from plobi.agents import registry as registry_mod

    monkeypatch.delenv("PLOBI_PROJECTS_CONFIG", raising=False)
    # MIND_ROOT 指一个不存在的目录 → resolve_root 返回 None → 播种机不落任何 Mind 占位，
    # 于是名册里的「谁配了 / 谁没配」完全由本测试亲手摆的那几条决定。
    monkeypatch.setenv("MIND_ROOT", str(tmp_path / "no-such-mind"))
    return registry_mod.AgentRegistry.load(tmp_path / "projects.yaml")


def _registered(registry, name: str, *, description: str = "") -> None:
    """一条**盖过「已登记」章**的分身（→ roster）。"""
    from plobi.agents.registry import AgentEntry

    registry.agents[name] = AgentEntry(
        name=name,
        role="l2_project",
        category="projects",
        description=description or f"{name} 的真实项目说明",
        registered=True,
    )


def _mind_seed(registry, name: str, *, description: str | None = None) -> None:
    """一条播种机落下的投影（→ mind_candidates），章盖的是 ``False``。"""
    from plobi.agents.registry import AgentEntry, L2_PLACEHOLDER_DESCRIPTION_PREFIX

    registry.agents[name] = AgentEntry(
        name=name,
        role="l2_project",
        category="projects",
        description=(
            f"{L2_PLACEHOLDER_DESCRIPTION_PREFIX}{name}" if description is None else description
        ),
        registered=False,
    )


def test_configured_and_candidates_land_in_separate_lists(isolated_registry):
    from plobi.agents.registry import run_project_status

    _registered(isolated_registry, "Nymo")
    _mind_seed(isolated_registry, "Anim")
    _mind_seed(isolated_registry, "Framelet")

    payload = run_project_status("各项目怎么样了", registry=isolated_registry)

    assert payload["roster"] == ["Nymo"]
    assert set(payload["mind_candidates"]) == {"Anim", "Framelet"}
    # 候选不许混进 roster（用户质疑的正是「Mind 项目被当成手下」）。
    assert not set(payload["roster"]) & set(payload["mind_candidates"])


def test_roster_and_candidates_never_lose_a_reported_project(isolated_registry):
    """分成两格是标注，不是丢弃：报出来的每条项目要么在 roster、要么在候选，一个都不少。"""
    from plobi.agents.registry import run_project_status

    _registered(isolated_registry, "Nymo")
    _registered(isolated_registry, "Schedule")
    _mind_seed(isolated_registry, "Anim")

    payload = run_project_status("手下有谁", registry=isolated_registry)

    reported = {row["id"] for row in payload["projects"]}
    assert set(payload["roster"]) | set(payload["mind_candidates"]) == reported
    # 每行自带 registered 旗标，两类各归其位。
    for row in payload["projects"]:
        assert row["registered"] == (row["id"] in payload["roster"])


def test_nothing_registered_yields_empty_roster_not_the_candidates(isolated_registry):
    """用户一个分身都没登记时，roster 必须是空的——绝不能把整堆候选当手下念。"""
    from plobi.agents.registry import run_project_status

    _mind_seed(isolated_registry, "Anim")
    _mind_seed(isolated_registry, "Framelet")

    payload = run_project_status("我手下有谁", registry=isolated_registry)

    assert payload["roster"] == []
    assert set(payload["mind_candidates"]) == {"Anim", "Framelet"}


# ---------------------------------------------------------------------------
# R-072：判据是那一格布尔，不是那句文本
# ---------------------------------------------------------------------------


def test_membership_reads_the_stamp_never_the_description(isolated_registry):
    """章说了算手下就算手下——哪怕它的描述长得像播种机的占位句。

    这一条就是旧判据会做错的地方：``description`` 以
    ``L2_PLACEHOLDER_DESCRIPTION_PREFIX`` 开头曾被当成「不是用户配的」，于是
    用户自己登记、只是没重新写过说明的分身会从名单里消失。
    """
    from plobi.agents.registry import L2_PLACEHOLDER_DESCRIPTION_PREFIX, run_project_status

    _registered(isolated_registry, "Nymo", description=f"{L2_PLACEHOLDER_DESCRIPTION_PREFIX}Nymo")
    _mind_seed(isolated_registry, "Aura", description="Aura 真正在做的事（用户写过的说明）")

    payload = run_project_status("我手下有谁", registry=isolated_registry)

    assert payload["roster"] == ["Nymo"]
    assert payload["mind_candidates"] == ["Aura"]


def test_the_description_guess_is_gone_from_every_judgement():
    """R-072 拆的是「拿那句占位话当判据」：成员资格与可问性都不许读 ``description``。

    占位句的**识别**留着，但它只服务渲染层——分身身份里那句「这个项目在做的事」不许
    印播种机自己写的备注（钉在 ``tests/plobi/test_master_tools.py``）。判据与渲染是
    两件事，这里钉的就是它们不许互相串：旧的那个对象级兼容垫片照样彻底不许有。
    """
    import inspect

    import plobi.agents.registry as registry_mod

    assert not hasattr(registry_mod.AgentEntry, "is_mind_placeholder")

    # 两道判据：既不认占位前缀，也不引用那份只为渲染存在的识别。
    for fn in (registry_mod.agent_askability, registry_mod._profile_landed):
        src = inspect.getsource(fn)
        assert "description" not in src, fn.__name__
        assert "L2_PLACEHOLDER_DESCRIPTION_PREFIX" not in src, fn.__name__
        assert "_is_placeholder_project_description" not in src, fn.__name__
    src = inspect.getsource(registry_mod.run_project_status)
    assert "L2_PLACEHOLDER_DESCRIPTION_PREFIX" not in src
    assert "_is_placeholder_project_description" not in src
    assert "entry.registered" in src  # 成员资格读的是那一格章

    # 渲染层用的识别与**生成桩**同源（认常量，不抄字面量——抄一遍就会漂）。
    assert registry_mod._is_placeholder_project_description(
        f"{registry_mod.L2_PLACEHOLDER_DESCRIPTION_PREFIX}Aura"
    ) is True
    render_src = inspect.getsource(registry_mod.build_l2_identity_soul_block)
    assert "_is_placeholder_project_description" in render_src
    assert '"l2_project for Mind project' not in render_src


def test_askability_never_flips_because_of_the_description_text(monkeypatch):
    """同一颗家、两句不同的说明：可问性必须一模一样——那句话长什么样不算数。"""
    import plobi_cli.profiles as profiles_mod

    from plobi.agents.registry import (
        L2_PLACEHOLDER_DESCRIPTION_PREFIX,
        AgentEntry,
        agent_askability,
    )

    monkeypatch.setattr(profiles_mod, "profile_exists", lambda name: True)

    stamped_by_seeder = AgentEntry(
        name="nymo",
        role="l2_project",
        category="projects",
        description=f"{L2_PLACEHOLDER_DESCRIPTION_PREFIX}Nymo",
    )
    written_by_human = AgentEntry(
        name="stithy",
        role="l2_project",
        category="projects",
        description="从零手写编程语言系统",
    )
    assert agent_askability(stamped_by_seeder) == (True, "")
    assert agent_askability(written_by_human) == agent_askability(stamped_by_seeder)


def test_ask_side_has_no_description_heuristic_either():
    """``plobi_agent_ask`` 那侧也不许留一份文本判据（名单与嘴同源）。"""
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    source = (repo / "plugins" / "plobi-north-star" / "agent_ask.py").read_text(
        encoding="utf-8"
    )
    assert "is_mind_placeholder" not in source
    assert "L2_PLACEHOLDER_DESCRIPTION_PREFIX" not in source
    # 问不到的行不是「查无此人」：拒答与筛名单用的是同一份结构性判据。
    assert "agent_askability" in source


# ---------------------------------------------------------------------------
# 那一格落在盘上：yaml 里看得见、读回来不靠猜
# ---------------------------------------------------------------------------


def test_registered_roundtrips_through_yaml_and_is_always_written(tmp_path):
    from plobi.agents.registry import AgentEntry, AgentRegistry

    reg = AgentRegistry.load(tmp_path / "projects.yaml")
    _registered(reg, "Nymo")
    _mind_seed(reg, "Anim")
    reg.save()

    raw = (tmp_path / "projects.yaml").read_text(encoding="utf-8")
    # 永远写出来（和 category 同一口径）：读 yaml 的人要看得见这一格，不靠反推。
    assert "registered: true" in raw
    assert "registered: false" in raw

    again = AgentRegistry.load(tmp_path / "projects.yaml")
    assert again.get("Nymo").registered is True
    assert again.get("Anim").registered is False


def test_unstamped_legacy_row_stays_on_the_roster(tmp_path):
    """老数据没盖过章 = 按「登记过」读：宁可多列一行，不许把手下悄悄藏掉。"""
    from plobi.agents.registry import AgentEntry, AgentRegistry

    (tmp_path / "projects.yaml").write_text(
        "version: 1\nagents:\n  Nymo:\n    role: l2_project\n    category: projects\n"
        "    description: l2_project for Mind project Nymo\n",
        encoding="utf-8",
    )
    reg = AgentRegistry.load(tmp_path / "projects.yaml")
    assert reg.get("Nymo").registered is True
    assert AgentEntry.from_dict("X", {"role": "l2_project"}).registered is True


def test_a_fabricated_stamp_is_refused_instead_of_guessed(tmp_path):
    """``registered: maybe`` 不许被猜成真的或假的了——直接报错。"""
    from plobi.agents.registry import AgentEntry, RegistryError

    with pytest.raises(RegistryError):
        AgentEntry.from_dict("X", {"role": "l2_project", "registered": "maybe"})
    # 明确写出来的假话就当真话读，不因为描述像人写的就翻案。
    assert AgentEntry.from_dict("X", {"registered": "no"}).registered is False
