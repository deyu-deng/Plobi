"""微信那条腿**自己**管它要用的那个本地采集进程（chatlog 引擎）。

它**就是这条腿的一部分**，不是一个独立子系统——用户 11-20 09:3x 原话：
「不用叫 sidecar 了，地位没有这么高。只是一个采集腿代码逻辑而已。」

这一格只做一件事：**一轮采集 = 一个会话**。

    ensure_process()    探活 → 不在就起 → 等它应答
    release_process()   **只收自己起的那一个**（借来的那个一动不动）

**跨会话的归属**（2026-11-20 补，`WP-WECHAT-LEG-PROCESS-OWNER`）：后端一死，Windows 上
子进程**不会跟着走** ⇒ `:5030` 上会留下一个孤儿，而下一个会话按裁定 105 把它当**别人的**
⇒ 借、一辈子不收 ⇒ 「用完即收」在桌面退出那一瞬间就不成立了（而且是静默的）。
⇒ 起它时记一份**归属登记**（那棵树的 pid ＋ 起它的那个 pid，落数据家）；下一轮探到有人
应答时先看这份登记：**主人没了**且**命令行确实是 chatlog** ⇒ 那是**我们上一轮留下的**
⇒ 认领它（主人换成我）⇒ 那一轮用完照旧收掉。读不到（psutil 不在）、登记不在、pid 被别的
进程占用了 ⇒ **不认领**（裁定 105：只动自己那个）。

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

import atexit
import contextlib
import json
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
# 归属登记：那个进程是谁起的（跨会话也要说得清）
# --------------------------------------------------------------------------- #
#
# 2026-11-20 补上的那一格（`WP-WECHAT-LEG-PROCESS-OWNER`）。
#
# 漏在哪：腿起的那一份是**后端的子进程**。后端一死（桌面退出、后端崩了），
# Windows 上子进程**不会跟着走** ⇒ `:5030` 上留下一个孤儿。
# 下一个会话的腿探到「有人在应答」⇒ 按裁定 105 当成**别人的**⇒ **借它、一辈子不收**
# ⇒ 「用完即收」在桌面退出的那一瞬间就不成立了，而且**它是静默的**
# （那一行还显示「已就绪」，没人看得出多了一个常驻）。
#
# 怎么收：**登记归属**。起它的时候记下「这棵树的 pid ＋ 起它的那个 pid」，
# 下一轮探到有人应答时先看这份登记：
#
# - 登记在、主人还活着 ⇒ 那是**活着的别人** ⇒ 借（不动它）。
# - 登记在、主人没了、它还活着、而且**命令行确实是 chatlog** ⇒ 那是**我们上一轮留下的**
#   ⇒ **认领**（主人换成我）⇒ 那一轮结束照旧收掉 ⇒ 「用完即收」跨会话也成立。
# - 读不到（`psutil` 不在）、登记不在、pid 被别的进程占用了 ⇒ **不认领**
#   （宁可让它多活一会儿，也不许动一个不是我们起的进程——裁定 105）。
#
# 落点也在数据家：`$PLOBI_HOME/chatlog/collect.owner.json`（不落 C 盘）。


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
# 归属登记：读与写
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class OwnerRecord:
    """一份归属登记：`tree_pid` 那棵进程树是 `owner_pid` 起的。

    `create_time` 是那个 pid 的**进程创建时刻**——pid 会被 Windows 复用，
    光看 pid 会对上别人 ⇒ 认错人就会杀掉一个与我们无关的进程（裁定 105）。
    """

    tree_pid: int
    owner_pid: int
    started_at: float
    create_time: Optional[float] = None


def owner_path() -> Path:
    """归属登记的落点：数据家 `chatlog/` 底下，与起停锁同目录。"""
    from plobi_constants import get_plobi_home

    return get_plobi_home() / "chatlog" / "collect.owner.json"


def _read_owner() -> Optional[OwnerRecord]:
    """读那份登记。读不到／写坏了 ⇒ **没有登记**（不是「有一个坏的登记」）。"""
    try:
        payload = json.loads(owner_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        created = payload.get("create_time")
        return OwnerRecord(
            tree_pid=int(payload["tree_pid"]),
            owner_pid=int(payload["owner_pid"]),
            started_at=float(payload.get("started_at") or 0.0),
            create_time=float(created) if created is not None else None,
        )
    except (KeyError, TypeError, ValueError):
        return None


def _write_owner(
    tree_pid: int,
    owner_pid: Optional[int] = None,
    create_time: Optional[float] = None,
) -> None:
    """写归属登记。写不进去只出声——**不许因为登记失败就不起进程**（那是两件事）。"""
    path = owner_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "tree_pid": int(tree_pid),
                    "owner_pid": int(owner_pid if owner_pid is not None else os.getpid()),
                    "started_at": time.time(),
                    "create_time": create_time,
                }
            ),
            encoding="utf-8",
        )
    except OSError:
        logger.warning("chatlog process: 归属登记写不进去：%s", path)


def _clear_owner(tree_pid: int) -> None:
    """清掉那份登记（**只清 tree_pid 对得上那一条**——别把别人的登记抹了）。"""
    record = _read_owner()
    if record is None or record.tree_pid != int(tree_pid):
        return
    try:
        owner_path().unlink(missing_ok=True)
    except OSError:
        logger.warning("chatlog process: 归属登记清不掉：%s", owner_path())


def _pid_alive(pid: int) -> Optional[bool]:
    """那个 pid 还在不在。**读不到就回 None**（不许当成"不在"，也不许当成"在"）。"""
    try:
        import psutil
    except ImportError:  # pragma: no cover - 这台有 psutil；没有就是"读不到"，不是"没有"
        return None
    try:
        return bool(psutil.pid_exists(int(pid)))
    except Exception:  # noqa: BLE001 - 读不到就是读不到
        return None


def _create_time(pid: int) -> Optional[float]:
    """那个 pid 的进程**创建时刻**。读不到 ⇒ ``None``（不是 0，也不是「现在」）。"""
    try:
        import psutil
    except ImportError:  # pragma: no cover
        return None
    try:
        return float(psutil.Process(int(pid)).create_time())
    except Exception:  # noqa: BLE001 - 读不到就是读不到
        return None


def _is_our_tree(pid: int, expect_create_time: Optional[float] = None) -> Optional[bool]:
    """那个 pid 是不是我们起的 chatlog 那一棵（看它的命令行 ＋ 它的创建时刻）。

    这道是 **pid 复用**的护栏：登记里那个 pid 要是被 Windows 派给了别的进程，
    认领它就是认错人 ⇒ 会去杀一个与我们无关的进程。读不到命令行 ⇒ **不认领**。

    `expect_create_time` 是登记里记下的那个创建时刻：对不上 ⇒ 那个 pid 已经不是
    我们起的那一个了（被复用了）⇒ 一律当「不是我们的」。
    """
    try:
        import psutil
    except ImportError:  # pragma: no cover
        return None
    try:
        proc = psutil.Process(int(pid))
    except psutil.NoSuchProcess:
        return False
    except Exception:  # noqa: BLE001
        return None
    if expect_create_time is not None:
        current = _create_time(pid)
        if current is None:
            return None
        if abs(current - expect_create_time) > 2.0:
            return False
    try:
        cmdline = proc.cmdline()
    except psutil.AccessDenied:
        return None
    except Exception:  # noqa: BLE001
        return False
    if not cmdline:
        return False
    return "chatlog" in " ".join(cmdline).lower()


def _orphan_tree() -> Optional[int]:
    """上一轮**我们自己**留下的那个进程（还活着、主人没了、确实是 chatlog 那一棵）。

    任何一格读不到 ⇒ **不认领**（回 `None`）：宁可让它多活一会儿，
    也不许动一个不是我们起的进程——裁定 105。
    """
    record = _read_owner()
    if record is None:
        return None
    if _pid_alive(record.tree_pid) is not True:
        return None
    # 主人还活着 ⇒ 那不是孤儿（另一个会话正管着它）；主人读不到 ⇒ 也不当孤儿。
    if _pid_alive(record.owner_pid) is not False:
        return None
    if _is_our_tree(record.tree_pid, record.create_time) is not True:
        return None
    return record.tree_pid


# --------------------------------------------------------------------------- #
# 退出时把自己起的那几棵带走
# --------------------------------------------------------------------------- #
#
# 与浏览器那一族（`tools/browser_tool.py` 的 owner_pid ＋ orphan reaper）**同一个形状**：
# 正常退出自己清（atexit），崩了留下的由**归属登记**兜底（下一个会话认领回收）。
# 两条合起来，「用完即收」才在**所有**退出路径上成立——只做后者，正常退出那一格还漏着。

#: 本进程起过、**还没收掉**的那几棵：`pid → 那个 pid 的创建时刻`。
_OWNED_TREES: dict[int, Optional[float]] = {}


def _reap_our_own_on_exit() -> None:
    """本进程退出时，把**自己起的那几棵**收掉。

    为什么（11-20 实测）：腿起的那一份是**后端的子进程**；Windows 上父进程一死，
    子进程**不会跟着走** ⇒ `:5030` 上留下一个孤儿。下一个会话的腿探到「有人在应答」
    ⇒ 按裁定 105 当成**别人的**⇒ 借它、一辈子不收 ⇒ 「用完即收」静默地不成立。

    **杀之前先验身份**（命令行里确实有 chatlog ＋ 创建时刻对得上）：pid 会被复用
    ⇒ 认错人会杀掉一个与我们无关的进程。身份对不上就**不动它**（浏览器那一族同一条护栏）。
    """
    for pid, created in list(_OWNED_TREES.items()):
        if _is_our_tree(pid, created) is not True:
            _OWNED_TREES.pop(pid, None)
            continue
        try:
            kill_tree(pid)
            logger.info("chatlog process: 退出前收掉自己起的那棵（pid=%s）", pid)
        except Exception:  # noqa: BLE001 - 退出路上不许抛
            pass
    _OWNED_TREES.clear()


atexit.register(_reap_our_own_on_exit)



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


def _adopt_or_borrow() -> tuple[ProcessHandle, str]:
    """**已经在应答**：是我们上一轮留下的就**认领**（这一轮用完收掉），是别人的就**借**（一动不动）。

    认领这一格必须在**锁里**做：两个腿同时撞上同一个孤儿时，只许一个认领——
    另一个看见主人还活着，就当借来的用（它借的是一个还活着的进程，不会被收）。
    不然两个会话会互相把对方正在用的那个收掉。
    """
    orphan = _orphan_tree()
    if orphan is None:
        return ProcessHandle(False, 0, "已经在跑"), "采集进程已经在跑，借它"

    # 主人换成我 ⇒ 下一个会话看得见「有人管着它」⇒ 不会来抢。
    _write_owner(orphan, os.getpid(), _create_time(orphan))
    _OWNED_TREES[orphan] = _create_time(orphan)
    logger.info("chatlog process: 认领上一轮留下的那个（pid=%s）", orphan)
    return ProcessHandle(True, orphan, "认领上一轮留下的"), "认领了上一轮留下的那个采集进程，这一轮用完收掉"


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
    - 句柄的 ``started_by_us`` 是 True ⇒ **是我们自己的**（这一轮起的，或上一轮留下的孤儿）⇒ 收得掉。
    """
    if _healthy(client):
        # 有人在应答：多半是**别人**起的（桌面常驻那份、或另一个腿起的）⇒ 借它。
        # 但先看有没有一份归属登记——那可能是**我们上一轮留下的孤儿**（主人已经没了）：
        # 认领它，这一轮用完收掉 ⇒ 「用完即收」跨会话也成立。
        if _read_owner() is None:
            return ProcessHandle(False, 0, "已经在跑"), "采集进程已经在跑，借它"
        try:
            with _exclusive(lock_path(), timeout=lock_timeout):
                return _adopt_or_borrow()
        except TimeoutError:
            # 拿不到锁就不认领：宁可借着，也不动一个归属不明、正被别人用着的进程。
            return ProcessHandle(False, 0, "已经在跑"), "采集进程已经在跑，借它"

    try:
        with _exclusive(lock_path(), timeout=lock_timeout):
            # 拿到锁再探一次：等锁的那段时间里，别人可能刚把它起好了。
            if _healthy(client):
                return _adopt_or_borrow()

            # 没人应答，但登记里还留着一个**我们上一轮留下的**孤儿 ⇒ 先收掉它
            # （它占着端口、挂着残骸），再起我们自己的。
            orphan = _orphan_tree()
            if orphan is not None:
                kill_tree(orphan)
                _clear_owner(orphan)
                _OWNED_TREES.pop(orphan, None)
                logger.info("chatlog process: 收掉上一轮留下的那个（pid=%s）", orphan)

            pid = spawn_process()
            if pid is None:
                return None, "采集进程起不来"

            # 归属登记：谁起的这棵、主人是谁、它什么时候起的 ⇒ 下一个会话认得出来
            #（跨会话回收靠它；创建时刻是防 pid 复用的那道）。
            created = _create_time(pid)
            _write_owner(pid, os.getpid(), created)
            _OWNED_TREES[pid] = created

            if not _wait_ready(client, timeout):
                kill_tree(pid)
                _clear_owner(pid)
                _OWNED_TREES.pop(pid, None)
                return None, "采集进程起来了，但一直没应答"

            return ProcessHandle(True, pid, "自己起的"), "采集进程这一轮由腿自己起"
    except TimeoutError:
        return None, "另一个采集腿正占着这个端口"


def release_process(handle: Optional[ProcessHandle]) -> None:
    """收掉**自己起的那一个**。借来的那个一动不动。"""
    if handle is None or not handle.started_by_us or not handle.pid:
        return
    kill_tree(handle.pid)
    _clear_owner(handle.pid)
    _OWNED_TREES.pop(handle.pid, None)
    logger.info("chatlog process: 收掉自己那个（pid=%s）", handle.pid)


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
