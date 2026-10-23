"""信源那颗开关的**另一半**：真的把这一条的本机服务拉起来／收掉。

用户 2026-10-23 对着信源那一屏说：「**微信那个按钮形同虚设。我打开还是关闭都是显示未连接。
然后我希望开始和关闭是会真的切断这条信源的采集程序。比如微信是会真的把采集工具 chatlog 关掉。
而不影响软件任何功能。**」

⇒ 那一颗开关勾的是两件事：**配置层的 `enabled`**（下一轮采不采它）与本机那个服务活不活着。
这个模块只管后一半，而且是**照声明办事**（`SourceSpec.sidecar_port` / `sidecar_start`）：
公共层仍然不出现任何信源名字（判据 4），加一条带本机服务的信源只是多声明两格。

三条口径写在前面，免得后来的人把它读成"顺便关掉了一个进程"：

1. **只动那个端口上的那个进程**（不是按进程名杀）。端口由声明给，杀之前再核一次它真的在听；
   顺手误杀的代价是用户丢一个别的程序，这个模块不许有那种"差不多"。
2. **失败是一句读数**，不抛：开关本身是配置动作，服务拉不起来是这一条的读数（与采集失败同族）。
   调用方把 `detail` 原样挂到响应上，屏上说得出来"开了，但服务没起来"。
3. **关掉不影响软件别的功能**：进程是这条信源自己的（chatlog 只服务微信这一腿），
   杀掉它不碰网关、不碰桌面壳、不碰别的信源。
"""

from __future__ import annotations

import logging
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

logger = logging.getLogger(__name__)

#: 拉起之后最多等多久再看它有没有在听（秒）。等太久会把一次点击变成一次挂起。
START_WAIT_SECONDS = 6.0
#: 收掉之后最多等多久看端口有没有松手（秒）。
STOP_WAIT_SECONDS = 3.0


def port_listening(port: int, *, host: str = "127.0.0.1", timeout: float = 0.4) -> bool:
    """端口上有没有人在应答。**连接一次**就算，不看是哪个进程。"""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def owning_pids(port: int) -> list[int]:
    """听在这个端口上的进程号（可能不止一个，IPv4/IPv6 各一条）。"""
    try:
        import psutil
    except ImportError:  # pragma: no cover - 这台有 psutil；没有就是"读不到"，不是"没有"
        logger.warning("sidecar: psutil 不在，读不到端口 %s 的属主", port)
        return []

    found: set[int] = set()
    for conn in psutil.net_connections(kind="inet"):
        try:
            if conn.laddr and int(conn.laddr.port) == int(port) and conn.pid:
                found.add(int(conn.pid))
        except (AttributeError, TypeError):
            continue
    return sorted(found)


def _terminate(pid: int) -> bool:
    """先礼后兵：TERM，等一会儿，还活着才 KILL。"""
    try:
        import psutil
    except ImportError:  # pragma: no cover
        return False

    try:
        process = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return True
    try:
        process.terminate()
    except psutil.NoSuchProcess:
        return True                        # 它已经不在了——这就是"收掉了"，不是失败
    except psutil.AccessDenied as exc:
        logger.warning("sidecar: 收不掉 pid %s：%s", pid, exc)
        return False
    try:
        process.wait(timeout=STOP_WAIT_SECONDS)
    except psutil.TimeoutExpired:
        try:
            process.kill()
        except psutil.NoSuchProcess:
            pass
    except psutil.NoSuchProcess:
        pass
    return True


def _repo_root() -> Path:
    """`plobi/collectors/sidecar.py` → 仓库根。无盘符硬编码。"""
    return Path(__file__).resolve().parents[2]


def _spawn(script: str) -> Optional[int]:
    """把声明的那支脚本在后台拉起来，回 pid（起不来就是 None）。"""
    path = _repo_root() / script
    if not path.exists():
        logger.warning("sidecar: 启动脚本不存在：%s", path)
        return None
    if path.suffix.lower() == ".ps1" and sys.platform == "win32":
        argv = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(path)]
    else:
        argv = [sys.executable if path.suffix.lower() == ".py" else "bash", str(path)]

    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        child = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 creationflags=flags)
    except OSError as exc:
        logger.warning("sidecar: 拉不起来 %s：%s", script, exc)
        return None
    return int(child.pid)


def apply(spec, *, enabled: bool,
          listening: Callable[[int], bool] = port_listening,
          finder: Callable[[int], Iterable[int]] = owning_pids,
          killer: Callable[[int], bool] = _terminate,
          spawn: Callable[[str], Optional[int]] = _spawn,
          wait_seconds: float = START_WAIT_SECONDS) -> Optional[dict]:
    """按声明把这一条的常驻服务**拉起来或收掉**。没有声明端口就什么都不做（回 ``None``）。

    `enabled=True`：已经在听就不动它；没在听就按声明的脚本拉起，并**等一下**再看它活没活。
    `enabled=False`：在听就收掉（只收听在那个端口上的进程），然后核端口松没松手。

    返回的形状照"读数"写：``{"port", "action", "listening", "detail"}``。
    """
    port = getattr(spec, "sidecar_port", None)
    if port is None:
        return None

    port = int(port)
    was_listening = bool(listening(port))

    if enabled:
        if was_listening:
            return {"action": "already_running", "detail": "服务本来就在跑，没有动它",
                    "listening": True, "port": port}
        script = str(getattr(spec, "sidecar_start", "") or "")
        if not script:
            return {"action": "cannot_start", "detail": "这一条没声明怎么拉起自己的服务",
                    "listening": False, "port": port}
        pid = spawn(script)
        deadline = time.monotonic() + max(0.0, float(wait_seconds))
        now_listening = False
        while True:
            if listening(port):
                now_listening = True
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(0.25)
        return {"action": "started" if now_listening else "started_not_listening",
                "detail": (f"已拉起（pid {pid}），{port} 在应答" if now_listening
                           else f"已拉起（pid {pid}），但 {port} 还没开始应答"),
                "listening": now_listening, "port": port}

    if not was_listening:
        return {"action": "already_stopped", "detail": "服务本来就没在跑", "listening": False, "port": port}

    pids = [int(pid) for pid in finder(port)]
    stopped = [pid for pid in pids if killer(pid)]
    time.sleep(0.2)
    still = bool(listening(port))
    # 那句话以**端口松没松手**为准，不以"我调用了几次 terminate"为准：
    # 上一轮实测里 killer 报了一句"收不掉"（进程其实已经消失），照调用结果写就会自相矛盾。
    return {"action": "stopped" if not still else "stop_incomplete",
            "detail": (f"已收掉听在 {port} 上的进程 {pids}" if not still
                       else f"{port} 还在应答：{pids or '（读不到属主）'} 没能全部收掉"),
            "listening": still, "port": port}
