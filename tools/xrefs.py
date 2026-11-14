"""跨门类引导：工具描述里那句「别的门类用哪一把」的话。

上游 Hermes 的纪律（``tools/AGENTS.md`` 原文）：

    **Schema descriptions must not name tools from other toolsets.**  Those tools
    may be unavailable (missing key, disabled toolset) and the model hallucinates
    calls to them.  Cross-references are added dynamically in
    ``get_tool_definitions()``.

这一刀（``WP-TOOL-SCHEMA-XGATE``）就是照这条：

* **静态**描述里去掉跨门类的点名（只留本类真信息）；
* 那句话**按这个 Agent 实际进表的工具**动态补。

**以 Agent 为最小单位**（Plobi 以 Agent 为单位，**没有会话**）：判据只看**这个 Agent
这一份**的工具表——不看会话、不看进程全局，也**不用 enabled_toolsets 判定**
（Agent 自己的黑名单会把工具减掉，按门类判定就会补一句指向**不在表里**的工具 ⇒
幻觉面照旧）。
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional

# 工具名 → 门类。
#
# 只有「按种类分家」的那四族需要互相指路：笔记／日记／档案（人读面）与脑自己的页面。
# 其余门类（web／browser／terminal…）不在这张表里——它们要么永远在表里，要么各管各的。
FAMILY_OF: Dict[str, str] = {
    # note（人读面：笔记）
    "note_read": "note",
    "note_write": "note",
    "note_rename": "note",
    "note_delete": "note",
    "agent_notes": "note",
    "note_list": "note",
    "note_links": "note",
    "note_set_property": "note",
    # diary（人读面：日记）
    "diary_read": "diary",
    "diary_write": "diary",
    "diary_list": "diary",
    "diary_append": "diary",
    # dossier（人读面：档案）
    "dossier_read": "dossier",
    "dossier_write": "dossier",
    "dossier_list": "dossier",
    "dossier_task_toggle": "dossier",
    "dossier_stage_update": "dossier",
    # mind（脑自己的页面，仍由 provider 提供）
    "mind_read": "mind",
    "mind_write": "mind",
}

# 门类 → 提示句里的称呼（给模型看的是英文）。
_LABEL: Dict[str, str] = {
    "note": "notes",
    "diary": "the diary",
    "dossier": "dossiers",
    "mind": "brain pages",
}

# 门类在提示句里的先后（读起来顺一点）。
_ORDER = ("note", "diary", "dossier", "mind")

# 哪些工具的描述带那句引导：就是那几把**按种类分家**的读写手——
# 它们的静态描述原来有一句「别的门类用哪一把」，那句话现在改成动态补。
# （细粒度那几把——``note_list``／``note_links``／``diary_append``…——各管各的，不带。）
HINT_TARGETS = frozenset({
    "note_read",
    "note_write",
    "diary_read",
    "diary_write",
    "dossier_read",
    "dossier_write",
    "mind_read",
    "mind_write",
})

# 提示句的前缀（认得出是动态加的那句）。
_MARK = "Other kinds live elsewhere"


def family_of(name: str) -> Optional[str]:
    """这一把属于哪一族（不在那张表里就是 ``None``）。"""
    return FAMILY_OF.get(name or "")


def hint_text(family: str, available: Iterable[str]) -> str:
    """这一族（``family``）的工具该补的那句话。

    **只含实际在表里的**别的门类工具——那句话是给模型指路的，指到一个不存在的工具
    就是幻觉面（上游纪律）。
    """
    names = set(available)
    clauses: List[str] = []
    for other in _ORDER:
        if other == family:
            continue
        others = sorted(n for n in names if FAMILY_OF.get(n) == other)
        if not others:
            continue
        clauses.append(f"{_LABEL[other]}: " + "/".join(f"``{n}``" for n in others))
    if not clauses:
        return ""
    return f"{_MARK} — " + "; ".join(clauses) + "."


def apply_cross_family_hints(tools: List[dict], available: Iterable[str]) -> List[dict]:
    """给那几把工具的描述**动态补**跨门类引导。

    上游就在 ``get_tool_definitions()`` 里加同一句（``tools/AGENTS.md``：「Cross-references
    are added dynamically in ``get_tool_definitions()``」）；provider 那两把（``mind_*``）
    由 :func:`agent.memory_manager.inject_memory_provider_tools` 那条路补，判据一样：
    **这个 Agent 手上的工具表**。
    """
    names = set(available)
    out: List[dict] = []
    for entry in tools:
        fn = entry.get("function") or {}
        name = fn.get("name")
        family = FAMILY_OF.get(name or "")
        if family and name in HINT_TARGETS:
            hint = hint_text(family, names)
            desc = str(fn.get("description") or "")
            if hint and _MARK not in desc:
                entry = {
                    "type": "function",
                    "function": {**fn, "description": desc.rstrip() + " " + hint},
                }
        out.append(entry)
    return out
