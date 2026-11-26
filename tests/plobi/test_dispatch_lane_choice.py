"""名手与客席的选路判据（11-25）。

现场：首席拿到一块活时不知道该派名手还是开客席。根因不在模型，在提示本身——
``delegate_task`` 的描述写着四类活 HARD RULE 派专家，``external_offload_run``
的描述写着"默认先想到它"，两边的触发条件（「一整块能独立验收的活」「写文件/
跑脚本/查资料出成品」）几乎逐字重叠。两条互斥的强指令同时在场，模型只能随机挑
一条，最坏是两条都不用、自己上手。

改法：不问「谁更好」，只问「名手够不够得着」——名手是默认劳动力，客席是够不着
时的特例。这几条断言就是那个定序，防止后续有人把客席改回"默认"。
"""

from __future__ import annotations


def test_offload_is_not_the_default_lane():
    """客席那条工具描述不许再自称默认。"""
    from tools.offload_tools import RUN_SCHEMA

    desc = RUN_SCHEMA["description"]
    assert "默认先想到它" not in desc, "客席又被写成默认了——和名手的 HARD RULE 打架"
    assert "它不是默认选项" in desc
    # 三种「名手够不着」的硬证要写全，否则模型手上没有可判的东西
    assert "登录态或账号" in desc
    assert "独家能力" in desc
    assert "接着那条会话" in desc
    # 用户点名某台软件是例外，不能被这条判据吞掉
    assert "点名" in desc


def test_virtuoso_lane_is_declared_the_default_owner():
    """名手那条描述要明确自己是这四类活的默认归属方，并说清何时让位客席。"""
    from tools.delegate_tool import _build_top_level_description

    desc = _build_top_level_description()
    assert "HARD RULE" in desc, "四类活的硬规则被删了"
    assert "DEFAULT owner" in desc
    assert "external offload" in desc
    assert "cannot reach it" in desc
    # 两边都能干时选名手的理由要写明，不然模型没有取舍依据
    assert "retried with different parameters" in desc


def test_principal_soul_carries_the_ordering():
    """首席的灵魂块里必须带着这条定序判据（它每轮都读，优先级高于工具描述）。"""
    import inspect

    from plobi.agents import registry

    src = inspect.getsource(registry)
    assert "名手是默认，客席是特例" in src
    assert "也优先外包" not in src, "旧的「两条都优先」还在，判据又被抹平了"
    # 止损：客席一趟失败不重派，名手可重试
    assert "别派第二趟" in src
