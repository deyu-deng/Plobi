"""WP-L1-ROSTER（R-072）——L1 的 SOUL 模板里必须写着「你能问手下、该问谁、怎么问」。

用户 2026-10-07 的原话：「为什么它每次什么事情都去调用日程秘书呀……我希望的是只要
新建了 Agent，L2 配套的东西都自动建立好并登记好，L1 就可以看到一个名单，自己根据任务
决定问谁。」

核实过的根因不是硬编码：落到 L1 家的那份 SOUL（围栏以内那一段）通篇没提
``plobi_agent_ask`` 这张嘴，而旧文本反而把「日程 / 排天 / 待确认 / **项目进度**」全部
钉成 ``plobi_secretary_ask`` 的第一动作——模型照本宣科，于是什么事都去找日程秘书。

这里钉四件事：

1. L1 的 SOUL 合同有**模板文件**那一份真源（``plugins/plobi-north-star/l1_soul_template.md``），
   运行时不打补丁：改口令就改这个文件，``L1_SOUL_BLOCK`` 是它的读数。
2. 模板里写着「名单从哪来、什么时候问谁、怎么问、问不到怎么说」。
3. 日程秘书 ``agenda`` 与其他 L2 地位平等（裁定 69），模板不许把它当万事入口。
4. **生效路径**：已经存在的 L1 家在下一次启动时，围栏以内那一段整段换成新模板，
   围栏以外用户自己写的字一个字都不动（幂等，且不覆盖用户手改）。
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / ".plobi"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PLOBI_HOME", str(home))
    return home


def _soul(home):
    return (home / "SOUL.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# ① 模板文件是唯一真源
# ---------------------------------------------------------------------------


def test_l1_soul_block_is_read_from_the_template_file():
    from plobi.agents.registry import (
        L1_SOUL_BLOCK,
        L1_SOUL_TEMPLATE_PATH,
        load_l1_soul_template,
    )

    assert L1_SOUL_TEMPLATE_PATH.is_file(), "L1 SOUL 模板文件不见了（唯一真源）"
    raw = L1_SOUL_TEMPLATE_PATH.read_text(encoding="utf-8").replace("\r\n", "\n").strip()
    # 常量 = 模板文件读数，一处不增（运行时往提示词里另打一份补丁就是第二真源）。
    assert L1_SOUL_BLOCK == load_l1_soul_template()
    assert L1_SOUL_BLOCK.strip() == raw


def test_a_broken_template_fails_loud_instead_of_quietly_losing_the_contract(tmp_path):
    """围栏缺一道 / 顺序反了 → RegistryError，不能悄悄写出一份没围栏的 SOUL。"""
    import plobi.agents.registry as registry_mod
    from plobi.agents.registry import L1_SOUL_BEGIN, L1_SOUL_END, RegistryError

    original = registry_mod.L1_SOUL_TEMPLATE_PATH
    try:
        no_fence = tmp_path / "l1_soul_template.md"
        no_fence.write_text("## 只有正文没有围栏\n", encoding="utf-8")
        registry_mod.L1_SOUL_TEMPLATE_PATH = no_fence
        with pytest.raises(RegistryError):
            registry_mod.load_l1_soul_template()

        reversed_fence = tmp_path / "reversed.md"
        reversed_fence.write_text(
            f"{L1_SOUL_END}\n{L1_SOUL_BEGIN}\n", encoding="utf-8"
        )
        registry_mod.L1_SOUL_TEMPLATE_PATH = reversed_fence
        with pytest.raises(RegistryError):
            registry_mod.load_l1_soul_template()
    finally:
        registry_mod.L1_SOUL_TEMPLATE_PATH = original


# ---------------------------------------------------------------------------
# ② 模板教 L1 问手下
# ---------------------------------------------------------------------------


def test_template_names_the_mouth_that_asks_the_roster():
    from plobi.agents.registry import L1_SOUL_BLOCK

    assert "plobi_agent_ask" in L1_SOUL_BLOCK, "L1 的 SOUL 里还是没提这张嘴"
    # 名单从哪来 + 每一行的三格。
    assert "roster" in L1_SOUL_BLOCK
    assert "askable" in L1_SOUL_BLOCK
    assert "not_askable_because" in L1_SOUL_BLOCK
    assert "mind_candidates" in L1_SOUL_BLOCK
    # 什么时候问、怎么问、问几个。
    assert "手下名单与当面提问" in L1_SOUL_BLOCK
    for phrase in ("现在的实情", "用他的原话", "一次问一个"):
        assert phrase in L1_SOUL_BLOCK, phrase
    # 摘录不许冒充分身的回答。
    assert "冒充分身" in L1_SOUL_BLOCK


def test_template_stops_routing_everything_to_the_agenda_secretary():
    """旧文本那句「日程 / 项目 / 排天 / 待确认 第一动作只有 plobi_secretary_ask」
    是这次的答案：项目这一格必须从里头摘出去。"""
    from plobi.agents.registry import L1_SOUL_BLOCK

    assert "日程 / 项目 / 排天 / 待确认 **第一动作**只有 `plobi_secretary_ask`" not in L1_SOUL_BLOCK
    assert "日程 / 排天 / 待确认 / 项目进度：第一动作仍是已有的 `plobi_secretary_ask`" not in L1_SOUL_BLOCK
    # 但日程那一摊的入口没被顺手改掉（裁定 32 / 33 / 40 / 44 的合同还在）。
    assert "plobi_secretary_ask" in L1_SOUL_BLOCK
    assert "七个 intent 一个不增" in L1_SOUL_BLOCK


def test_template_puts_the_builtin_agenda_secretary_on_the_same_footing_as_other_l2():
    """裁定 69 的口径写进模板：agenda 只是系统自带的测试用 L2，不得特殊化。"""
    from plobi.agents.registry import L1_SOUL_BLOCK

    assert "日程秘书" in L1_SOUL_BLOCK
    assert "测试用 L2" in L1_SOUL_BLOCK
    assert "不得特殊化" in L1_SOUL_BLOCK
    assert "地位平等" in L1_SOUL_BLOCK


# ---------------------------------------------------------------------------
# ④ 落到已经存在的 L1 家：下一次启动换掉围栏以内那一段，围栏外不动
# ---------------------------------------------------------------------------


def test_existing_l1_home_upgrades_at_next_boot_and_keeps_user_text(isolated_home):
    from plobi.agents.registry import (
        L1_SOUL_BEGIN,
        L1_SOUL_BLOCK,
        L1_SOUL_END,
        ensure_l1_secretary_routing_soul,
    )

    # 一份「装机时写过的那版」SOUL：围栏以内是**旧**合同（没有 plobi_agent_ask），
    # 围栏以外是用户自己写的人设。
    old_contract = (
        f"{L1_SOUL_BEGIN}\n## 旧版\n\n- 项目进度：第一动作仍是已有的 `plobi_secretary_ask`。\n"
        f"{L1_SOUL_END}\n"
    )
    persona = "我自己写的人设：说话简短，别客套。\n"
    isolated_home.joinpath("SOUL.md").write_text(persona + "\n" + old_contract, encoding="utf-8")

    assert ensure_l1_secretary_routing_soul(home=isolated_home) is True

    text = _soul(isolated_home)
    # 新合同整段进来……
    assert L1_SOUL_BLOCK.strip() in text
    assert "plobi_agent_ask" in text
    # ……旧的那一段一个字都不留（否则一份 SOUL 里两段 L1 合同互相打脸）。
    assert "## 旧版" not in text
    assert text.count(L1_SOUL_BEGIN) == 1
    # 围栏以外用户写的字原样留着——这一刀不覆盖用户手改。
    assert "我自己写的人设：说话简短，别客套。" in text
    # 幂等：第二次启动什么都不必改。
    assert ensure_l1_secretary_routing_soul(home=isolated_home) is False
    assert _soul(isolated_home).count(L1_SOUL_END) == 1


def test_boot_path_is_the_backend_startup_hook_not_a_chat_time_patch():
    """:func:`ensure_l1_secretary_form` 是启动那一步的写入方（web_server lifespan），
    它把模板落到 default profile 自己的家；聊天时不打补丁。"""
    import inspect

    from plobi.agents import registry as registry_mod

    src = inspect.getsource(registry_mod.ensure_l1_secretary_form)
    assert "ensure_l1_secretary_routing_soul" in src
    # 只在 default profile 那一把椅子上写（裁定 42 §6(b)）。
    assert 'get_active_profile_name() != "default"' in src
