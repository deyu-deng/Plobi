"""**Capabilities**：一个 Agent 现在能自主调什么（`WP-AGENT-CAPABILITIES`，11-15）。

这一档钉三件事：

1. **三样能力各自只有一份判据**，这一层只**汇**不**判**（技能／工具门类／MCP）；
2. **名册行的黑名单真的减**（技能／工具集／MCP 各一份，都在 `blocked` 里报出来）；
3. 名册行那个**正面的 `skills:` 退役**（写了不生效 ⇒ 读盘忽略、写回自然抹掉）。

真名册那份（`plobi/agents/registry.py`）另有一档钉退役本身
（`tests/plobi/test_agent_registry.py`）。
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def sources(monkeypatch):
    """把三样能力的**判据**换成可控的假读数（本层只汇，不判）。"""
    import agent.skill_utils as skill_utils
    import model_tools
    import plobi.agents.registry as registry
    import plobi_cli.tools_config as tools_config
    import tools.mcp_tool as mcp_tool
    import tools.skills_tool as skills_tool

    state = {
        "installed_skills": [{"name": "alpha"}, {"name": "beta"}],
        "global_disabled": {"beta"},
        "platform_toolsets": {"file", "web", "terminal"},
        "mcp_servers": {"filesystem": {}, "remote": {}},
        "row": {"skills": set(), "toolsets": set(), "mcp_servers": set()},
        "active_agent": "",
        "definitions": [
            {"type": "function", "function": {"name": "read_file"}},
            {"type": "function", "function": {"name": "web_search"}},
        ],
    }

    def fake_disabled_sets(profile_name: str = "") -> dict:
        # 照真实那条的口径：查无此行 ⇒ 全空（不猜）
        name = str(profile_name or state["active_agent"]).strip()
        if not name:
            return {"skills": set(), "toolsets": set(), "mcp_servers": set()}
        return {
            "skills": set(state["row"]["skills"]),
            "toolsets": set(state["row"]["toolsets"]),
            "mcp_servers": set(state["row"]["mcp_servers"]),
        }

    monkeypatch.setattr(
        skills_tool, "_find_all_skills", lambda **kw: list(state["installed_skills"])
    )
    monkeypatch.setattr(
        skill_utils, "get_disabled_skill_names", lambda platform=None: set(state["global_disabled"])
    )
    monkeypatch.setattr(
        tools_config, "_get_platform_tools", lambda config, platform, **kw: set(state["platform_toolsets"])
    )
    monkeypatch.setattr(
        mcp_tool, "_load_mcp_config", lambda: dict(state["mcp_servers"])
    )
    monkeypatch.setattr(registry, "agent_disabled_sets", fake_disabled_sets)
    monkeypatch.setattr(
        model_tools,
        "get_tool_definitions",
        lambda **kw: list(state["definitions"]),
    )
    return state


def test_the_capabilities_tool_is_registered():
    from tools.registry import discover_builtin_tools, registry

    discover_builtin_tools()
    entry = registry.get_entry("agent_capabilities")
    assert entry is not None
    assert entry.toolset == "capabilities"


def test_the_three_kinds_are_reported_side_by_side(sources):
    from plobi.agents.capabilities import capabilities_for

    out = capabilities_for("aura", platform="desktop")

    assert out["agent"] == "aura"
    assert out["platform"] == "desktop"
    # 技能：装着的 − 全局禁用的
    assert out["skills"] == ["alpha"]
    # 工具门类：平台开的
    assert out["toolsets"] == ["file", "terminal", "web"]
    # 工具名：实际进表的那几把
    assert out["tools"] == ["read_file", "web_search"]
    # MCP：全局表
    assert out["mcp_servers"] == ["filesystem", "remote"]


def test_the_row_blacklist_subtracts_from_all_three(sources):
    from plobi.agents.capabilities import capabilities_for

    sources["row"]["skills"] = {"alpha"}
    sources["row"]["toolsets"] = {"terminal"}
    sources["row"]["mcp_servers"] = {"remote"}

    out = capabilities_for("aura", platform="desktop")

    assert out["skills"] == []
    assert "terminal" not in out["toolsets"]
    assert out["mcp_servers"] == ["filesystem"]
    # 三份黑名单都报出来（人要知道是谁关的）
    assert out["blocked"]["skills"] == ["alpha"]
    assert out["blocked"]["toolsets"] == ["terminal"]
    assert out["blocked"]["mcp_servers"] == ["remote"]


def test_the_answer_is_read_only_and_says_what_it_means(sources):
    """它只**报**，不启动任何东西、不改任何配置（门类说明里那句话也在）。"""
    from plobi.agents.capabilities import capabilities_for

    out = capabilities_for("aura", platform="desktop")

    assert "没被列出来" in out["note"]
    assert "只读" in out["note"] or "此刻" in out["note"]


def test_an_unknown_agent_has_no_row_blacklist(sources):
    """查无此行 ⇒ 不减（名册里没有那个人，不当他关掉了什么）。"""
    from plobi.agents.capabilities import capabilities_for

    sources["row"]["skills"] = {"alpha"}

    out = capabilities_for("", platform="desktop")

    # 空身份 ⇒ 那一格黑名单是空的（名册查无此行 = 原样全量）
    assert out["blocked"]["skills"] == []
    assert out["skills"] == ["alpha"]
