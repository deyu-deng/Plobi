"""`say` 投递之后，让收件人**像收到一条用户消息那样**直接开工（裁定 200）。

用户 10-23 原话：「收件人收到消息会像收到用户消息一样直接开始工作。即使我不去打开
那个 Agent」，「本质就是用户给它发了一个消息一样」。

所以这里不加新语义：消息已经以 user 角色落进**对方自己的会话**（`channel._deliver`），
这一枚只做一件事——按 `ask` 已经跑通的那条 spawn 手，把那一屏续上一轮，然后立刻
返回（不等）。命令形状与 `ask` 同一份（`agent_ask.build_child_command`：共享层 +
agent 身份 + `--resume <那一屏>`），**不许**回到 `-p <profile>` 那套已判废的形状。

四条护栏（裁定 200，少一条就出事）：
1. 不等：`Popen` + 输出落 `logs/channel-wake.log`；`say` 的承诺是「投递即返回」。
2. 同一个 Agent 不并发两轮：标记文件记 pid，活着就跳过——两次＝两份钱、两条打架的回答。
3. 不唤醒唤醒链：被唤醒的那一轮（`PLOBI_WAKE=1`）再 `say` 时不再往下叫醒。
4. 叫不醒不算 `say` 失败：话已经送到了，照实回 `woke=false` + 原因。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import socket
import subprocess
import time
from datetime import datetime
from pathlib import Path

from . import agent_ask as AA

logger = logging.getLogger(__name__)

#: 唤醒进程的输出落这儿。打包版主进程不落盘，这条链出问题时必须有一份自己的证据。
WAKE_LOG = "channel-wake.log"

_WAKE_DIRNAME = "channel-wake"


def wake_prompt(sender: str, ticket_id: str = "", expect_answer: bool = False) -> str:
    """唤醒时补的那一句。**不重述消息内容**——它就在那一屏最上面。

    裁定 216：把这一跳的票号交给被唤醒的那一轮，它才有得汇报。`ticket_report`
    是旁路状态通道（不是第三枚嘴），所以这里只是告诉它票号，不重述消息、
    不改它该干什么。

    ``expect_answer``（``ask`` 走的那一趟）把口径从「去办这件事」换成「回答这一问」：
    派活那句会让它写「我做了什么」，而发问方等的是一个答案。
    """
    ticket_line = ""
    if ticket_id:
        ticket_line = (
            f"这一趟的授权票号是 `{ticket_id}`。做完用 "
            f'`ticket_report(ticket_id="{ticket_id}", state="ready", note="…")` 报一声；'
            "卡住报 blocked、要等人报 waiting，别只在聊天里说说就算了。\n"
        )
    if expect_answer:
        return (
            f"【{sender} 在问你一件事，问话就在这一屏最上面那条（以「[来自 {sender} · 时间]」开头）。】\n"
            "他在等你的答复：读完那条，直接把答案说出来，一两句话，答完就打住——"
            "不用写「我做了什么」的报告，也别反过来催他。\n"
            + ticket_line
        )
    return (
        f"【{sender} 刚给你发了一条消息，就在这一屏最上面那条（以「[来自 {sender} · 时间]」开头）。】\n"
        "它就等于用户亲手发给你的一条消息：去读它，按它说的做，做完用一两句话讲清你做了什么、卡在哪儿。\n"
        + ticket_line
        + "如果这件事需要反过来问他，就用 `ask` 问一次，别用 `say`——`say` 不会把他叫醒。"
    )


def _plobi_root() -> Path:
    try:
        from plobi_constants import get_plobi_home

        return get_plobi_home()
    except Exception:  # noqa: BLE001
        return Path.home() / ".plobi"


def _wake_dir() -> Path:
    root = _plobi_root() / "plobi" / _WAKE_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def _wake_log_path() -> Path:
    root = _plobi_root() / "logs"
    root.mkdir(parents=True, exist_ok=True)
    return root / WAKE_LOG


def _safe_slug(name: str) -> str:
    """标记文件名：ASCII 安全名，中文 Agent 名不许变成非法文件名。"""
    cleaned = "".join(ch for ch in name if ch.isalnum() or ch in "-_")[:40]
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    return f"{cleaned or 'agent'}-{digest}"


def _pid_alive(pid: int) -> bool:
    """这个 pid 现在还是个活进程吗？**只问，不投信号。**

    Windows 上 `os.kill(pid, 0)` 不是无探活副作用的（bpo-14484）：它落到
    `GenerateConsoleCtrlEvent(0, pid)`，给目标所在的整个控制台进程组发 Ctrl+C。
    用到这里的后果有两条，都直接踩在护栏 2 上：活着的上一轮被判成死的（同一 Agent
    并发两轮＝两份钱），以及同组的 pytest 主进程在 threading 等待里被打成
    `KeyboardInterrupt`。判活只有一份真相，在 `plobi_round_lock.pid_alive`——cron
    那一把锁用的也是它。
    """
    try:
        import plobi_round_lock as RL

        return RL.pid_alive(pid)
    except Exception:  # noqa: BLE001 - 锁模块不在，不代表这条唤醒不能判活
        if pid <= 0:
            return False
        if os.name == "nt":
            return False
        try:
            os.kill(pid, 0)  # windows-footgun: ok — POSIX-only branch（Windows 已在上方 return）
        except OSError:
            return False
        return True


def _already_waking(marker: Path) -> bool:
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
        return _pid_alive(int(data.get("pid") or 0))
    except (OSError, ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# 通道一：那一屏本来就开着，就让它自己接着跑（P0-2）
# ---------------------------------------------------------------------------
# 唤醒原来只有一条腿：起一个新进程。可那一屏往往是开着的（桌面端就把 ACP 会话
# 跑在原地），再起一个 = 两份钱 + 两条互相打架的历史（护栏 2 说的正是这个）。
# 所以先问一句：这一屏在不在你那儿？
#
# 活着的宿主把端口/令牌登记在 `channel-wake/live-acp/<pid>.json`（由
# `acp_adapter/server.py::_start_inline_wake_host` 落地）。这里连上去投一句，
# 它自己调 `prompt()` 起轮——和用户亲手打字走的是同一条路，消息、UI、落库全一致。
# 没人接、或那一屏不在 ⇒ 返回 `False`，退回起进程那条老路；
# **不许**因为通道一没接上就把这条消息丢了。

_INLINE_DIRNAME = "live-acp"

#: 连上就走，不排队：这是**本地回环**，卡住的只可能是对面没在收。
_INLINE_CONNECT_SECONDS = 2.0
#: 对面回话最多等这么久——它在回话里就已经把轮起好了，只差一句 `ok`。
_INLINE_REPLY_SECONDS = 8.0


def _inline_dir() -> Path:
    root = _wake_dir() / _INLINE_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def _inline_ask(entry_path: Path, session_id: str, prompt: str) -> tuple[bool, str]:
    """问一个登记过的屏：这一屏在不在你那儿？返回 ``(收下了没, 一句说明)``。"""
    try:
        host = json.loads(entry_path.read_text(encoding="utf-8"))
        port = int(host.get("port") or 0)
        token = str(host.get("token") or "")
        pid = int(host.get("pid") or 0)
    except (OSError, ValueError, TypeError):
        return False, "登记文件读不出来"
    if not port or not token or not _pid_alive(pid):
        # 硬杀（不留 atexit 机会）会留下这种死登记：顺手收掉，别让它年年堆积。
        # 只删刚读到的这一份，不扫目录——唤醒不该有「批量删东西」的权限。
        try:
            entry_path.unlink(missing_ok=True)
        except OSError:
            pass
        return False, "那个宿主已经不在了"

    line = json.dumps(
        {"token": token, "session_id": (session_id or "").strip(), "prompt": prompt},
        ensure_ascii=False,
    ).encode("utf-8") + b"\n"
    sock = None
    try:
        sock = socket.create_connection(("127.0.0.1", port), timeout=_INLINE_CONNECT_SECONDS)
        sock.settimeout(_INLINE_REPLY_SECONDS)
        sock.sendall(line)
        buf = b""
        while b"\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
        reply = json.loads(buf.split(b"\n", 1)[0].decode("utf-8") or "{}")
    except OSError as exc:
        return False, f"连不上那个宿主（{exc.__class__.__name__}）"
    except (ValueError, UnicodeDecodeError):
        return False, "宿主回的话看不懂"
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    if not isinstance(reply, dict):
        return False, "宿主回的话看不懂"
    if reply.get("ok"):
        return True, str(reply.get("mode") or "inline")
    return False, str(reply.get("reason") or "那边没收")


def _inline_deliver(session_id: str, prompt: str) -> tuple[bool, str]:
    """问遍所有活着的屏，谁认这一屏就谁来跑。"""
    if not (session_id or "").strip():
        return False, "不知道是哪一屏，没法内联"
    try:
        hosts = sorted(_inline_dir().glob("*.json"))
    except OSError:
        return False, "登记目录读不出来"
    if not hosts:
        return False, "没有活着的屏"

    reasons: list[str] = []
    for entry_path in hosts:
        ok, why = _inline_ask(entry_path, session_id, prompt)
        if ok:
            return True, why
        reasons.append(why)
    # 只用前两条：说明是给人看的，不是日志。
    return False, "；".join(reasons[:2])


def _release_round(marker) -> None:
    try:
        import plobi_round_lock as RL

        RL.release(marker)
    except Exception:  # noqa: BLE001
        logger.debug("say: round lock 没放掉", exc_info=True)


def _rehold_round(marker, *, agent_name: str, pid: int, holder: str) -> None:
    if marker is None:
        return
    try:
        Path(marker).write_text(
            json.dumps(
                {
                    "pid": int(pid),
                    "agent": agent_name,
                    "holder": holder,
                    "at": datetime.now().replace(microsecond=0).isoformat(),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError:
        logger.warning("say: round lock 没改成子进程 pid（不影响这一趟）", exc_info=True)


#: 起来之后盯多久才算「没立刻死」。
#: `say` 的承诺是「投递即返回」，这里最多多花 6 秒——用 6 秒换掉一个假的
#: `woke=true`，账怎么算都值。11-23 那次五个 `woke=true` 全假，事后全靠人
#: 翻 `channel-wake.log` 才对上账。
WAKE_VERIFY_SECONDS = 6.0
_VERIFY_POLL_SECONDS = 0.2
_LOG_TAIL_BYTES = 1200


def _pending_dir() -> Path:
    root = _plobi_root() / "pending" / "wake"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _log_tail(path: Path, limit: int = _LOG_TAIL_BYTES) -> str:
    """日志尾巴——不读全量，只要够指认死法的那几行。"""
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - limit))
            raw = handle.read()
    except OSError:
        return ""
    text = raw.decode("utf-8", errors="replace").strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return " / ".join(lines[-3:])[:400]


def _verify_spawn(proc, log_path: Path, *,
                  seconds: Optional[float] = None) -> tuple[bool, str]:
    """孩子真的在跑吗？返回 ``(还在跑, 死因)``。

    `Popen` 返回只证明 `CreateProcess` 成功，不证明它是个能干活的进程。
    11-23 现场：`argv[0]` 是 Electron 壳，孩子起来立刻因
    `Unable to move the cache: 拒绝访问` + `Gpu Cache Creation failed` 死掉，
    `wake_agent` 却已经写了 `woke=true` 就返回了——话在屏上，人没醒，
    下一次 `_already_waking` 看 marker 里的死 pid 判「过期」，又假成功一遍。

    所以这一枚必须真的等：等它退（退就是失败，带上日志尾巴），或者等过
    观察窗（活着才算起得来）。
    """
    # 默认值要运行期取：默认参数在定义时求值，抓不住后来改掉的观察窗。
    if seconds is None:
        seconds = WAKE_VERIFY_SECONDS
    deadline = time.monotonic() + max(0.0, seconds)
    while True:
        code = proc.poll()
        if code is not None:
            tail = _log_tail(log_path)
            detail = f"起手就退了（退出码 {code}）"
            if tail:
                detail += f"，日志尾部：{tail}"
            return False, detail
        if time.monotonic() >= deadline:
            return True, ""
        time.sleep(_VERIFY_POLL_SECONDS)


def _queue_pending_wake(entry, session_id: str, *, sender: str,
                        ticket_id: str, reason: str) -> Optional[Path]:
    """叫不醒的那一条记进待重放队列。

    护栏 4 说「叫不醒不算 `say` 失败」——那是指 `say` 不许因为叫不醒而报错，
    不是指这一条可以人间蒸发。消息已经在对方屏上，这一轮没跑起来，
    下次它被唤醒（或被用户打开）时得有人提醒它回头看那一屏。
    """
    payload = {
        "agent": entry.name,
        "session": session_id,
        "sender": sender,
        "ticket": ticket_id,
        "prompt": wake_prompt(sender, ticket_id),
        "reason": reason,
        "at": datetime.now().replace(microsecond=0).isoformat(),
    }
    name = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{_safe_slug(entry.name)}.json"
    path = _pending_dir() / name
    try:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return path
    except OSError:
        logger.warning("say: 唤醒待办没记下来（不影响这一趟）", exc_info=True)
        return None


def _pending_for(agent_name: str) -> list[Path]:
    """这个 Agent 攒下的、还没接着干的那几条（旧的在前）。"""
    suffix = f"-{_safe_slug(agent_name)}.json"
    try:
        return sorted(
            path for path in _pending_dir().glob("*.json") if path.name.endswith(suffix)
        )
    except OSError:
        return []


def _pending_prompt(files: list[Path]) -> str:
    """把待重放的那几条交代给这一轮——不然它们只是躺在磁盘上的文件。"""
    if not files:
        return ""
    return (
        f"\n另外你有 {len(files)} 条更早的消息**上一趟没被叫醒**"
        "（叫醒进程起手就退了，不是发件人的错）。它们也在各自的屏上："
        "这一轮做完之后，逐个去看一眼，别让它们烂在那儿。\n"
    )


def _clear_pending(files: list[Path]) -> None:
    """这一轮真起来了，待重放的账就清了——清不掉的留着下一趟继续提醒。"""
    for path in files:
        try:
            path.unlink()
        except OSError:
            logger.debug("say: 待重放清单没删掉 %s", path, exc_info=True)


def _argv_runnable(argv: list[str]) -> Optional[bool]:
    """``plobi_argv_runnable`` 的软引用：判定层不在就返回 None（不拦）。"""
    try:
        from plobi_cli.plobi_bin import plobi_argv_runnable
    except Exception:  # noqa: BLE001 - 命令行层缺席不该让唤醒整个炸掉
        return None
    try:
        return bool(plobi_argv_runnable(argv))
    except Exception:  # noqa: BLE001
        return None


def wake_agent(entry, session_id: str, *, sender: str,
               ticket_id: str = "",
               expect_answer: bool = False) -> tuple[bool, str]:
    """投递之后让那个 Agent 自己开始干。返回 ``(起没起得来, 一句说明)``。

    ``ticket_id`` 由裁定 216 的 router 票带进来，只进唤醒提示词，不改命令形状。

    ``expect_answer`` 给 ``ask`` 用：这一趟是**同步问答**，发问方坐着等答案，
    链路到它为止，不会自己转回去——所以「被唤醒的轮不再往下叫醒」那道护栏
    对它开口。``say`` 不传，防互叫死循环的立场不变。
    """
    if os.environ.get("PLOBI_WAKE") == "1" and not expect_answer:
        return False, "这一轮本身就是被唤醒的，不再往下叫醒（防止互相叫醒的死循环）。"

    marker = _wake_dir() / f"{_safe_slug(entry.name)}.json"
    if _already_waking(marker):
        return False, "它已经在干上一件活了，这条等它做完再看。"

    # 通道一：那一屏要是开着，让它自己接着跑，不起第二个进程。
    # 提示词与起进程那条腿**同源**（同一个 `wake_prompt` + 同一份欠账），
    # 免得两条路给出两种交代。跑道锁交给对面自己拿——只有它知道是不是
    # 已经有一轮在跑（那一轮该排队，不该被拒）。
    pending_inline = _pending_for(entry.name)
    inline_ok, inline_why = _inline_deliver(
        session_id,
        wake_prompt(sender, ticket_id, expect_answer) + _pending_prompt(pending_inline),
    )
    if inline_ok:
        _clear_pending(pending_inline)
        logger.info("say: 内联起轮 %s（%s）", entry.name, inline_why)
        return True, f"它那一屏本来就开着，已经在原地接着干了（{inline_why}）。"

    # 与 cron 共用同一把「这一屏同时只跑一轮」的锁（`plobi_round_lock`）：
    # 两个回合同时落进同一屏＝两份钱 + 两条互相打架的历史。
    try:
        import plobi_round_lock as RL

        round_lock = RL.acquire(entry.name, _plobi_root(), holder=f"say:{sender}")
    except Exception:  # noqa: BLE001 - 锁坏了不等于这一轮不能叫醒
        round_lock = None
    if round_lock is None:
        return False, "它正在跑另一轮（cron 或别的唤醒），这条等它做完再看。"

    # 上一趟没叫醒的那几条，这一趟一并交代；真起来了再销账（见 `_clear_pending`）。
    pending_before = _pending_for(entry.name)

    try:
        argv, env, cwd = AA.build_child_command(
            entry,
            wake_prompt(sender, ticket_id, expect_answer) + _pending_prompt(pending_before),
            asked_by=f"say:{sender}",
            resume_session=(session_id or "").strip() or None,
        )
    except Exception as exc:  # noqa: BLE001 - 叫不醒也不许炸掉说话人的回合
        logger.warning("say: 唤醒命令拼不出来（%s）", exc)
        _release_round(round_lock)
        return False, f"叫不醒它：{exc}"

    # 拼得出命令 ≠ 起得来：桌面端里 `plobi_argv_runnable` 会一眼看穿
    # 「宿主 exe + -m」这种跑不起来的形状，省得再上演一次假成功。
    if _argv_runnable(argv) is False:
        reason = (
            f"没有能跑命令行的 plobi（拼出来的 argv[0] 是 `{argv[0]}`，"
            "它当不了 Python 解释器）"
        )
        _release_round(round_lock)
        pending = _queue_pending_wake(
            entry, session_id, sender=sender, ticket_id=ticket_id, reason=reason
        )
        logger.warning("say: 唤醒不成立（%s）", reason)
        tail = f"；已记待重放（{pending.name}）" if pending else ""
        return False, f"叫不醒它：{reason}{tail}。"

    env["PLOBI_WAKE"] = "1"
    env["PLOBI_ASKED_BY"] = f"say:{sender}"
    # 这一轮写进那一屏的 prompt 是**系统事件**（居中、无气泡），不是谁说的话。
    env["PLOBI_MESSAGE_KIND"] = "system"
    # 而且它连记都不进库（11-25 用户裁定）：唤醒提示只是一个启动信号，真正要它
    # 处理的那句话，发问方已经用 _deliver 落好了；再记一遍就是同一件事两份。
    env["PLOBI_EPHEMERAL_PROMPT"] = "1"

    log_path = _wake_log_path()
    try:
        with log_path.open("ab") as handle:
            handle.write(
                f"\n=== wake {entry.name} @ {datetime.now().replace(microsecond=0).isoformat()} "
                f"(from {sender}, session {session_id}) ===\n".encode("utf-8")
            )
            handle.flush()
            proc = subprocess.Popen(  # noqa: S603 — argv 是本模块自己拼的
                argv,
                stdin=subprocess.DEVNULL,
                stdout=handle,
                stderr=handle,
                env=env,
                cwd=cwd,
                start_new_session=True,
            )
    except FileNotFoundError:
        _release_round(round_lock)
        return False, "这台机器上找不到可执行的命令行，叫不醒它。"
    except OSError as exc:
        logger.warning("say: 唤醒失败（%s）", exc)
        _release_round(round_lock)
        return False, f"叫不醒它：{exc}"

    # P0-1：起没起来不许靠猜。等它退（退＝失败，带日志尾巴），或等过观察窗。
    verified, why = _verify_spawn(proc, log_path)
    if not verified:
        _release_round(round_lock)
        try:
            marker.unlink()
        except OSError:
            logger.debug("say: 唤醒标记没删掉", exc_info=True)
        pending = _queue_pending_wake(
            entry, session_id, sender=sender, ticket_id=ticket_id, reason=why
        )
        logger.warning("say: 唤醒没成功（%s）", why)
        tail = f"；已记待重放（{pending.name}）" if pending else ""
        return False, f"叫不醒它：{why}{tail}。"

    # 锁改记**子进程**的 pid：父进程这一趟马上就返回了，记父 pid 会立刻变成
    # 「过期」，cron 那边就能同时开跑；记子 pid，cron 看得到它在跑。
    _rehold_round(round_lock, agent_name=entry.name, pid=proc.pid, holder=f"say:{sender}")

    try:
        marker.write_text(
            json.dumps(
                {
                    "pid": proc.pid,
                    "agent": entry.name,
                    "session": session_id,
                    "from": sender,
                    "started": datetime.now().replace(microsecond=0).isoformat(),
                    # 这一条是「盯过才写的」，不是 `Popen` 一返回就写。
                    "verified": True,
                    "verify_seconds": WAKE_VERIFY_SECONDS,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError:
        logger.warning("say: 唤醒标记没写进去（不影响这一趟）", exc_info=True)

    # 这一轮真起来了，且它的提示词里已经交代过那几条，账可以销了。
    _clear_pending(pending_before)

    return True, (
        f"已叫醒它（后台 pid {proc.pid}，盯了 {WAKE_VERIFY_SECONDS:.0f} 秒没退），"
        "它会在这一屏里接着干。"
    )
