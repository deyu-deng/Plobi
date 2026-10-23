"""R-090 / 裁定 143② —— 一门课要成为「有嘴可问」的 L2，而不是名册里点不动的一行。

用户 10-15 的原话就是判据：「**课程Agent还是不能点击**」「**每门课一个 L2 Agent**」。
触发它的实况有两层：名册里 5 条课程条目的 ``profile_name`` 全为空 ⇒ 桌面前端
``isNavigableAgent``（只看 profile 非空）把它们整栏锁死；而 ``plobi_agent_ask`` 的名单
按 ``ASKABLE_ROLE`` 筛，``l2_course`` 不在里面 ⇒ L1 也问不到（裁定 145①(b) 却要求
「那一摊必须派」）。

钉的是**设计里那条路**（``plobi/routing/models.py:35-40`` 自己写的：真要给它一个运行时，
就把角色改成 ``l2_project``），不是新造标志位：

* 一条 ``category: courses`` 的记录，**声明了** ``profile`` 就同时得到：非空
  ``profile_name``（前端可点）、在 ``ASKABLE_ROLE`` 里（L1 可问）、该角色有模型路由
  （真能跑）——三件事必须一起成立，缺一条就是二等公民；
* **没声明** profile 的记录，``profile_name`` 必须继续回空串：那正是
  ``WP-DESKTOP-POOL-GHOST-PROFILE`` 修掉的形状（空 profile 冒充一格 profile ⇒
  桌面为它起 ``plobi -p <课名>``，非 ASCII 名被 CLI 预扫丢掉退 2，每一门都是一场
  无限重起，10-13 现核单「电工电子学」2767 行 failed）。

名册一律落在 tmp 里，不碰用户 ``PLOBI_HOME`` 下那份真名册。
"""

from __future__ import annotations

import pytest

from plobi.agents.registry import (
    ASKABLE_ROLE,
    AgentEntry,
    AgentRegistry,
    agent_askability,
    is_askable_agent,
    profile_name_blocker,
)
from plobi.routing.models import DEFAULT_ROUTES, L2_COURSE, L2_PROJECT

COURSE = "大学物理（甲）Ⅱ"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.delenv("PLOBI_PROJECTS_CONFIG", raising=False)
    monkeypatch.setattr(
        "plobi.agents.registry.default_path", lambda: tmp_path / "projects.yaml"
    )
    return tmp_path


def _course_entry(**over) -> AgentEntry:
    data = {
        "name": COURSE,
        "role": L2_PROJECT,          # 有运行时的那条路（models.py 注释指定的改法）
        "category": "courses",       # 类型只决定附加设置（裁定 146②(c)）
        "profile": "l2-course-phys",  # 显式 ASCII 名：非 ASCII 会被 CLI 的 -p 预扫丢掉
    }
    data.update(over)
    return AgentEntry(**data)


def test_declared_course_entry_gets_a_nonempty_profile_name():
    """声明了 profile 的课 ⇒ 前端那句「能不能点」的谓词拿到非空（R-090 的可点半边）。"""
    entry = _course_entry()

    assert entry.profile_name == "l2-course-phys"
    assert entry.profile_name.strip()  # isNavigableAgent 在 JS 侧判的就是这个


def test_undeclared_entry_keeps_profile_name_empty_so_no_ghost_sidecar():
    """没声明 profile ⇒ 继续回空串。这是负例：空串挡着「冒充一格 profile 起后端」。"""
    entry = AgentEntry(name="电工电子学", role=L2_COURSE, category="courses")

    assert entry.profile_name == ""
    assert profile_name_blocker(entry) == ""  # 数据条目没有家不是缺陷，别报错
    assert not is_askable_agent(entry)


def test_course_role_is_the_one_the_ask_list_and_router_both_accept():
    """可问性与路由必须一起成立：L1 问得到、且那个角色解析得到模型。"""
    entry = _course_entry()

    assert entry.role == ASKABLE_ROLE
    assert entry.role in DEFAULT_ROUTES
    assert profile_name_blocker(entry) == ""


def test_a_missing_home_no_longer_changes_askability(tmp_path, monkeypatch):
    """裁定 192（10-23）：家不在家在盘上，**不再**改变可问性。

    原来这一条钉的是「家没落地时要指『去登记/新建一次』」；那把尺把 L1 的名单绑在
    `profiles/<name>` 存在上，10-23 11:36 一次整屋搬家因此把 24 行左栏全打成点不动。
    现在身份只看名册这一行（角色有没有路由 + registered 那一格章）。
    """
    entry = _course_entry()

    assert not (tmp_path / "profiles" / entry.profile_name).exists()  # 前提：盘上没有这个家
    askable, reason = agent_askability(entry)

    assert askable is True
    assert reason == ""
    assert L2_COURSE not in reason
