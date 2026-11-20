"""微信那条腿**自己**管它要用的那个本地采集进程（chatlog 引擎）。

它**就是这条腿的一部分**，不是一个独立子系统——用户 11-20 09:3x 原话：
「不用叫 sidecar 了，地位没有这么高。只是一个采集腿代码逻辑而已。」

这一格只做一件事：**一轮采集 = 一个会话**。

    ensure_process()    探活 → 不在就起 → 等它应答
    release_process()   **只收自己起的那一个**（借来的那个一动不动）

为什么这么分（11-20 现核）：

- 微信没有 API：消息在本机已解密的库里，读库的是那个引擎进程（它答 :5030 的 HTTP 查询）。
- 采集代码本来就只在采集时跑 ⇒ **那个进程也该只在采集时在**。
- 这一版引擎**没有实时接收**（`sidecar.py:68-69`）⇒ 让它常驻换不来「消息一到就落池」，
  只换来「随时能问」——而现在问的那一趟**就是**采集那一趟。
- 家长原来在桌面上：桌面关着 ⇒ 那个进程不存在 ⇒ cron／巡检（独立进程）那几轮整条腿采不到。

**借来的那个不动**：探到已经在跑就借用它，不重复起、**也不收它**——不是自己起的，不许乱杀
（裁定 105：只杀自己那个子进程，按 PID 杀它那棵进程树，不按映像名全局杀）。

**同一件事只有一份实现**：起它走 `scripts/chatlog_server.ps1`（那份脚本是唯一写引擎配置、
唯一拼 argv 的地方——密钥不上命令行）。这里**不重写**它，只管「什么时候起、什么时候收」。
"""

from __future__ import annotations

import contextlib
import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

#: 起它之后等它应答的上限（秒）。引擎要读库、可能还要解一段 ⇒ 给得宽一点。
DEFAULT_START_TIMEOUT = 45.0
#: 等应答时的轮询间隔（秒）。
POLL_INTERVAL = 0.5
#: 拿不到起停锁时的等待上限（秒）——两个腿（cron 采集／cron 巡检）同时撞上同一个端口时的排队。
DEFAULT_LOCK_TIMEOUT = 60.0


@dataclass(frozen=True)
class ProcessHandle:
    """一轮采集里那个进程的**归属**。

    :attr:`started_by_us` 是 False ⇒ **那个不是我们起的**：借用它，收的时候不许动它。
    """

    started_by_us: bool
    pid: int = 0
    detail: str = ""


def repo_root() -> Path:
    """``plobi/collectors/chatlog/process.py`` → 仓库根。无盘符硬编码。"""
    return Path(__file__).resolve().parents[3]


#: 起它走的那份脚本——**这一腿自己的一份**，公共层不再有 spawn 代码（它只照声明喊这一腿的手）。
SCRIPT_RELATIVE = Path("scripts") / "chatlog_server.ps1"


def start_script() -> Path:
    """起它走的那份脚本——**唯一**写引擎配置、唯一拼 argv 的地方。"""
    return repo_root() / SCRIPT_RELATIVE


def lock_path() -> Path:
    """起停锁的落点：**数据家**底下，不落 C 盘（`D:\\_POLICY`）。"""
    from plobi_constants import get_plobi_home

    return get_plobi_home() / "chatlog" / "collect.lock"


# --------------------------------------------------------------------------- #
# 跨进程互斥：两个腿不许同时去起同一个端口
# --------------------------------------------------------------------------- #


