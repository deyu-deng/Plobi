"""桌面那两把的闸：**门类是面闸，不是进程 env**（WP-DESKTOP-UI-SURFACE）。

上游 Hermes 的纪律（``tools/AGENTS.md``）：

* **Surface capability is a property of the SESSION, never of the process env.**
* **The toolset is the surface gate.** Keep such tools off the core bundle and in
  a named toolset; the GUI gateway folds it in when the session's platform says GUI.
* **check_fn answers reachability or opt-in, not surface.**（它的结果是进程级 TTL 缓存的，
  「这次会话是不是桌面」根本不该住在那里。）

这一档钉住六件事：

  ① ``desktop_ui`` 门类里**恰好**那两把；
  ② 它们**不在**核心束（CLI／消息／cron 的复合体不再带它们的 schema），
  也不在 ``coding`` posture 里；
  ③ 会话平台 ＝ ``desktop`` ⇒ 那一会话的门类清单里有 ``desktop_ui``；
     平台 ＝ ``tui`` ⇒ 没有；
  ④ **判据不依赖 ``PLOBI_DESKTOP``**：清掉那个环境变量，桌面会话照样有那两把
     （桌面客户端连**远程后端**时后端进程上就没有那个变量）；
  ⑤ 那两把**没有 env check_fn**（check_fn 不再答表面）；
  ⑥ ``resolve_toolset("all")`` **不含**那两把（表面门类不进「全都给」那一扫）。
"""

from __future__ import annotations

import pytest


def _desktop_ui_names():
    from toolsets import TOOLSETS

    return set(TOOLSETS["desktop_ui"]["tools"])


def test_the_desktop_ui_toolset_holds_exactly_those_two():
    """① ``desktop_ui`` 门类里恰好那两把。"""
    assert _desktop_ui_names() == {"read_terminal", "close_terminal"}


def test_the_two_are_off_the_core_bundle_and_out_of_coding():
    """② 窄腰：核心束与 coding posture 都不再带它们。"""
    from toolsets import TOOLSETS, _PLOBI_CORE_TOOLS

    assert "read_terminal" not in _PLOBI_CORE_TOOLS
    assert "close_terminal" not in _PLOBI_CORE_TOOLS
    coding = set(TOOLSETS["coding"]["tools"])
    assert "read_terminal" not in coding
    assert "close_terminal" not in coding


def test_the_gui_gateway_folds_it_in_for_desktop_sessions_only(monkeypatch):
    """③ 会话的平台 ＝ desktop ⇒ 折进去；＝ tui ⇒ 不折。"""
    from tui_gateway import server

    monkeypatch.delenv("PLOBI_TUI_TOOLSETS", raising=False)
    monkeypatch.delenv("PLOBI_DESKTOP", raising=False)
    monkeypatch.delenv("PLOBI_DESKTOP_TERMINAL", raising=False)

    desktop = server._load_enabled_toolsets(platform="desktop") or []
    assert "desktop_ui" in desktop, desktop

    tui = server._load_enabled_toolsets(platform="tui") or []
    assert "desktop_ui" not in tui, tui


def test_the_gate_does_not_depend_on_the_process_env(monkeypatch):
    """④ 判据不依赖 ``PLOBI_DESKTOP``（远程后端那一种拓扑的命门）。"""
    from tui_gateway import server

    monkeypatch.delenv("PLOBI_TUI_TOOLSETS", raising=False)
    monkeypatch.delenv("PLOBI_DESKTOP", raising=False)
    monkeypatch.delenv("PLOBI_DESKTOP_TERMINAL", raising=False)

    desktop = server._load_enabled_toolsets(platform="desktop") or []
    assert "desktop_ui" in desktop, desktop


def test_the_two_tools_have_no_env_check_fn():
    """⑤ check_fn 不再答表面（注册时不再传 check_fn）。"""
    from tools.registry import discover_builtin_tools, registry

    discover_builtin_tools()
    for name in ("read_terminal", "close_terminal"):
        entry = registry.get_entry(name)
        assert entry is not None, name
        assert entry.toolset == "desktop_ui", (name, entry.toolset)
        assert entry.check_fn is None, name


def test_the_surface_toolset_is_not_swept_in_by_all():
    """⑥ 「全都给」不捎上表面那一格（否则 TUI／CLI／cron 白拿那两把）。"""
    from toolsets import TOOLSET_SESSION_PLATFORMS, resolve_toolset

    assert "desktop_ui" in TOOLSET_SESSION_PLATFORMS
    everything = set(resolve_toolset("all"))
    assert "read_terminal" not in everything
    assert "close_terminal" not in everything


def test_end_to_end_the_desktop_session_actually_gets_the_two_tools(monkeypatch):
    """端到端：这一会话的门类清单 ⇒ **真的**进 Agent 的工具表（不是只在清单里挂着）。"""
    from model_tools import get_tool_definitions
    from tui_gateway import server

    monkeypatch.delenv("PLOBI_TUI_TOOLSETS", raising=False)
    monkeypatch.delenv("PLOBI_DESKTOP", raising=False)
    monkeypatch.delenv("PLOBI_DESKTOP_TERMINAL", raising=False)

    def _names(platform):
        enabled = server._load_enabled_toolsets(platform=platform)
        defs = get_tool_definitions(
            enabled_toolsets=enabled,
            quiet_mode=True,
            skip_tool_search_assembly=True,
        )
        return {t["function"]["name"] for t in defs}

    desktop = _names("desktop")
    assert {"read_terminal", "close_terminal"} <= desktop, sorted(desktop)

    tui = _names("tui")
    assert not {"read_terminal", "close_terminal"} & tui, sorted(tui)
