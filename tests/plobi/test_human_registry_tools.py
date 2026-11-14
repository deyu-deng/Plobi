"""人读面那 17 把工具是**注册表工具**，provider 只管脑页（WP-HUMAN-REGISTRY-TOOLS）。

用户 11-14 拍「provider 只管脑页」。这一档钉住四件事：

  ① **provider 只剩脑页那两把**——``get_tool_schemas()`` 里只有 ``mind_read``／
     ``mind_write``；
  ② 人读面那 17 把**在注册表里**，且各归各的门类（note／diary／dossier）；
  ③ **下发路换了**：注册表工具走 ``model_tools.get_tool_definitions``，不再是
     provider 在 agent init 之后追加 ⇒ **门类关掉就真没有**；
  ④ **provider 不再处理人读面那几把**（明确说「不处理这把」，不是装作读不到）——
     而且**它们与 provider 换不换无关**：provider 是可换的（``memory.provider``
     可以指别家第三方），人读面是产品本体，不该跟着一起消失。

行为一个字没改：schema、返回、闸、门类默认开，全部照旧（判据见同刀的纸）。
"""

from __future__ import annotations

import json

import pytest

# 注册表工具靠**导入**自己登记（``tools/registry.py`` 那套约定）⇒ 这一档先把
# 人读面那三个模块拉进来，后面那些判据查的就是真注册表。
from tools import human_diary_tools, human_dossier_tools, human_note_tools  # noqa: F401

NOTE_TOOLS = {
    "note_read",
    "note_write",
    "note_rename",
    "note_delete",
    "agent_notes",
    "note_list",
    "note_links",
    "note_set_property",
}
DIARY_TOOLS = {"diary_read", "diary_write", "diary_list", "diary_append"}
DOSSIER_TOOLS = {
    "dossier_read",
    "dossier_write",
    "dossier_list",
    "dossier_task_toggle",
    "dossier_stage_update",
}
HUMAN_TOOLS = NOTE_TOOLS | DIARY_TOOLS | DOSSIER_TOOLS
MIND_TOOLS = {"mind_read", "mind_write"}


def test_the_provider_only_offers_the_brain_tools():
    """① provider 只剩脑页那两把。"""
    from plobi.mind.provider import MindProvider

    names = {s["name"] for s in MindProvider().get_tool_schemas()}
    assert names == MIND_TOOLS, names


def test_the_provider_no_longer_handles_the_human_side_tools():
    """④ provider 明确说「不处理这把」——不是装作读不到。"""
    from plobi.mind.provider import MindProvider

    provider = MindProvider()
    result = json.loads(provider.handle_tool_call("note_read", {"path": "Notes/x.md"}))
    assert result["success"] is False
    assert "does not handle" in result["error"], result


def test_every_human_side_tool_is_registered_under_its_own_category():
    """② 人读面那 17 把在注册表里，各归各的门类。"""
    from tools.registry import registry

    for name in NOTE_TOOLS:
        assert registry.get_toolset_for_tool(name) == "note", name
    for name in DIARY_TOOLS:
        assert registry.get_toolset_for_tool(name) == "diary", name
    for name in DOSSIER_TOOLS:
        assert registry.get_toolset_for_tool(name) == "dossier", name
    # 注册表里**没有** provider 门类那套了：脑页那两把仍归 provider。
    for name in MIND_TOOLS:
        assert registry.get_toolset_for_tool(name) is None, name


def test_the_human_side_tools_reach_the_agent_through_the_registry():
    """③ 下发路换了：门类开着就在，关掉就真没有。"""
    from model_tools import get_tool_definitions

    on = {
        t["function"]["name"]
        for t in get_tool_definitions(
            enabled_toolsets=["note", "diary", "dossier"],
            quiet_mode=True,
            skip_tool_search_assembly=True,
        )
    }
    assert HUMAN_TOOLS.issubset(on), sorted(HUMAN_TOOLS - on)

    off = {
        t["function"]["name"]
        for t in get_tool_definitions(
            enabled_toolsets=["todo"],
            quiet_mode=True,
            skip_tool_search_assembly=True,
        )
    }
    assert not (HUMAN_TOOLS & off), sorted(HUMAN_TOOLS & off)


def test_the_human_side_tools_do_not_depend_on_a_loaded_provider():
    """**这一刀的根**：provider 是可换的 ⇒ 人读面的工具不该跟着 provider 一起消失。

    判据：注册表里那 17 把**不需要**挂任何 provider 实例就能下发
    （``get_tool_definitions`` 不经过 ``inject_memory_provider_tools``）。
    """
    from model_tools import get_tool_definitions

    names = {
        t["function"]["name"]
        for t in get_tool_definitions(
            enabled_toolsets=["note"],
            quiet_mode=True,
            skip_tool_search_assembly=True,
        )
    }
    assert NOTE_TOOLS.issubset(names), sorted(NOTE_TOOLS - names)


@pytest.mark.parametrize("name", sorted(HUMAN_TOOLS))
def test_each_human_side_tool_keeps_its_schema_shape(name):
    """schema 逐字搬过来：描述与参数一个字没改。"""
    from tools.registry import registry

    schema = registry.get_schema(name)
    assert schema is not None, name
    assert schema["description"], name
    assert schema["parameters"]["type"] == "object", name
    # 裁 185：一枚工具只干一件事——不许用 action/mode 这类参数把两件事塞进来。
    assert not {"action", "mode", "surface", "intent"} & set(
        schema["parameters"]["properties"]
    ), name
