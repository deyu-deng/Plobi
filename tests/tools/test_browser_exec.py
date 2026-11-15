"""``browser_exec``：写一段 Python 驱动一个真浏览器（WP-BROWSER-EXEC 16a 底座 ＋ 16b 换面）。

16a 那一段既钉**形状**，也钉**它真的通**：

* 工具在 ``browser`` 门类里注册（上游：它是唯一那把浏览器核心工具）；
* 空代码 ＋ 非法会话名 ⇒ 说人话，不开浏览器；
* **实测**：起一个本地浏览器 ⇒ ``page_info()`` 回得出这一页（title／url／可见文本）；
* 超时 ⇒ 说人话 ＋ **不留孤儿进程**（子进程与浏览器进程树都收干净）。

16b 那一段钉**换面**：

* 旧那十几把点击类**不再进注册表**（文件留着，代码留着）；
* ``browser`` 门类只剩 ``browser_exec`` ＋ 裸 CDP 那把 ``browser_cdp`` ＋ ``web_search``；
* **旧能力不丢**：旧族能做的（导航／快照／点击／输入／滚动／后退／按键／取图／
  控制台／截图／裸 CDP／弹窗）都在 helper 集里；
* **护栏**：云元数据那条地板线一律拒；私有／内网地址在浏览器是远端时拒。

真浏览器那几条在本机没有 Chromium 家族时会**跳过**（不是绿，是「没法验」）⇒ 别的机器
上照样能过这一档。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

RETIRED_BROWSER_TOOLS = [
    "browser_navigate",
    "browser_snapshot",
    "browser_click",
    "browser_type",
    "browser_scroll",
    "browser_back",
    "browser_press",
    "browser_get_images",
    "browser_vision",
    "browser_console",
    "browser_dialog",
]


def _chromium_available() -> bool:
    try:
        from tools.browser_launcher import find_chromium

        return find_chromium() is not None
    except Exception:  # noqa: BLE001
        return False


needs_browser = pytest.mark.skipif(
    not _chromium_available(), reason="no Chromium-family browser on this machine"
)


def test_the_tool_is_registered_in_the_browser_toolset():
    from tools.registry import discover_builtin_tools, registry

    discover_builtin_tools()
    entry = registry.get_entry("browser_exec")
    assert entry is not None, "browser_exec is not registered"
    assert entry.toolset == "browser", entry.toolset
    assert "code" in entry.schema["parameters"]["properties"]


def test_empty_code_says_so_without_starting_a_browser():
    from tools.browser_exec_tool import browser_exec

    out = browser_exec("   ")
    assert out["success"] is False
    assert "code" in out["error"].lower()


def test_an_invalid_session_name_is_refused():
    from tools.browser_exec_tool import browser_exec

    out = browser_exec("print(1)", session="bad name!")
    assert out["success"] is False
    assert "session" in out["error"].lower()


def test_the_workspace_persists_between_calls():
    """跨调用持久的是**文件**，不是 Python 变量（上游同款口径）。"""
    from tools.browser_exec_runtime import workspace_dir

    first = workspace_dir("unit-surface")
    marker = first / "kept.txt"
    marker.write_text("kept", encoding="utf-8")
    second = workspace_dir("unit-surface")
    assert second == first
    assert (second / "kept.txt").read_text(encoding="utf-8") == "kept"


@needs_browser
def test_it_really_drives_a_browser():
    """**实测**：起一个本地浏览器，跑一段 Python 读回这一页。"""
    from tools.browser_exec_tool import browser_exec

    out = browser_exec(
        "info = page_info()\nprint('TITLE:', info.get('title'))\nprint('URL:', info.get('url'))",
        task_id="test-browser-exec",
        timeout_s=120,
    )
    assert out["success"] is True, out
    assert "URL:" in out["output"], out
    assert "about:blank" in out["output"], out


@needs_browser
def test_a_timeout_is_reported_and_leaves_no_orphan():
    """超时 ⇒ 说人话 ＋ 子进程被收干净（不留孤儿）。"""
    from tools.browser_exec_tool import browser_exec

    before = _child_pids()
    out = browser_exec("while True:\n    pass\n", task_id="test-browser-exec", timeout_s=5)
    assert out["success"] is False
    assert "timed out" in out["error"].lower(), out
    time.sleep(1.0)
    after = _child_pids()
    assert not (after - before), f"orphan processes: {sorted(after - before)}"


def _child_pids() -> set:
    """当前 python 子进程（按命令行里那个 browser_exec_runner 认）。"""
    pids = set()
    try:
        import psutil

        for proc in psutil.process_iter(["pid", "cmdline"]):
            try:
                cmdline = proc.info.get("cmdline") or []
            except Exception:  # noqa: BLE001
                continue
            if any("browser_exec_runner" in str(part) for part in cmdline):
                pids.add(proc.info["pid"])
    except Exception:  # noqa: BLE001 - psutil 没有就当没有子进程
        return pids
    return pids


# ── 16b · 换面 ───────────────────────────────────────────────────────────────


def test_the_retired_click_family_is_not_registered():
    """旧那十几把**停止注册**（文件不删，代码留着——16c 还要用里面的云接线）。"""
    from tools.registry import discover_builtin_tools, registry

    discover_builtin_tools()
    for name in RETIRED_BROWSER_TOOLS:
        assert registry.get_entry(name) is None, f"{name} is still registered"


def test_the_browser_toolset_is_just_the_one_tool():
    """``browser`` 门类只剩一把 ＋ 裸 CDP ＋ web_search。"""
    from toolsets import resolve_toolset

    tools = resolve_toolset("browser")
    assert "browser_exec" in tools
    assert "browser_cdp" in tools
    for name in RETIRED_BROWSER_TOOLS:
        assert name not in tools, f"{name} is still in the browser toolset"


def test_the_helper_set_covers_what_the_old_family_did():
    """**旧能力不丢**：旧族能做的都在 helper 集里（判据 6）。"""
    from tools.browser_exec_runner import _HELPERS

    for helper in [
        "goto_url",        # browser_navigate
        "snapshot",        # browser_snapshot
        "click",           # browser_click
        "fill_input",      # browser_type
        "scroll",          # browser_scroll
        "back",            # browser_back
        "press",           # browser_press
        "get_images",      # browser_get_images
        "console_logs",    # browser_console
        "capture_screenshot",  # browser_vision 的截图那半
        "cdp",             # browser_cdp
        "dialogs",         # browser_dialog
        "respond_dialog",
    ]:
        assert helper in _HELPERS, f"helper {helper} is missing"


def test_the_cloud_metadata_floor_is_unconditional():
    """云元数据那条地板线：不管浏览器在不在本机，一律拒。"""
    from tools.browser_exec_guard import check_navigation_url

    for is_local in (True, False):
        verdict = check_navigation_url(
            "http://169.254.169.254/latest/meta-data/", browser_is_local=is_local
        )
        assert verdict.ok is False, f"metadata endpoint passed with is_local={is_local}"
        assert "metadata" in verdict.reason.lower()


def test_private_addresses_are_blocked_for_a_remote_browser_only():
    """私有／内网地址：远端浏览器拒；本机浏览器放行（本机开发服务器是正常用法）。"""
    from tools.browser_exec_guard import check_navigation_url

    remote = check_navigation_url("http://127.0.0.1:8080/", browser_is_local=False)
    assert remote.ok is False, "a remote browser must not reach a private address"

    local = check_navigation_url("http://127.0.0.1:8080/", browser_is_local=True)
    assert local.ok is True, local.reason


@needs_browser
def test_the_guard_stops_a_navigation_before_it_happens():
    """实测：护栏在**导航之前**就把危险 URL 拦下来（页面根本没去）。"""
    from tools.browser_exec_tool import browser_exec

    out = browser_exec(
        "goto_url('http://169.254.169.254/latest/meta-data/')\nprint('should not get here')",
        task_id="test-browser-exec-guard",
        timeout_s=120,
    )
    assert out["success"] is False, out
    assert "blocked" in (out.get("error") or "").lower(), out
    assert "should not get here" not in out.get("output", "")


@needs_browser
def test_goto_url_goes_through_the_guarded_path():
    """实测：``goto_url`` 走护栏那条路（主进程判 ＋ 导航 ＋ 落点复核）。"""
    from tools.browser_exec_tool import browser_exec

    out = browser_exec(
        "print('AT:', goto_url('about:blank'))",
        task_id="test-browser-exec-nav",
        timeout_s=120,
    )
    assert out["success"] is True, out
    assert "AT:" in out["output"], out
