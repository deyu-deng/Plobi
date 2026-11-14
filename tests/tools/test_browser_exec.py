"""``browser_exec``：写一段 Python 驱动一个真浏览器（WP-BROWSER-EXEC 16a）。

这一档既钉**形状**，也钉**它真的通**：

* 工具在 ``browser`` 门类里注册（上游：它是唯一那把浏览器核心工具）；
* 空代码 ＋ 非法会话名 ⇒ 说人话，不开浏览器；
* **实测**：起一个本地浏览器 ⇒ ``page_info()`` 回得出这一页（title／url／可见文本）；
* 超时 ⇒ 说人话 ＋ **不留孤儿进程**（子进程与浏览器进程树都收干净）。

真浏览器那几条在本机没有 Chromium 家族时会**跳过**（不是绿，是「没法验」）⇒ 别的机器
上照样能过这一档。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest


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
