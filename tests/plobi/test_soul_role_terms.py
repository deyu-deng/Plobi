"""裁定 220／223：四个角色名要进**模型读到的那份文字**，代号不许再当称呼。

这一层以前是空的：界面早已统一（``retired-words.test.ts`` 管屏上），代码标识符
也收过口（``WP-TERMS-FOUR-WORDS``），但 SOUL 围栏里仍写着「你是本机 Plobi 指挥
（L1）」「派给对应项目的 L2（它再管 L3）」——模型读到的是代号，于是会对用户说
「我是 L1」。用户 11-15 14:4x 问「所有 Agent 都知道我们最新的统一命名了吗」，
本档把答案钉成可复跑的判据。

边界（写在裁定 223）：**清的是「当称呼用的代号」**，不是把字符 `L1` 从文件里
抹掉。三处刻意保留：① WP 编号（`WP-L1-ROSTER` 这类档案件名）；② 兼容旧问法
「你是 L1 吗」——用户还会这么问，指挥得听懂；③ 「不说 `L1 / L2 / L3`」这句
禁令本身。
"""

from __future__ import annotations

from types import SimpleNamespace

from plobi.agents.registry import CONDUCTOR_SOUL_BLOCK, build_principal_identity_soul_block
from plobi.delegation.expert_roles import DUTIES

ROLES = ("指挥", "首席", "名手", "客席")


def _one_l2_block() -> str:
    # 测试环境的名册是隔离的空库；生成器只读 entry 的字段，给一个最小替身即可，
    # 免得这条判据依赖用户家里恰好有一行 Agent。
    entry = SimpleNamespace(
        name="probe",
        display_name="探针",
        project_path=None,
        mind_subtree=None,
        registered=False,
        description="",
        archived=False,
    )
    return build_principal_identity_soul_block(entry)


def test_l1_fence_names_all_four_roles_and_says_they_are_not_ranks():
    """指挥的 SOUL 必须同时给四个名字，并明写「角色名不是等级」（裁定 220 边界①）。"""
    for role in ROLES:
        assert role in CONDUCTOR_SOUL_BLOCK, f"L1 围栏里缺角色名：{role}"
    assert "角色名，不是等级" in CONDUCTOR_SOUL_BLOCK
    assert "不说 `L1 / L2 / L3` 这类内部代号" in CONDUCTOR_SOUL_BLOCK


def test_l1_fence_dropped_codes_used_as_names():
    """当称呼用的代号逐条清掉；删任何一句都不该悄悄回来。"""
    for stale in (
        "Plobi 指挥（L1）",
        "不是** 日程 L2",
        "派给对应项目的 L2",
        "（它再管 L3）",
        "是 L1 自己动的那一摊",
        "登记 L2 Agent",
        "**L2 Agent**",
    ):
        assert stale not in CONDUCTOR_SOUL_BLOCK, f"旧代号仍当称呼用：{stale}"


def test_l1_still_understands_a_user_asking_with_the_old_code():
    """兼容是刻意的：用户还会问「你是 L1 吗」，指挥得听懂、但回答不跟着念。"""
    assert "你是 L1 吗" in CONDUCTOR_SOUL_BLOCK
    assert "你是指挥吗" in CONDUCTOR_SOUL_BLOCK
    assert "不要跟着念" in CONDUCTOR_SOUL_BLOCK


def test_l2_fence_names_its_role_without_the_parenthesized_code():
    block = _one_l2_block()
    assert "你的**角色**叫**首席**" in block
    assert "统筹全局那位叫**指挥**" in block
    assert "**名手**" in block and "**客席**" in block
    assert "角色名，不是等级" in block
    assert "不说 `L1 / L2 / L3` 这类内部代号" in block
    assert "Plobi 指挥（L1）" not in block
    assert "归 L1（" not in block


def test_l3_duties_open_with_the_role_name_not_the_object_name():
    """裁定 220 第 4 条：`专家` 是对象名（左栏抽屉），`名手` 是这一跳的身份。"""
    assert set(DUTIES) == {"file", "browser", "software", "computer"}
    for key, duty in DUTIES.items():
        assert duty.startswith("你是名手（"), f"{key} 的职责句没报角色名：{duty[:30]}"
        assert "专家：" not in duty, f"{key} 又退回旧自称：{duty[:30]}"
