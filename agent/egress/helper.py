"""提权侧车：那个真正拿着管理员令牌、替主进程动引擎与 TUN 的进程。

它只做三件事：听管道、认口令、把命令交给 ``agent.egress`` 里已有的实现。没有任何自己的
网络逻辑——引擎怎么起、网卡怎么建、守卫怎么拦，全部还是 ``engine`` / ``tun`` 那一套，
侧车只是**换了一个权限更高的身体**去调它们。这条边界必须守住：一旦侧车开始自己发明
启动流程，同一个开关就会有两种行为，而用户看到的是哪一种取决于谁先到。

为什么要独立进程（方案 B，2026-11-22 定）：

* 提权只该覆盖「必须提权的那一小段」。整包 exe 设 ``requireAdministrator`` 是方案 C——
  每次开 Plobi 点一次 UAC，用户当场否掉。
* 计划任务以**当前用户**身份、最高权限、登录触发 ⇒ 不需要把整机权限借给一个聊天软件，
  退出登录自动消失，卸载时随安装器一起摘掉。
* 主进程保持普通权限，于是「Plobi 只是个普通程序」这件事对系统仍然成立：它不会因为有
  管理员令牌而顺手改坏别的东西。

单实例：管道名是按 home 算出来的固定名字，第二个侧车 Listen 会直接失败 ⇒ 退出码 2。
这不是错误，是「已经有一个在岗」。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
from multiprocessing import AuthenticationError
from multiprocessing.connection import Listener
from pathlib import Path
from typing import Any, Callable, Optional

from agent.egress import helper_client
from agent.egress import tun as _tun

_LOG = logging.getLogger("plobi.egress.helper")

#: 退出码：正常退出 / 已经有一个在岗 / 这个平台没有这条线。
EXIT_OK = 0
EXIT_ALREADY_SERVING = 2
EXIT_UNSUPPORTED = 3

#: 引擎那几条命令会占住很久（TUN 启动窗口 25 秒），互斥执行——两个 engine.ensure
#: 同时跑会各自看到「没有我们的进程」然后各起一个，端口上打架。
_COMMAND_LOCK = threading.Lock()


def _log_path(home: str | Path | None = None) -> Path:
    return helper_client.home_dir(home) / "logs" / "egress-helper.log"


def _setup_logging(home: str | Path | None = None, *, verbose: bool = False) -> None:
    """日志落 ``$PLOBI_HOME/logs/egress-helper.log``。

    侧车是计划任务拉起的：没有控制台、没有父进程读它的 stdout。日志文件是它**唯一**能被
    人看见的地方——所以启动第一行就写清楚 pid、是否真拿到了管理员、管道叫什么。
    """
    path = _log_path(home)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    handlers: list[logging.Handler] = []
    if path.parent.exists():
        handlers.append(logging.FileHandler(path, encoding="utf-8"))
    if verbose or not handlers:
        handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
        force=True,
    )


def _is_admin() -> tuple[bool, str]:
    """侧车到底有没有拿到管理员——「起了但没提权」必须在上屏的读数里看得出来。"""
    try:
        return _tun.is_admin()
    except Exception as exc:  # noqa: BLE001 - 判权限失败不该让侧车起不来
        return False, f"{type(exc).__name__}: {exc}"


def _report_dict(report: Any) -> dict[str, Any]:
    """``EngineReport`` → 可 JSON 的 dict。字段逐个抄，不用 ``asdict``：

    ``notes`` 是 tuple，``asdict`` 会给一串 list，而屏幕那边按 tuple 拼的字符串会变样；
    手写这一层反而更稳，并且以后加字段漏了会立刻被测试抓住。
    """
    if report is None:
        return {}
    return {
        "state": getattr(report, "state", ""),
        "detail": getattr(report, "detail", ""),
        "mixed_port": getattr(report, "mixed_port", 0),
        "workdir": getattr(report, "workdir", ""),
        "config_source": getattr(report, "config_source", ""),
        "pid": getattr(report, "pid", None),
        "probe": getattr(report, "probe", ""),
        "notes": list(getattr(report, "notes", ()) or ()),
    }


def _table(home: str | Path | None):
    """侧车自己读表。表是唯一事实来源（裁定 94），侧车不接「你按我说的干」这种指令。"""
    from agent.egress.rules import load_table

    try:
        return load_table(home=home)
    except TypeError:
        return load_table()


def _cmd_ping(args: dict[str, Any], home: str | Path | None) -> dict[str, Any]:
    admin, why = _is_admin()
    return {
        "pid": os.getpid(),
        "admin": admin,
        "admin_detail": why,
        "protocol": helper_client.PROTOCOL_VERSION,
        "pipe": helper_client.pipe_name(home),
        "python": sys.executable,
        "started_at": _STARTED_AT,
        "platform": sys.platform,
    }


def _cmd_status(args: dict[str, Any], home: str | Path | None) -> dict[str, Any]:
    from agent.egress import engine

    return _report_dict(engine.status(_table(home), home=home))


def _cmd_engine_ensure(args: dict[str, Any], home: str | Path | None) -> dict[str, Any]:
    from agent.egress import engine

    wait_s = float(args.get("wait_s") or 6.0)
    with _COMMAND_LOCK:
        return _report_dict(engine.ensure(_table(home), home=home, wait_s=wait_s))


def _cmd_engine_stop(args: dict[str, Any], home: str | Path | None) -> dict[str, Any]:
    from agent.egress import engine

    with _COMMAND_LOCK:
        return _report_dict(engine.stop_ours(_table(home), home=home))


def _cmd_engine_reload(args: dict[str, Any], home: str | Path | None) -> dict[str, Any]:
    from agent.egress import engine

    with _COMMAND_LOCK:
        return _report_dict(engine.reload_engine(_table(home), home=home))


def _cmd_tun_preflight(args: dict[str, Any], home: str | Path | None) -> dict[str, Any]:
    workdir = args.get("workdir") or None
    return _tun.preflight(_table(home), workdir=workdir)


#: 命令表。加命令只在这里加一行——侧车不该长出一套「谁的 if 分支都能改」的路由。
_COMMANDS: dict[str, Callable[[dict[str, Any], Any], dict[str, Any]]] = {
    "ping": _cmd_ping,
    "status": _cmd_status,
    "engine.ensure": _cmd_engine_ensure,
    "engine.stop": _cmd_engine_stop,
    "engine.reload": _cmd_engine_reload,
    "tun.preflight": _cmd_tun_preflight,
}

_STARTED_AT = time.time()


def commands() -> tuple[str, ...]:
    """侧车认哪些命令。客户端与测试都读这一份，别各抄一遍。"""
    return tuple(sorted(_COMMANDS))


def handle(request: Any, *, home: str | Path | None = None) -> dict[str, Any]:
    """执行一条请求，回 ``{"ok": bool, "result": ..., "error": ...}``。

    独立成纯函数是为了能在测试里**不建管道**地验每一条命令的行为：管道的连通性归管道，
    命令语义归这里。
    """
    if not isinstance(request, dict):
        return {"ok": False, "result": None, "error": "request-not-a-dict"}
    cmd = request.get("cmd")
    if not isinstance(cmd, str) or cmd not in _COMMANDS:
        return {"ok": False, "result": None, "error": f"unknown-command: {cmd!r}"}
    args = request.get("args") or {}
    if not isinstance(args, dict):
        return {"ok": False, "result": None, "error": "args-not-a-dict"}
    try:
        result = _COMMANDS[cmd](args, home)
    except Exception as exc:  # noqa: BLE001 - 一条命令炸了不许掀掉侧车
        _LOG.warning("command %s raised %s: %s", cmd, type(exc).__name__, exc)
        return {"ok": False, "result": None, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "result": result, "error": ""}


def _serve_connection(conn: Any, home: str | Path | None) -> None:
    """一条连接一条命令。收、验、做、回、断。"""
    try:
        try:
            request = conn.recv()
        except (EOFError, OSError) as exc:
            _LOG.debug("peer vanished before sending: %s", exc)
            return
        cmd = request.get("cmd") if isinstance(request, dict) else None
        started = time.monotonic()
        reply = handle(request, home=home)
        reply["cmd"] = cmd
        reply["elapsed"] = round(time.monotonic() - started, 3)
        _LOG.info("cmd=%s ok=%s elapsed=%.2fs%s", cmd, reply.get("ok"), reply["elapsed"],
                  "" if reply.get("ok") else f" error={reply.get('error')}")
        try:
            conn.send(reply)
        except (EOFError, OSError) as exc:
            _LOG.debug("could not hand back the answer for %s: %s", cmd, exc)
    finally:
        try:
            conn.close()
        except OSError:
            pass


def serve(
    home: str | Path | None = None,
    *,
    pipe: Optional[str] = None,
    ready: Optional[Callable[[dict[str, Any]], None]] = None,
    idle_s: float = 0.0,
    max_requests: Optional[int] = None,
) -> int:
    """在管道上待命，直到没人要它。

    :param ready: 起来之后回调一次，带上「pid / admin / pipe」——测试靠它拿就绪信号，
        计划任务不需要（它写完日志就走）。
    :param idle_s: 多久没有命令就自己退（0 = 一直待到注销）。只给开发机与自愈用：
        常驻是计划任务的本分，而手动跑起来的那个不该在盘上留一个没人认领的管理员进程。
    :param max_requests: 处理满多少条就退。测试用；生产传 ``None``。
    """
    if not helper_client.supported():
        _LOG.error("this platform has no sidecar line (%s)", sys.platform)
        return EXIT_UNSUPPORTED

    helper_client.mark_serving(True)
    name = pipe or helper_client.pipe_name(home)
    token = helper_client.issue_token(home)
    admin, why = _is_admin()

    try:
        listener = Listener(name, family="AF_PIPE" if sys.platform == "win32" else "AF_UNIX",
                            authkey=token.encode("utf-8"))
    except OSError as exc:
        _LOG.info("another sidecar already owns %s (%s) — standing down", name, exc)
        helper_client.mark_serving(False)
        return EXIT_ALREADY_SERVING

    _LOG.info("sidecar up pid=%s admin=%s pipe=%s python=%s%s",
              os.getpid(), admin, name, sys.executable, "" if admin else f" ({why})")
    if not admin:
        _LOG.warning("这条侧车没有管理员权限 —— TUN 仍然会失败。计划任务是不是没按「最高权限」注册？")

    if ready is not None:
        try:
            ready({"pid": os.getpid(), "admin": admin, "pipe": name, "token": token})
        except Exception:  # noqa: BLE001 - 回调是观测者，不是参与者
            _LOG.debug("ready callback raised", exc_info=True)

    handled = 0
    last_seen = time.monotonic()
    stop_at = {"why": ""}

    if idle_s > 0:
        def _watchdog() -> None:
            nonlocal last_seen
            while True:
                time.sleep(min(1.0, idle_s / 4.0))
                if time.monotonic() - last_seen >= idle_s:
                    stop_at["why"] = f"idle for {idle_s:.0f}s"
                    # 关掉监听是**唯一**能让阻塞在 accept 上的线程醒来的办法：命名管道没有
                    # 「等 N 秒就返回」这种收法，给它设 socket 超时既不可移植也不可靠。
                    try:
                        listener.close()
                    except OSError:
                        pass
                    return

        threading.Thread(target=_watchdog, daemon=True, name="sidecar-idle").start()

    try:
        while True:
            try:
                conn = listener.accept()
            except (OSError, AuthenticationError) as exc:
                if stop_at["why"]:
                    _LOG.info("%s — standing down", stop_at["why"])
                    return EXIT_OK
                # 认不过口令的握手会走到这里（AuthenticationError 不是 OSError 的近亲，
                # 得单独列出来）。一次错过口令不该让侧车倒下：它是本机唯一那个能提权的进程。
                _LOG.warning("accept failed: %s: %s", type(exc).__name__, exc)
                continue
            last_seen = time.monotonic()
            worker = threading.Thread(target=_serve_connection, args=(conn, home), daemon=True)
            worker.start()
            handled += 1
            if max_requests is not None and handled >= max_requests:
                worker.join(timeout=5.0)
                return EXIT_OK
    finally:
        try:
            listener.close()
        except OSError:
            pass
        helper_client.mark_serving(False)
        _LOG.info("sidecar down after %d connection(s)", handled)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="plobi-egress-helper",
                                     description="Plobi 提权侧车：替主进程动引擎与 TUN")
    parser.add_argument("--home", default=None, help="PLOBI_HOME（默认按产品规则解析）")
    parser.add_argument("--pipe", default=None, help="覆盖管道名（诊断用）")
    parser.add_argument("--idle-exit", type=float, default=0.0,
                        help="空闲多少秒自己退（0 = 常驻，计划任务用 0）")
    parser.add_argument("--max-requests", type=int, default=None, help="处理满 N 条就退（测试用）")
    parser.add_argument("--list-commands", action="store_true", help="列出认的命令后退出")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    if args.list_commands:
        print(json.dumps(commands(), ensure_ascii=False))
        return EXIT_OK

    home = args.home or None
    _setup_logging(home, verbose=args.verbose)
    return serve(home, pipe=args.pipe, idle_s=args.idle_exit, max_requests=args.max_requests)


if __name__ == "__main__":
    raise SystemExit(main())
