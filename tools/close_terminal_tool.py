#!/usr/bin/env python3
"""Close a read-only agent terminal tab in the Plobi desktop GUI.

Each ``terminal(background=true)`` process is mirrored as a read-only tab in the
desktop's terminal pane. This tool lets the agent drop a tab it no longer needs
to show — WITHOUT killing the process (use ``process(action='kill')`` for that).
The output keeps buffering and the user can reopen the tab from the status stack.

It routes through the process registry's ``on_close`` sink, which the desktop
gateway wires to emit a ``terminal.close`` event the renderer handles. Like
``read_terminal`` it lives in the ``desktop_ui`` toolset — that toolset is the
surface gate, folded in by the GUI gateway for **desktop sessions** only
(``PLOBI_DESKTOP`` used to be the gate; it answered "who started this process",
not "who is in this session" — upstream's rule: surface capability is a
property of the SESSION).
"""

import json
import os

from tools.process_registry import process_registry
from tools.registry import registry, tool_error


def close_terminal_tool(process_id: str) -> str:
    """Ask the desktop GUI to close a background process's read-only tab."""
    pid = (process_id or "").strip()
    if not pid:
        return tool_error("process_id is required (the background process whose tab to close).")

    return json.dumps(process_registry.request_close_terminal(pid), ensure_ascii=False)


def check_close_terminal_requirements() -> bool:
    """**没有 env 闸**——可用性由**会话**决定（上游纪律②）。

    以前答的是「这个进程是不是被桌面起的」（``PLOBI_DESKTOP``）⇒ **进程级**的：
    桌面客户端连**远程后端**时后端进程上没有那个变量 ⇒ 工具**从工具表里静默消失**，
    而能力其实可用（renderer 的 WS 桥就在那个连接上）。

    现在**门类 ``desktop_ui`` 就是那道面闸**：GUI 网关按**这一会话的平台**折进去
    （platform=desktop 折、tui 不折）。check_fn 只答**可达性／可选**，不答表面
    （它的结果是**进程级 TTL 缓存**的）。

    这个函数现在**没有调用点**（注册时不再传 check_fn）——留着只为让旧引用不至于
    ImportError；新的判据在 ``tests/tools/test_desktop_ui_surface.py``。
    """
    return True


CLOSE_TERMINAL_SCHEMA = {
    "name": "close_terminal",
    "description": (
        "Close the read-only terminal tab for one of your background processes in "
        "the Plobi desktop GUI (the tabs mirroring terminal(background=true) runs). "
        "This does NOT kill the process — it only drops the tab/view; the output "
        "keeps buffering and the user can reopen it from the status stack. Use it "
        "to tidy up when a background process's live terminal is no longer worth "
        "showing. To actually stop the process, use process(action='kill') instead."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "process_id": {
                "type": "string",
                "description": (
                    "The background process's session id (from terminal(background=true) "
                    "output or process(action='list')) whose tab should be closed."
                ),
            },
        },
        "required": ["process_id"],
    },
}


registry.register(
    name="close_terminal",
    toolset="desktop_ui",
    schema=CLOSE_TERMINAL_SCHEMA,
    handler=lambda args, **kw: close_terminal_tool(process_id=args.get("process_id", "")),
    emoji="🖥️",
)
