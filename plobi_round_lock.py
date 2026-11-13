"""同一屏同时只跑一轮：cron 到点、`say` 的叫醒子进程，共用这一把锁（裁定 201）。

两个回合同时落进同一屏＝两份钱 + 两条互相打架的历史（模型下一轮读到自相矛盾的
「自己说过的话」）。所以锁只有一份实现，两个入口都来拿：

* ``acquire(name)``：抢到返回标记路径；抢不到返回 ``None``（持有者还活着）。
* 持有者崩了（pid 不在）⇒ 视为过期，直接接管。
* ``release(marker)``：正常跑完删掉。
* 子进程代持：``acquire(name, pid=child.pid)`` —— 叫醒那一轮是**子进程**在跑，
  父进程起完就走，所以标记里记子进程的 pid；子进程退出后下一次 acquire 自然接管。
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

LOCK_DIRNAME = "round-locks"


def lock_path(agent_name: str, root: Path) -> Path:
    slug = "".join(ch for ch in agent_name if ch.isalnum() or ch in "-_")[:40]
    digest = hashlib.sha1(agent_name.encode("utf-8")).hexdigest()[:8]
    folder = Path(root) / "plobi" / LOCK_DIRNAME
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{slug or 'agent'}-{digest}.json"


_IS_WINDOWS = os.name == "nt"


def pid_alive(pid: int) -> bool:
    """这个 pid 现在还活着吗？**只询问，不投信号。**

    Windows 上 `os.kill(pid, 0)` 不是无副作用的探活（bpo-14484）：CPython 把
    ``sig=0`` 当 ``CTRL_C_EVENT`` 走 ``GenerateConsoleCtrlEvent(0, pid)``，等于给
    目标所在的**整个控制台进程组**发 Ctrl+C。后果两条，都是这一把锁的命门：

    1. 活着的持有者被判成死的 ⇒ 锁形同虚设，同一屏并发两轮＝两份钱；
    2. 同组的 pytest 主进程在 threading 等待里被打成 ``KeyboardInterrupt``
       （全量测试跑到 14%~19% 就断，就是它干的）。

    判法与 ``gateway/status.py::_pid_exists`` 同一套：psutil 优先，Windows 上兜底
    走 ``OpenProcess`` + ``WaitForSingleObject``，POSIX 才用 ``os.kill(pid, 0)``。
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        import psutil  # type: ignore

        try:
            if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                return False  # 僵尸还挂在进程表里，但已经死了
        except getattr(psutil, "NoSuchProcess", ()):
            return False
        except Exception:  # noqa: BLE001 - 读不到状态就落到下面那个权威判断
            pass
        return bool(psutil.pid_exists(pid))
    except ImportError:
        pass
    if _IS_WINDOWS:
        return _win_pid_alive(pid)
    try:
        os.kill(pid, 0)  # windows-footgun: ok — POSIX-only branch（Windows 已在上面分流）
    except OSError:
        return False
    return True


def _win_pid_alive(pid: int) -> bool:
    """``OpenProcess`` + ``WaitForSingleObject``：问到答案就关句柄，不碰这个进程。"""
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        # 钉住返回类型：ctypes 默认 restype 是有符号 c_int，会把 WAIT_* / 句柄弄成负数。
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
        kernel32.WaitForSingleObject.restype = ctypes.c_uint32
        kernel32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
        kernel32.CloseHandle.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        SYNCHRONIZE = 0x100000
        WAIT_TIMEOUT = 0x00000102
        ERROR_INVALID_PARAMETER = 87
        ERROR_ACCESS_DENIED = 5

        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, int(pid)
        )
        if not handle:
            err = ctypes.get_last_error()
            if err == ERROR_ACCESS_DENIED:
                return True  # 存在，只是不归我们这用户管
            if err == ERROR_INVALID_PARAMETER:
                return False  # pid 确定已经没了
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
        finally:
            kernel32.CloseHandle(handle)
    except (OSError, AttributeError, ValueError):
        return False


#: 老名字：`cron/scheduler.py` 与 `plugins/plobi-core/wake.py` 都按这个引用。
_pid_alive = pid_alive


def acquire(
    agent_name: str,
    root: Path,
    *,
    pid: Optional[int] = None,
    holder: str = "",
) -> Optional[Path]:
    """抢这一屏的跑道。抢到返回标记路径；持有者还活着返回 ``None``。"""
    marker = lock_path(agent_name, root)
    payload = json.dumps(
        {
            "pid": int(pid or os.getpid()),
            "agent": agent_name,
            "holder": holder,
            "at": datetime.now().replace(microsecond=0).isoformat(),
        },
        ensure_ascii=False,
    )
    try:
        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            held = json.loads(marker.read_text(encoding="utf-8"))
            held_pid = int(held.get("pid") or 0)
        except (OSError, ValueError, TypeError):
            held_pid = 0
        if held_pid and _pid_alive(held_pid):
            return None
        try:
            marker.write_text(payload, encoding="utf-8")
            return marker
        except OSError:
            return None
    except OSError:
        # 落不下锁就别把这一轮也吞了：宁可跑，也不要静默不跑。
        return None
    try:
        os.write(fd, payload.encode("utf-8"))
    finally:
        os.close(fd)
    return marker


def release(marker: Optional[Path]) -> None:
    if marker is None:
        return
    try:
        Path(marker).unlink(missing_ok=True)
    except OSError:
        pass