@contextlib.contextmanager
def _exclusive(path: Path, *, timeout: float = DEFAULT_LOCK_TIMEOUT) -> Iterator[None]:
    """一把跨进程的文件锁。拿不到就排队，排队超时就说排队超时（不假装拿到了）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if os.fstat(fd).st_size == 0:
            # 至少一个字节：`msvcrt.locking` 锁的是从当前位置起的那一段。
            os.write(fd, b"\0")
        deadline = time.monotonic() + timeout
        while True:
            try:
                _lock_fd(fd)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("另一个采集腿正占着这个端口，等它先走")
                time.sleep(POLL_INTERVAL)
        try:
            yield
        finally:
            _unlock_fd(fd)
    finally:
        os.close(fd)


def _lock_fd(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_fd(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


# --------------------------------------------------------------------------- #
# 起 / 收
# --------------------------------------------------------------------------- #


def _healthy(client) -> bool:
    """它在不在应答。任何异常都当「不在」（宁可多起一次，也不要拿着一个坏探针去采）。"""
    healthy = getattr(client, "healthy", None)
    if healthy is None:
        return False
    try:
        return bool(healthy())
    except Exception:  # noqa: BLE001 - 探针自己坏了也是「不在」
        return False


def spawn_process() -> Optional[int]:
    """起那个采集进程。回**这一棵树的根 pid**，起不来回 ``None``。

    走脚本而不是直接 spawn 引擎：那份脚本是唯一写引擎配置、唯一拼 argv 的地方
    （密钥不上命令行）。脚本**前台**跑引擎 ⇒ 这棵树的根是脚本那个进程。
    """
    script = start_script()
    if not script.exists():
        logger.warning("chatlog process: 启动脚本不在：%s", script)
        return None

    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    kwargs: dict = {}
    if sys.platform != "win32":
        # 自己一组 ⇒ 收的时候按组收，不用去猜子进程是谁。
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
            **kwargs,
        )
    except OSError as exc:
        logger.warning("chatlog process: 起不来：%s", exc)
        return None
    return proc.pid


def _wait_ready(client, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _healthy(client):
            return True
        time.sleep(POLL_INTERVAL)
    return _healthy(client)


def kill_tree(pid: int) -> None:
    """**只杀自己起的那一棵**。不按映像名全局杀（裁定 105：谁起的都杀不得）。"""
    if not pid:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
            )
            return
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (OSError, AttributeError, ProcessLookupError):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


def ensure_process(
    client,
    *,
    timeout: float = DEFAULT_START_TIMEOUT,
    lock_timeout: float = DEFAULT_LOCK_TIMEOUT,
) -> tuple[Optional[ProcessHandle], str]:
    """探活 → 不在就起 → 等它应答。

    回 ``(句柄, 那一句人话)``：

    - 句柄 ``None`` ⇒ 起不来（人话是为什么）。
    - 句柄的 ``started_by_us`` 是 False ⇒ **那个不是我们起的**：借它，收的时候不许动它。
    """
    if _healthy(client):
        return ProcessHandle(False, 0, "已经在跑"), "采集进程已经在跑，借它"

    try:
        with _exclusive(lock_path(), timeout=lock_timeout):
            # 拿到锁再探一次：等锁的那段时间里，别人可能刚把它起好了。
            if _healthy(client):
                return ProcessHandle(False, 0, "刚刚被别人起了"), "采集进程刚刚被别人起了，借它"

            pid = spawn_process()
            if pid is None:
                return None, "采集进程起不来"

            if not _wait_ready(client, timeout):
                kill_tree(pid)
                return None, "采集进程起来了，但一直没应答"

            return ProcessHandle(True, pid, "自己起的"), "采集进程这一轮由腿自己起"
    except TimeoutError:
        return None, "另一个采集腿正占着这个端口"


def release_process(handle: Optional[ProcessHandle]) -> None:
    """收掉**自己起的那一个**。借来的那个一动不动。"""
    if handle is None or not handle.started_by_us or not handle.pid:
        return
    kill_tree(handle.pid)
    logger.info("chatlog process: 收掉这一轮自己起的那个（pid=%s）", handle.pid)


def activate(client, *, timeout: float = DEFAULT_START_TIMEOUT) -> tuple[bool, str]:
    """把它起起来，并让它**留在那儿**（用户刚打开这一条时那一手）。

    与 :func:`ensure_process` 的差别只有一处：**归属**。

    - 采集那一轮要的是「起 → 采 → 收」（:func:`ensure_process` ＋ :func:`release_process`）：
      那一轮结束就把**自己起的那一个**收掉，用完即收。
    - 用户刚打开这一条时那一手要的是「它现在就在」：屏上那颗开关说了"开"，就得说得出
      它活没活（用户 10-23：「我打开还是关闭都是显示未连接」⇒ 开关若只改一个布尔、
      却把进程留在那儿，它就是个假动作）。所以这里起完**不收**——它活到用户关掉这一条为止。

    借来的那个（已经在跑、不是我们起的）照样不收不换。
    """
    handle, note = ensure_process(client, timeout=timeout)
    if handle is None:
        return False, note
    if not handle.started_by_us:
        return True, "采集进程已经在跑"
    return True, "采集进程已经起来了"
