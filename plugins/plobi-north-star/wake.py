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
import subprocess
from datetime import datetime
from pathlib import Path

from . import agent_ask as AA

logger = logging.getLogger(__name__)

#: 唤醒进程的输出落这儿。打包版主进程不落盘，这条链出问题时必须有一份自己的证据。
WAKE_LOG = "channel-wake.log"

_WAKE_DIRNAME = "channel-wake"


def wake_prompt(sender: str) -> str:
    """唤醒时补的那一句。**不重述消息内容**——它就在那一屏最上面。"""
    return (
        f"【{sender} 刚给你发了一条消息，就在这一屏最上面那条（以「[来自 {sender} · 时间]」开头）。】\n"
        "它就等于用户亲手发给你的一条消息：去读它，按它说的做，做完用一两句话讲清你做了什么、卡在哪儿。\n"
        "如果这件事需要反过来问他，就用 `ask` 问一次，别用 `say`——`say` 不会把他叫醒。"
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
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _already_waking(marker: Path) -> bool:
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
        return _pid_alive(int(data.get("pid") or 0))
    except (OSError, ValueError, TypeError):
        return False


def wake_agent(entry, session_id: str, *, sender: str) -> tuple[bool, str]:
    """投递之后让那个 Agent 自己开始干。返回 ``(起没起得来, 一句说明)``。"""
    if os.environ.get("PLOBI_WAKE") == "1":
        return False, "这一轮本身就是被唤醒的，不再往下叫醒（防止互相叫醒的死循环）。"

    marker = _wake_dir() / f"{_safe_slug(entry.name)}.json"
    if _already_waking(marker):
        return False, "它已经在干上一件活了，这条等它做完再看。"

    try:
        argv, env, cwd = AA.build_child_command(
            entry,
            wake_prompt(sender),
            asked_by=f"say:{sender}",
            resume_session=(session_id or "").strip() or None,
        )
    except Exception as exc:  # noqa: BLE001 - 叫不醒也不许炸掉说话人的回合
        logger.warning("say: 唤醒命令拼不出来（%s）", exc)
        return False, f"叫不醒它：{exc}"

    env["PLOBI_WAKE"] = "1"
    env["PLOBI_ASKED_BY"] = f"say:{sender}"

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
        return False, "这台机器上找不到可执行的命令行，叫不醒它。"
    except OSError as exc:
        logger.warning("say: 唤醒失败（%s）", exc)
        return False, f"叫不醒它：{exc}"

    try:
        marker.write_text(
            json.dumps(
                {
                    "pid": proc.pid,
                    "agent": entry.name,
                    "session": session_id,
                    "from": sender,
                    "started": datetime.now().replace(microsecond=0).isoformat(),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError:
        logger.warning("say: 唤醒标记没写进去（不影响这一趟）", exc_info=True)

    return True, f"已叫醒它（后台 pid {proc.pid}），它会在这一屏里接着干。"
