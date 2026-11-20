"""信源那颗开关的**另一半**：真的把这一条的本机服务拉起来／收掉。

用户 2026-10-23 对着信源那一屏说：「**微信那个按钮形同虚设。我打开还是关闭都是显示未连接。
然后我希望开始和关闭是会真的切断这条信源的采集程序。比如微信是会真的把采集工具 chatlog 关掉。
而不影响软件任何功能。**」

⇒ 那一颗开关勾的是两件事：**配置层的 `enabled`**（下一轮采不采它）与本机那个服务活不活着。
这个模块只管后一半，而且是**照声明办事**（`SourceSpec.sidecar_port` / `sidecar_start`）：
公共层仍然不出现任何信源名字（判据 4），加一条带本机服务的信源只是多声明两格。

**2026-11-20（`WP-WECHAT-LEG-OWNS-PROCESS`）：这个模块不再有 spawn 代码。**

那个进程**归采集腿所有**了——采集那一轮自己起它、采完收掉（用户 11-20 拍「侧车的家长换成
各自的采集腿」「不用叫 sidecar 了，只是一个采集腿代码逻辑而已」）。公共层若还自己 spawn，
就成了**第二个家长**：起它、起哪个、收不收，公共层与那一族各写一份 ⇒ 两处真源。
现在「怎么起它」也是**声明**（`sidecar_start` 是那一族自己给的那只手），公共层只照它喊一声。

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
import time
from typing import Callable, Iterable, Optional

logger = logging.getLogger(__name__)

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
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    except psutil.NoSuchProcess:
        pass
    except psutil.AccessDenied as exc:
        # `terminate()` 收下条令了，但**等它退出**这一步也要开进程句柄——这台就撞上过
        # 一个属主不同/提权过的进程：`OpenProcess` 给 `AccessDenied`。那一颗开关此刻
        # 的真实状态是"命令发了、我没资格确认它退没退"，不是"收掉了"，也不是"整个动作失败"。
        # 从前这一支没接住，于是**用户点开关拿到一个 500**——那是比"收不掉"更坏的形状。
        logger.warning("sidecar: 等不到 pid %s 退出（权限不够）：%s", pid, exc)
        return False
    return True


def apply(spec, *, enabled: bool,
          listening: Callable[[int], bool] = port_listening,
          finder: Callable[[int], Iterable[int]] = owning_pids,
          killer: Callable[[int], bool] = _terminate) -> Optional[dict]:
    """按声明把这一条的本机服务**准备起来或收掉**。没有声明端口就什么都不做（回 ``None``）。

    `enabled=True`：**准备 + 照声明喊那一族自己的手**（`sidecar_start`）——
    公共层**不再有 spawn 代码**（2026-11-20 `WP-WECHAT-LEG-OWNS-PROCESS`）：那个进程归采集腿所有，
    怎么起、起哪个、收不收全是那一族自己家的事；公共层只照声明喊它一声，
    起停的知识不在两处。

    准备那一手是**声明**（如"先替他取一次本机密钥"）：公共层不认识它是谁的、做什么——
    它抛了也不许把这次点击变成 500——最坏就是没做成，那句原因照实并进读数。

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

        prepared = ""
        prepare = getattr(spec, "sidecar_prepare", None)
        if prepare is not None:
            try:
                ok, note = prepare()
                prepared = ("" if ok else "准备那一步：") + (note or "")
                if ok:
                    logger.debug("sidecar prepare ok: %s", note)
            except Exception as exc:  # noqa: BLE001 - 一次点击不该被准备步骤炸掉
                prepared = f"准备那一步没做成：{type(exc).__name__}。"
                logger.warning("sidecar prepare failed: %s", exc)

        start = getattr(spec, "sidecar_start", None)
        if start is None:
            return {"action": "cannot_start", "detail": "这一条没声明怎么拉起自己的服务",
                    "listening": False, "port": port, "prepare": prepared}

        try:
            ok, note = start()
        except Exception as exc:  # noqa: BLE001 - 一次点击不该被"起服务"炸掉
            logger.warning("sidecar start failed: %s", exc)
            return {"action": "start_failed", "detail": f"起不来：{type(exc).__name__}。",
                    "listening": False, "port": port, "prepare": prepared}
        if not ok:
            return {"action": "start_failed", "detail": str(note or "起不来"),
                    "listening": False, "port": port, "prepare": prepared}

        now_listening = bool(listening(port))
        return {"action": "started" if now_listening else "started_not_listening",
                "prepare": prepared,
                "detail": (str(note or "") if note else
                           (f"已拉起，{port} 在应答" if now_listening
                            else f"已拉起，但 {port} 还没开始应答")),
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
