"""Windows：让 console 子进程别在用户桌面上弹窗（11-17）。

backend 是被 Electron 用 ``windowsHide`` 静默拉起来的（``plobi_cli.main serve``），
**它自己没有控制台**。这时候 ``subprocess`` 随手拉一个 console 程序（``mihomo -t``、
``tasklist``、``taskkill``、``powershell``、``icacls``），Windows 会给它新建一个控制台
窗口 —— 用户看到的就是一闪而过的黑窗。把 stdout/stderr 重定向到管道或 DEVNULL
**不改变这一点**：窗口是 CreateProcess 分配的，跟输出写到哪儿无关。

实测（本机 11-17）：在一个无控制台的 Python 进程里裸 ``Popen(['cmd', '/c', 'timeout'])``，
桌面上立刻多一个 ``conhost.exe`` 和一个空标题 ``ConsoleWindowClass`` 窗口。

用法：``subprocess.run([...], creationflags=NO_WINDOW)``；
已有 flags 的地方用 ``hidden(existing_flags)`` 并进去。非 Windows 上恒为 0，可以直接带。
"""

from __future__ import annotations

import subprocess
import sys

#: ``CREATE_NO_WINDOW``：给子进程一个**没有窗口的**控制台。跟 ``DETACHED_PROCESS``
#: 不是一回事——后者只是「不继承父控制台」，父子关系照旧（tree-kill 认得），
#: 而且父进程本来就没有控制台时，``cmd /c start /b`` 会以为可以新开一个，于是弹窗。
NO_WINDOW: int = (
    getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
)


def hidden(flags: int = 0) -> int:
    """把 ``NO_WINDOW`` 并进一组已有的 creationflags。"""
    return flags | NO_WINDOW
