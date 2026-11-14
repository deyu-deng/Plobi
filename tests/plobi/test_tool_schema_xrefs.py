"""工具的 schema 描述不许点名**别的门类**的工具（WP-TOOL-SCHEMA-XGATE）。

上游 Hermes 的纪律（``tools/AGENTS.md``）：

    **Schema descriptions must not name tools from other toolsets.**  Those tools
    may be unavailable (missing key, disabled toolset) and the model hallucinates
    calls to them.  Cross-references are added dynamically in
    ``get_tool_definitions()``.

11-14 那把刀把人读面那 17 把拆成 note／diary／dossier 三个门类 ⇒ 之前「都在
provider 里、互相点名无所谓」变成了**真的指着一个可能被关掉的门类**（现读：12 处、5 个
schema）。

这一档钉住四件事：

  ① **静态**描述里一个别的门类的工具名都没有；
  ② 开着的门类才被点名（关掉的那一格不许出现在描述里）；
  ③ 判据按**这个 Agent 的工具表**算——它自己的黑名单减掉的工具不许被点名
     （**Plobi 以 Agent 为最小单位，没有会话**）；
  ④ provider 那两把（``mind_read``／``mind_write``）走注入那条路，同样按 Agent 算。
"""

from __future__ import annotations

import pytest

# 注册表工具靠**导入**自己登记（``tools/registry.py`` 那套约定）⇒ 这一档先把人读面
# 那三个模块拉进来，否则下面查的是**空注册表**（看着像「一个违规都没有」）。
from tools import human_diary_tools, human_dossier_tools, human_note_tools  # noqa: F401
from tools.xrefs import FAMILY_OF, HINT_TARGETS


def _human_tool_names():
    return [n for n, fam in FAMILY_OF.items() if fam in {"note", "diary", "dossier"}]


def _static_descriptions():
    """（工具名 → 静态描述）：注册表那 17 把 ＋ provider 那两把。"""
    from plobi.mind.provider import MindProvider
    from tools.registry import registry

    out = {}
    for name in list(FAMILY_OF):
        schema = registry.get_schema(name)
        if schema:
            out[name] = str(schema.get("description") or "")
    for schema in MindProvider().get_tool_schemas():
        out[schema["name"]] = str(schema.get("description") or "")
    # 注册表那 17 把必须**真的**在表上——否则这一档查的是空注册表（假绿）。
    missing = [
        n
        for n in FAMILY_OF
        if FAMILY_OF[n] in {"note", "diary", "dossier"} and n not in out
    ]
    assert not missing, f"注册表里没有这些工具：{missing}"
    return out


def test_no_static_description_names_another_categorys_tool():
    """① 静态描述里一个别的门类的工具名都没有。"""
    offenders = []
    for name, desc in _static_descriptions().items():
        family = FAMILY_OF.get(name)
        if not family:
            continue
        for other, other_family in FAMILY_OF.items():
            if other_family == family:
                continue
            if other in desc:
                offenders.append((name, other))
    assert not offenders, offenders


def test_the_statics_still_say_what_belongs_to_their_own_kind():
    """①的反面：去掉了跨门类的点名，**本类真信息一个字没少**。"""
    from tools.registry import registry

    note_read = str(registry.get_schema("note_read")["description"])
    assert "``Notes/``" in note_read
    dossier_read = str(registry.get_schema("dossier_read")["description"])
    assert "``Dossiers/``" in dossier_read


def _desc_of(tools, name):
    for td in tools:
        if td.get("function", {}).get("name") == name:
            return str(td["function"].get("description") or "")
    return ""


def test_the_hint_names_only_the_categories_that_are_on():
    """② 开着的门类才被点名；关掉的那一格不许出现在描述里。"""
    from model_tools import get_tool_definitions

    tools = get_tool_definitions(
        enabled_toolsets=["note", "diary"],
        quiet_mode=True,
        skip_tool_search_assembly=True,
    )
    desc = _desc_of(tools, "note_read")
    assert "diary_read" in desc, desc
    # dossier 门类没开 ⇒ 描述里不许出现 dossier 那几把。
    assert "dossier_read" not in desc, desc


def test_the_hint_follows_this_agents_own_table():
    """③ 按这个 Agent 的工具表算：黑名单减掉的工具不许被点名。

    **以 Agent 为最小单位，没有会话**——判据看的是这个 Agent 这一份。
    """
    from model_tools import get_tool_definitions

    tools = get_tool_definitions(
        enabled_toolsets=["note", "diary"],
        disabled_toolsets=["diary"],
        quiet_mode=True,
        skip_tool_search_assembly=True,
    )
    desc = _desc_of(tools, "note_read")
    assert "diary_read" not in desc, desc


def test_the_provider_tools_get_the_same_treatment():
    """④ provider 那两把（mind_read／mind_write）按注入那条路补，判据一样。"""
    from agent.memory_manager import inject_memory_provider_tools
    from plobi.mind.provider import MindProvider

    class _MM:
        def __init__(self):
            self._providers = [MindProvider()]

        def get_all_tool_schemas(self):
            out = []
            for p in self._providers:
                out.extend(p.get_tool_schemas())
            return out

    class _Agent:
        def __init__(self, enabled):
            self.enabled_toolsets = enabled
            self.tools = [{"type": "function", "function": {"name": "note_read"}}]
            self.valid_tool_names = {"note_read"}
            self._memory_manager = _MM()

    agent = _Agent(["mind", "note"])
    inject_memory_provider_tools(agent)
    desc = _desc_of(agent.tools, "mind_read")
    assert "note_read" in desc, desc


def test_the_hint_targets_are_the_kind_scoped_handlers():
    """带引导的就是那几把「按种类分家」的读写手——细粒度那几把不带。"""
    assert HINT_TARGETS == {
        "note_read",
        "note_write",
        "diary_read",
        "diary_write",
        "dossier_read",
        "dossier_write",
        "mind_read",
        "mind_write",
    }
