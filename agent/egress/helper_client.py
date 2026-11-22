"""提权侧车（egress helper）的门：一条管道、一枚口令、一个「本进程是不是那个侧车」。

为什么要有这一层（2026-11-22 用户拍板方案 B）：

TUN 要建网卡，就得一个管理员进程。而把整包 exe 设成 ``requireAdministrator`` 等于让用户
每开一次 Plobi 点一次 UAC（方案 C，当场排除）。所以权限被搬到**侧车**：装的时候注册一条
「最高权限、登录触发」的计划任务，由它跑 ``agent/egress/helper.py``；主进程与后端保持
普通权限，要动 TUN / 起引擎时从这条管道递话过去。

这里的三个决定，都是有代价的选项里挑的：

* 传输用 ``multiprocessing.connection``（Windows 上就是命名管道），不自造帧格式。它自带
  长度前缀与 pickle，本仓不需要第二个 RPC 方言；代价是**管道默认谁都能连**（命名管道的
  ACL 由 listen 方给，而 multiprocessing 没给我们留那个口子）——所以口令在下一层。
* 口令是 32 字节随机串，落在 ``$PLOBI_HOME/egress/helper/token``，写盘时用
  ``engine.restrict_file_acl`` 收到「只有本人」。侧车每条命令都先比口令，比不过就断，
  **不执行、不回细节**：一个连着就能指挥管理员进程的管道，比没有这个管道危险得多。
* 管道名带 home 的短哈希：两台 profile 各跑一份侧车时不许互相串门，而固定名字做不到。
  名字里没有 PLOBI_* 环境变量——AGENTS.md 明写不新增，测试换名走 :func:`use_pipe_name`。

时钟与超时：每条命令**一次连接一次应答**，不在管道上挂长连接。开关拨一下是秒级操作，
长连接只会换来「侧车没了而客户端还挂着」这种更难查的形态。
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from multiprocessing import AuthenticationError
from pathlib import Path
from typing import Any, Optional

#: 管道名前缀。Windows 侧完整形状是 ``\\.\pipe\<此处>-<home 哈希>``。
PIPE_PREFIX = "plobi-egress-helper"

#: 口令长度。32 字节随机 ⇒ 猜不中；写盘后只有本人可读。
TOKEN_BYTES = 32

#: 一次「喂，在吗」。侧车没起来时这条是常态，不许让它等成 UI 卡顿。
PING_TIMEOUT = 0.6

#: 真正要干活的那些（起引擎、建网卡）宁可等：25 秒是 ``engine.TUN_START_WAIT_SECONDS``
#: 那一档的两倍多，够侧车把 mihomo 起完再回话。
CALL_TIMEOUT = 60.0

PROTOCOL_VERSION = 1

#: 计划任务名。安装器（apps/desktop/installer.nsh）按这个名字注册，这里按这个名字叫醒它。
#: 两边必须逐字一致——改一处的结果是「用户拨了开关，什么都没发生」。
TASK_NAME = "Plobi Egress Helper"

#: 上次试图叫醒侧车的时间。侧车不在是常态（没装、非 Windows、已卸载），而每次保存网络
#: 设置都跑一遍 schtasks 会白白给操作加几百毫秒。冷却期内的失败直接记成「叫不醒」。
_START_COOLDOWN = 30.0
_last_start_attempt = 0.0

#: 测试/特殊宿主用来换管道名的口子。进程内改，不读环境变量（AGENTS.md：不新增 PLOBI_*）。
_PIPE_NAME_OVERRIDE: Optional[str] = None

#: 本进程就是侧车时为真。**这是防递归的唯一开关**：侧车内部调 ``engine.ensure`` 时若又
#: 想「转交给侧车」，那就自己连自己、自己等自己——表现为一次谁也没动的挂起。
_SERVING = False


def use_pipe_name(name: Optional[str]) -> None:
    """换管道名（仅测试/诊断用）。传 ``None`` 恢复默认。"""
    global _PIPE_NAME_OVERRIDE
    _PIPE_NAME_OVERRIDE = name


def mark_serving(serving: bool = True) -> None:
    """声明「本进程就是侧车」。由 :mod:`agent.egress.helper` 在开始收话前调用。"""
    global _SERVING
    _SERVING = serving


def is_serving() -> bool:
    """本进程是不是那个提权侧车。"""
    return _SERVING


def supported() -> bool:
    """这条线在不在这个平台上。Windows 命名管道、POSIX 域套接字都有，但没有侧车时会直接判空。"""
    return sys.platform in ("win32", "linux", "darwin")


def home_dir(home: str | Path | None = None) -> Path:
    """侧车状态落在哪个家。延迟导入 ``engine`` 是为了不给 engine→helper 的循环导入添砖。"""
    if home is not None:
        return Path(home)
    from agent.egress import engine as _engine

    return Path(_engine._default_home())


def pipe_name(home: str | Path | None = None) -> str:
    """这个 home 对应的管道全名。"""
    if _PIPE_NAME_OVERRIDE:
        return _PIPE_NAME_OVERRIDE
    digest = hashlib.sha1(str(home_dir(home)).encode("utf-8")).hexdigest()[:8]
    name = f"{PIPE_PREFIX}-{digest}"
    if sys.platform == "win32":
        return rf"\\.\pipe\{name}"
    # POSIX 侧只给测试与 Linux 诊断用：路径长度有上限，短哈希刚好够。
    return str(Path(os.environ.get("TMPDIR") or "/tmp") / f"{name}.sock")


def token_path(home: str | Path | None = None) -> Path:
    return home_dir(home) / "egress" / "helper" / "token"


def read_token(home: str | Path | None = None) -> str:
    """读口令。没有就是空串——「没口令」与「口令不对」在这里不区分，两者都连不上。"""
    try:
        return token_path(home).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def issue_token(home: str | Path | None = None) -> str:
    """侧车启动时造一枚口令：写盘 + 收 ACL，返回明文给内存里的那份。

    每次都换新：上一次侧车留下的口令没有任何价值，留着只是多一条可被读走的东西。
    """
    path = token_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(TOKEN_BYTES)
    path.write_text(token + "\n", encoding="utf-8")
    try:
        from agent.egress import engine as _engine

        _engine.restrict_file_acl(path)
    except Exception:  # noqa: BLE001 - ACL 收不住不该让侧车起不来，口令本身就是防线
        pass
    return token


def _client(pipe: str, token: str):
    """建一个连接对象。超时不在这里——见 :func:`_connect` 的注释。

    ``authkey`` 是这条线上**唯一**的准入：``multiprocessing.connection`` 在握手阶段先用
    HMAC 挑战应答验口令，验不过就断开、一个字节的 pickle 都不解。不给它口令的后果不是
    「别人能读」，而是「任何本机进程能往一个管理员进程里扔任意 pickle」——那是执行任意代码。
    """
    from multiprocessing.connection import Client

    family = "AF_PIPE" if sys.platform == "win32" else "AF_UNIX"
    return Client(pipe, family=family, authkey=token.encode("utf-8"))


def _connect(pipe: str, token: str, timeout: float):
    """连上就把超时设到它的 socket 上；连不上抛 OSError 家族。

    ``multiprocessing.connection.Client`` 在 Windows 上**不吃 timeout 参数**（3.11 的签名
    只有 address/family/authkey），所以超时只能在拿到句柄之后设——不设，侧车卡住时这条
    调用会一直等到对方关闭，UI 就跟着转圈。
    """
    conn = _client(pipe, token)
    try:
        sock = getattr(conn, "sock", None)
        if sock is not None:
            sock.settimeout(timeout)
    except OSError:
        pass
    return conn


def _send(conn, payload: dict[str, Any]) -> None:
    conn.send(payload)


def call(
    cmd: str,
    *,
    timeout: float = CALL_TIMEOUT,
    home: str | Path | None = None,
    **args: Any,
) -> dict[str, Any]:
    """递一条命令给侧车，回 ``{"ok": bool, "result": ..., "error": str, "said": str}``。

    **不抛异常**。侧车不在、口令过期、命令炸了，都塌成 ``ok=False`` 加一句人话——调用方
    要判的是「这条线现在能不能用」，而不是分辨十二种异常类型。
    """
    pipe = pipe_name(home)
    token = read_token(home)
    if not token:
        return {"ok": False, "result": None, "error": "sidecar-token-missing", "said": "侧车没有口令文件（还没起过）"}
    payload = {
        "v": PROTOCOL_VERSION,
        "token": token,
        "cmd": cmd,
        "args": args,
        "pid": os.getpid(),
    }
    started = time.monotonic()
    try:
        conn = _connect(pipe, token, timeout)
    except AuthenticationError as exc:
        # 口令对不上：侧车可能刚重启换了新口令，而手上这份是旧的。这不是「攻击」的常态，
        # 更常见的是主进程读缓存没刷新——所以照实说「口令不对」，不要吓人也不要静默。
        return {
            "ok": False,
            "result": None,
            "error": f"sidecar-auth-failed: {type(exc).__name__}: {exc}",
            "said": "侧车不认这枚口令（它可能刚重启过，重新握一次手）",
            "elapsed": round(time.monotonic() - started, 3),
        }
    except (OSError, EOFError) as exc:
        return {
            "ok": False,
            "result": None,
            "error": f"sidecar-unavailable: {type(exc).__name__}: {exc}",
            "said": "侧车没在跑（计划任务没注册，或者没起来）",
            "elapsed": round(time.monotonic() - started, 3),
        }
    try:
        _send(conn, payload)
        reply = conn.recv()
    except (OSError, EOFError) as exc:
        return {
            "ok": False,
            "result": None,
            "error": f"sidecar-dropped: {type(exc).__name__}: {exc}",
            "said": "侧车收下命令后断开了（看它自己的日志）",
            "elapsed": round(time.monotonic() - started, 3),
        }
    finally:
        try:
            conn.close()
        except OSError:
            pass
    if not isinstance(reply, dict):
        return {"ok": False, "result": None, "error": "sidecar-garbled", "said": "侧车回了看不懂的东西"}
    reply.setdefault("elapsed", round(time.monotonic() - started, 3))
    return reply


def ping(*, home: str | Path | None = None, timeout: float = PING_TIMEOUT) -> dict[str, Any]:
    """侧车在不在。答话里带 pid 与是否真拿到了管理员——「起了但没提权」要看得出来。"""
    return call("ping", home=home, timeout=timeout)


def available(*, home: str | Path | None = None, timeout: float = PING_TIMEOUT) -> bool:
    return bool(ping(home=home, timeout=timeout).get("ok"))


def should_delegate(*, home: str | Path | None = None, timeout: float = PING_TIMEOUT) -> bool:
    """本次调用该不该把活转给侧车。

    三件事同时成立才转：① 本进程不是侧车（否则自己连自己）；② 这个平台有这条线；③ 侧车
    现在真答得上话。少一条就老实本进程干——普通权限干得了的那些（起 mixed 端口引擎、
    写 HKCU 代理）本来就该在这里干，转交只会多一跳。
    """
    if _SERVING or not supported():
        return False
    return available(home=home, timeout=timeout)


def start(*, home: str | Path | None = None, wait: float = 3.0) -> bool:
    """叫醒侧车：``schtasks /Run``，然后等它答话。叫得醒返回 True。

    为什么不是「起一个 exe」：侧车必须以管理员身份运行，而普通进程直接 CreateProcess 一个
    提权进程会弹 UAC——那正是方案 B 要避开的东西。计划任务是这个系统里**唯一**允许「以最高
    权限运行、还不打扰用户」的通道，所以叫醒它的动作也得走这条通道。

    它平时不必常驻：登录时任务会起一次，空闲一段时间会自己退（见 helper 的 ``--idle-exit``）。
    用户真正拨开关时再叫一遍，提权进程只在你用网的那几分钟活着。
    """
    global _last_start_attempt
    if _SERVING or not supported():
        return False
    if available(home=home):
        return True
    now = time.monotonic()
    if now - _last_start_attempt < _START_COOLDOWN:
        return False
    _last_start_attempt = now

    from agent.egress.winspawn import NO_WINDOW

    try:
        done = subprocess.run(
            ["schtasks.exe", "/Run", "/TN", TASK_NAME],
            capture_output=True,
            timeout=10,
            creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if done.returncode != 0:
        # 任务没注册（老版本装上去的、非 Windows 手动跑的）——这不是错误，只是没有那只手。
        return False

    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if available(home=home, timeout=0.4):
            return True
        time.sleep(0.2)
    return False


def describe(*, home: str | Path | None = None) -> dict[str, Any]:
    """给可见面看的那一行：侧车在不在、是不是管理员、管道叫什么。只读，不点头。"""
    reply = ping(home=home)
    return {
        "available": bool(reply.get("ok")),
        "pipe": pipe_name(home),
        "token": bool(read_token(home)),
        "detail": reply.get("error") or reply.get("said") or "",
        "result": reply.get("result") if reply.get("ok") else None,
    }


def dumps(reply: dict[str, Any]) -> str:
    """诊断用的一行 JSON（日志与测试断言共用，避免各自拼格式）。"""
    return json.dumps(reply, ensure_ascii=False, sort_keys=True)
