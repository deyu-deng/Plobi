"""WP-PROVIDER-TOOLSET-SPLIT：provider 的工具按**一类一门类**归户，且真进得了会话的工具表。

**为什么拆**（用户 11-13 原话「以后不要 surface 这个概念了。note 就放在 note 工具集里面。
日记就放在 diary 工具集，dossier-read 就放在对应的 dossier 里面就行了」）：
人读面那三样**规矩不一样**（笔记有硬契约、日记永不自动、档案只增不改），
合成一个门类就只有一个开关，用户没法只关日记。

这一档钉住七件事：

  ① 三个门类默认在桌面会话的 ``enabled_toolsets`` 里；
  ② 每门类的工具真进得了 Agent 的工具表（走真的注入路径，不是查表）；
  ③ 门类 id 与工具名的归户表对得上；
  ④ Tools 页看得见这三格；
  ⑤ 闸要真能关：全局关掉 ⇒ 一把都不进表；用户在 Tools 页关掉 ⇒ 尊重；
  ⑥ 存量迁移：旧的 ``surface`` 开关（开或关）展开成新三格，**不把用户存的态打回默认**；
  ⑦ 桌面会话真走的那条路（``tui_gateway`` 那个）也算；``mind`` 门类照旧。
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


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
MIND_TOOLS = {"mind_read", "mind_write"}
HUMAN_TOOLS = NOTE_TOOLS | DIARY_TOOLS | DOSSIER_TOOLS


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


# ── 判据①：三个门类默认在桌面会话的 enabled_toolsets 里 ────────────────────


def test_the_three_human_side_categories_are_enabled_by_default():
    """没有这三个门类就没有那九把工具 —— 这一条是根。"""
    from plobi_cli.tools_config import _get_platform_tools

    config = {
        "platform_toolsets": {"cli": ["file", "todo", "clarify"]},
        "agent": {},
    }
    enabled = _get_platform_tools(config, "cli", include_default_mcp_servers=True)
    for cat in ("note", "diary", "dossier"):
        assert cat in enabled, (
            f"{cat} 门类没进 enabled_toolsets ⇒ provider 的工具会被门类闸挡掉"
        )


# ── 判据②：每门类的工具真进得了 Agent 的工具表 ────────────────────────────


def test_every_human_side_tool_reaches_the_agent_tool_table():
    from plobi_cli.tools_config import _get_platform_tools

    config = {
        "platform_toolsets": {"cli": ["file", "todo", "clarify"]},
        "agent": {},
    }
    enabled = sorted(_get_platform_tools(config, "cli", include_default_mcp_servers=True))
    got = _provider_tools_for(enabled)
    assert HUMAN_TOOLS.issubset(got), (
        f"人读面那九把没全进表，缺：{sorted(HUMAN_TOOLS - got)}"
    )


def test_one_category_alone_brings_only_its_own_tools():
    """③ 拆开的意思：一门类只放自己那几把。"""
    got = _provider_tools_for(["note"])
    assert NOTE_TOOLS.issubset(got)
    assert not (DIARY_TOOLS & got)
    assert not (DOSSIER_TOOLS & got)

    got = _provider_tools_for(["diary"])
    assert DIARY_TOOLS.issubset(got)
    assert not (NOTE_TOOLS & got)

    got = _provider_tools_for(["dossier"])
    assert DOSSIER_TOOLS.issubset(got)
    assert not (NOTE_TOOLS & got)


# ── 判据③：归户表对得上 ────────────────────────────────────────────────────


def test_the_owner_table_puts_each_tool_under_its_own_kind():
    from agent.memory_manager import _PROVIDER_TOOL_TOOLSET

    for name in NOTE_TOOLS:
        assert _PROVIDER_TOOL_TOOLSET.get(name) == "note", name
    for name in DIARY_TOOLS:
        assert _PROVIDER_TOOL_TOOLSET.get(name) == "diary", name
    for name in DOSSIER_TOOLS:
        assert _PROVIDER_TOOL_TOOLSET.get(name) == "dossier", name
    for name in MIND_TOOLS:
        assert _PROVIDER_TOOL_TOOLSET.get(name) == "mind", name
    assert "surface" not in _PROVIDER_TOOL_TOOLSET.values(), (
        "surface 这个门类退役了（用户 11-13 原话）"
    )


def test_each_toolset_lists_exactly_its_own_tools():
    from toolsets import resolve_toolset

    assert set(resolve_toolset("note")) == NOTE_TOOLS
    assert set(resolve_toolset("diary")) == DIARY_TOOLS
    assert set(resolve_toolset("dossier")) == DOSSIER_TOOLS


# ── 判据④：Tools 页看得见这三格 ────────────────────────────────────────────


def test_the_three_rows_show_up_in_the_tools_page_checklist():
    from plobi_cli.tools_config import _get_effective_configurable_toolsets

    names = {name for name, _, _ in _get_effective_configurable_toolsets()}
    for cat in ("note", "diary", "dossier"):
        assert cat in names, f"Tools 页看不见「{cat}」这一格 = 用户以为没做"
    assert "surface" not in names, "退役的门类不许再出现在 Tools 页上"


# ── 判据⑤：闸要真能关 ────────────────────────────────────────────────────


def test_disabling_one_category_globally_removes_only_its_tools():
    config = {
        "platform_toolsets": {"cli": ["file", "todo"]},
        "agent": {"disabled_toolsets": ["diary"]},
    }
    enabled = sorted(_platform_tools(config))
    assert "diary" not in enabled
    got = _provider_tools_for(enabled)
    assert not (DIARY_TOOLS & got), "全局关掉 diary ⇒ 那两把不该进表"
    assert NOTE_TOOLS.issubset(got), "关 diary 不该连笔记一起关掉"


def test_user_turning_one_row_off_in_the_tools_page_keeps_it_off():
    """用户保存过 Tools 页、清单里没有 diary ⇒ 那是他关掉的 ⇒ 尊重。"""
    config = {
        "platform_toolsets": {"cli": ["file", "todo"]},
        "known_provider_toolsets": {"cli": ["note", "diary", "dossier", "mind"]},
        "agent": {},
    }
    enabled = sorted(_platform_tools(config))
    assert "diary" not in enabled
    got = _provider_tools_for(enabled)
    assert not (DIARY_TOOLS & got)


def test_user_leaving_the_row_on_keeps_it_on():
    config = {
        "platform_toolsets": {"cli": ["file", "todo", "note"]},
        "known_provider_toolsets": {"cli": ["note"]},
        "agent": {},
    }
    enabled = sorted(_platform_tools(config))
    assert "note" in enabled
    assert NOTE_TOOLS.issubset(_provider_tools_for(enabled))


def test_saving_an_empty_list_keeps_the_three_off():
    """用户在 Tools 页「全不勾再存」⇒ 这一格也在那张清单上，不在就是关着。

    门类默认开的那条路（「用户从没见过这一行 ⇒ 默认开」）**不许**把一张空清单
    变成「全开」——否则他把能勾的都取消掉，保存后这九把又自己回来了。
    """
    config = {
        "platform_toolsets": {"cli": []},
        "agent": {},
    }
    enabled = sorted(_platform_tools(config))
    for cat in ("note", "diary", "dossier"):
        assert cat not in enabled, f"用户存的是一张空清单 ⇒ {cat} 该是关着"
    got = _provider_tools_for(enabled)
    assert not (HUMAN_TOOLS & got)


def test_saving_the_tools_page_records_that_the_user_had_a_say():
    """保存时要记「用户表过态」，否则关掉一格下次又弹回来。"""
    from plobi_cli.tools_config import _save_platform_tools

    config = {"platform_toolsets": {"cli": ["file", "todo"]}, "agent": {}}
    _save_platform_tools(config, "cli", {"file", "todo"})
    known = config["known_provider_toolsets"]["cli"]
    for cat in ("note", "diary", "dossier"):
        assert cat in known, f"保存后必须记下用户对「{cat}」这一格表过态"

    # 关掉那一格再保存一次 ⇒ 读回来仍是关
    config2 = {"platform_toolsets": {"cli": ["file", "todo"]}, "agent": {}}
    _save_platform_tools(config2, "cli", {"file", "todo"})
    config2["platform_toolsets"]["cli"] = ["file", "todo"]
    enabled = sorted(_platform_tools(config2))
    assert "note" not in enabled


# ── 判据⑥：存量迁移（旧的 surface 开关） ──────────────────────────────────


def test_the_legacy_surface_switch_still_turns_the_three_on():
    """用户在老版本里把 surface 开着 ⇒ 拆成三格之后仍然开着。"""
    config = {
        "platform_toolsets": {"cli": ["file", "todo", "surface"]},
        "known_provider_toolsets": {"cli": ["surface"]},
        "agent": {},
    }
    enabled = sorted(_platform_tools(config))
    for cat in ("note", "diary", "dossier"):
        assert cat in enabled, f"旧的 surface 开着 ⇒ {cat} 不该变关"
    assert HUMAN_TOOLS.issubset(_provider_tools_for(enabled))


def test_the_legacy_surface_switch_off_stays_off():
    """用户在老版本里把 surface 关着 ⇒ 那是他显式关掉的 ⇒ 拆键不许弹回来。"""
    config = {
        "platform_toolsets": {"cli": ["file", "todo"]},
        "known_provider_toolsets": {"cli": ["surface"]},
        "agent": {},
    }
    enabled = sorted(_platform_tools(config))
    for cat in ("note", "diary", "dossier"):
        assert cat not in enabled, f"旧的 surface 关着 ⇒ {cat} 该仍然是关"
    got = _provider_tools_for(enabled)
    assert not (HUMAN_TOOLS & got)


def test_the_legacy_key_never_leaks_into_the_toolset_list():
    """旧键不在 TOOLSETS 里了 ⇒ 它不许留在清单里（resolve_toolset 会拿到不存在的门类）。"""
    from toolsets import TOOLSETS

    assert "surface" not in TOOLSETS
    config = {
        "platform_toolsets": {"cli": ["file", "todo", "surface"]},
        "known_provider_toolsets": {"cli": ["surface"]},
        "agent": {},
    }
    enabled = sorted(_platform_tools(config))
    assert "surface" not in enabled


# ── 不回归：mind 门类照旧，且不靠插件搭便车 ──────────────────────────────


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
    人读面那几把当时压根没有这层关系 ⇒ 从来没进过会话。
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

    assert "note" in enabled
    assert "mind" in enabled
    got = _provider_tools_for(enabled)
    assert HUMAN_TOOLS.issubset(got)
    assert "mind_read" in got and "mind_write" in got


# ── 判据⑦（自动化护栏）：桌面会话真走的那条路也算 ──────────────────────────


def test_the_desktop_gateway_path_also_enables_the_three(monkeypatch):
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
    for cat in ("note", "diary", "dossier"):
        assert cat in enabled, (
            f"桌面会话真路径算出的 enabled_toolsets 里没有 {cat} ⇒ "
            "provider 的工具在真机上还是进不了会话"
        )
    got = _provider_tools_for(sorted(enabled))
    assert HUMAN_TOOLS.issubset(got), (
        f"后端真路径下人读面那九把没全进表，缺：{sorted(HUMAN_TOOLS - got)}"
    )
