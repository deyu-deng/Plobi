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


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


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
