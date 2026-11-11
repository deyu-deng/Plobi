"""WP-SURFACE-TOOLSET-GATE：provider 的工具真进得了会话的工具表。

病根（11-10 22:5x 实探）：``surface`` 门类在 ``toolsets.py`` 有定义，但在
``plobi_cli/tools_config.py`` 里没有 ⇒ 桌面会话的 ``enabled_toolsets`` 不含它
⇒ ``agent/memory_manager.py`` 那道按门类归户的闸把七把笔记工具全挡在门外。

这档钉住判据七条里的六条（第七条是端到端探针，在屏上验）。
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


SURFACE_TOOLS = {
    "note_read",
    "note_write",
    "note_rename",
    "note_delete",
    "diary_read",
    "diary_write",
    "dossier_read",
    "dossier_write",
    "agent_notes",
}


# ── helpers ────────────────────────────────────────────────────────────────


def _provider_tools_for(enabled_toolsets):
    """走真的注入路径，返回进表的 provider 工具名集合。"""
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
            self.tools = []
            self.valid_tool_names = set()
            self._memory_manager = _MM()

    agent = _Agent(list(enabled_toolsets))
    inject_memory_provider_tools(agent)
    return {t["function"]["name"] for t in agent.tools}


def _platform_tools(config, platform="cli"):
    from plobi_cli.tools_config import _get_platform_tools

    return _get_platform_tools(config, platform, include_default_mcp_servers=True)


# ── 判据①：桌面会话的 enabled_toolsets 含 surface ─────────────────────────


def test_surface_category_is_enabled_on_the_desktop_platform():
    """没有 surface 门类就没有那九把工具 —— 这条是本刀的根。"""
    from plobi_cli.tools_config import _get_platform_tools

    config = {
        "platform_toolsets": {"cli": ["file", "todo", "clarify"]},
        "agent": {},
    }
    enabled = _get_platform_tools(config, "cli", include_default_mcp_servers=True)
    assert "surface" in enabled, (
        "surface 门类没进 enabled_toolsets ⇒ provider 的笔记工具会被门类闸挡掉"
    )


# ── 判据②：十一把里九把 surface 系全部进表 ───────────────────────────────


def test_all_nine_surface_tools_reach_the_agent_tool_table():
    from plobi_cli.tools_config import _get_platform_tools

    config = {
        "platform_toolsets": {"cli": ["file", "todo", "clarify"]},
        "agent": {},
    }
    enabled = sorted(_get_platform_tools(config, "cli", include_default_mcp_servers=True))
    got = _provider_tools_for(enabled)
    assert SURFACE_TOOLS.issubset(got), (
        f"surface 系工具没全进表，缺：{sorted(SURFACE_TOOLS - got)}"
    )


# ── 判据③：两把新工具的 owner 不再是 None ────────────────────────────────


def test_new_rename_delete_tools_share_the_surface_category():
    from agent.memory_manager import _PROVIDER_TOOL_TOOLSET

    assert _PROVIDER_TOOL_TOOLSET.get("note_rename") == "surface"
    assert _PROVIDER_TOOL_TOOLSET.get("note_delete") == "surface"


def test_surface_toolset_lists_exactly_the_provider_surface_tools():
    from toolsets import resolve_toolset

    assert set(resolve_toolset("surface")) == SURFACE_TOOLS


# ── 判据④：Tools 页看得见这一格 ─────────────────────────────────────────


def test_surface_shows_up_in_the_tools_page_checklist():
    from plobi_cli.tools_config import _get_effective_configurable_toolsets

    names = {name for name, _, _ in _get_effective_configurable_toolsets()}
    assert "surface" in names, "Tools 页看不见「人读面」这一格 = 用户以为没做"


# ── 判据⑤：闸要真能关 ───────────────────────────────────────────────────


def test_disabling_the_category_globally_removes_every_surface_tool():
    config = {
        "platform_toolsets": {"cli": ["file", "todo"]},
        "agent": {"disabled_toolsets": ["surface"]},
    }
    enabled = sorted(_platform_tools(config))
    assert "surface" not in enabled
    got = _provider_tools_for(enabled)
    assert not (SURFACE_TOOLS & got), "全局关掉后不该有任何 surface 工具进表"


def test_user_turning_the_row_off_in_the_tools_page_keeps_it_off():
    """用户保存过 Tools 页、清单里没有 surface ⇒ 那是他关掉的 ⇒ 尊重。"""
    config = {
        "platform_toolsets": {"cli": ["file", "todo"]},
        "known_provider_toolsets": {"cli": ["surface"]},
        "agent": {},
    }
    enabled = sorted(_platform_tools(config))
    assert "surface" not in enabled
    got = _provider_tools_for(enabled)
    assert not (SURFACE_TOOLS & got)


def test_user_leaving_the_row_on_keeps_it_on():
    config = {
        "platform_toolsets": {"cli": ["file", "todo", "surface"]},
        "known_provider_toolsets": {"cli": ["surface"]},
        "agent": {},
    }
    enabled = sorted(_platform_tools(config))
    assert "surface" in enabled
    assert SURFACE_TOOLS.issubset(_provider_tools_for(enabled))


def test_saving_the_tools_page_records_that_the_user_had_a_say():
    """保存时要记「用户表过态」，否则关掉一格下次又弹回来。"""
    from plobi_cli.tools_config import _save_platform_tools

    config = {"platform_toolsets": {"cli": ["file", "todo"]}, "agent": {}}
    _save_platform_tools(config, "cli", {"file", "todo"})
    known = config["known_provider_toolsets"]["cli"]
    assert "surface" in known, "保存后必须记下用户对这一格表过态"

    # 关掉那一格再保存一次 ⇒ 读回来仍是关
    config2 = {"platform_toolsets": {"cli": ["file", "todo"]}, "agent": {}}
    _save_platform_tools(config2, "cli", {"file", "todo"})
    config2["platform_toolsets"]["cli"] = ["file", "todo"]
    enabled = sorted(_platform_tools(config2))
    assert "surface" not in enabled


# ── 不回归：mind 门类照旧，且不靠插件搭便车 ─────────────────────────────


def test_mind_category_still_reaches_the_agent():
    config = {
        "platform_toolsets": {"cli": ["file", "todo"]},
        "agent": {},
    }
    enabled = sorted(_platform_tools(config))
    got = _provider_tools_for(enabled)
    assert "mind_read" in got and "mind_write" in got


def test_provider_categories_do_not_depend_on_a_plugin_being_loaded():
    """provider 门类不该靠插件搭便车才活。

    病根（11-10 23:1x 实测）：``mind`` 门类当时能进清单，靠的是 north-star
    插件把 ``learn_*`` 注册进 ``mind`` ⇒ 插件门类默认开；插件一缺席，
    ``mind`` 就掉出清单，``mind_read``／``mind_write`` 也跟着掉。
    ``surface`` 压根没有这层关系 ⇒ 七把笔记工具从来没进过会话。
    """
    import plobi_cli.tools_config as tc

    orig = tc._get_plugin_toolset_keys
    tc._get_plugin_toolset_keys = lambda: set()
    try:
        config = {
            "platform_toolsets": {"cli": ["file", "todo"]},
            "agent": {},
        }
        enabled = sorted(_platform_tools(config))
    finally:
        tc._get_plugin_toolset_keys = orig

    assert "surface" in enabled
    assert "mind" in enabled
    got = _provider_tools_for(enabled)
    assert SURFACE_TOOLS.issubset(got)
    assert "mind_read" in got and "mind_write" in got


# ── 判据⑦（自动化护栏）：桌面会话真走的那条路也算 surface ──────────────────


def test_the_desktop_gateway_path_also_enables_the_surface_category(monkeypatch):
    """桌面会话真正走的是 ``tui_gateway/server.py::_load_enabled_toolsets``。

    11-11 端到端复验抓到的：上面那些用例走的是 ``_get_platform_tools``，
    而桌面会话实际先过 ``_load_enabled_toolsets`` —— 两条路不是一回事
    （那一条会读环境变量、coding posture、config，再落到 ``_get_platform_tools``）。
    只验后者，真机上照样可能拿不到那九把 ⇒ 这里把前者也钉住。
    """
    monkeypatch.delenv("PLOBI_TUI_TOOLSETS", raising=False)
    # coding posture（focus 模式）会整份替换门类清单 ⇒ 强制走 fallback 那条，
    # 与用户没开 focus 时的桌面行为一致。
    import agent.coding_context as cc

    monkeypatch.setattr(cc, "coding_selection", lambda **kwargs: None)

    from tui_gateway.server import _load_enabled_toolsets

    enabled = set(_load_enabled_toolsets() or [])
    assert "surface" in enabled, (
        "桌面会话真路径算出的 enabled_toolsets 里没有 surface ⇒ "
        "provider 的笔记工具在真机上还是进不了会话"
    )
    got = _provider_tools_for(sorted(enabled))
    assert SURFACE_TOOLS.issubset(got), (
        f"后端真路径下 surface 系工具没全进表，缺：{sorted(SURFACE_TOOLS - got)}"
    )
