"""同一条会话的**排队闸**（``WP-OFFLOAD-QUEUE``，11-22）。

为什么非要有它：一条客席会话在对方那头是**一条活的上下文**，不是我们随便拼的
命令行。两边同时 ``session/load`` + ``session/prompt``，就是我们替它把两个 turn
叠进同一条对话里——它会自己打断自己，回来的话还分不清回的是哪一句。

做法取 T3 Code 的那条语义，最笨也最对：**不打断、按到达顺序排队**。
同一 (厂商, 会话号) 只有一把锁，后来的等前面那句发完。排队不是失败，所以
不设默认超时；要限时由发的人自己带着超时来，超了就说"前面那句还在跑"。

**范围**：进程内的闸。Plobi 后端是单进程（WS + 工具都在一个进程里），够用；
哪天拆成多进程，这里就得换成文件锁或台账里的认领位。
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator


_LOCK = threading.Lock()
#: (厂商, 会话号) → 那把锁。永不回收：会话数是几十的量级，回收只会引入竞态。
_TURNS: dict[tuple[str, str], threading.Lock] = {}
#: 每把锁上排了几个（读数用：让人知道自己是第几个）。
_WAITING: dict[tuple[str, str], int] = {}


def _key(vendor: str, session_id: str) -> tuple[str, str]:
    return (str(vendor or "").strip().lower(), str(session_id or "").strip())


def _lock_for(key: tuple[str, str]) -> threading.Lock:
    with _LOCK:
        lock = _TURNS.get(key)
        if lock is None:
            lock = threading.Lock()
            _TURNS[key] = lock
        return lock


def queue_depth(vendor: str, session_id: str) -> int:
    """这条会话上正在等的人数（不算已经拿到闸的那个）。"""
    with _LOCK:
        return int(_WAITING.get(_key(vendor, session_id), 0))


@contextmanager
def session_turn(vendor: str, session_id: str, *, timeout: float | None = None) -> Iterator[dict[str, Any]]:
    """占住这条会话发一句（离开时放出）。

    交回来的读数：``queued_seconds`` 排了多久、``queue_depth`` 排在第几。
    ``timeout`` 是**排队**的上限，不是这句话跑多久——超了抛 :class:`QueueTimeout`，
    让上层照实说"前面那句还没完"，而不是等了一个钟头再报超时。
    """
    key = _key(vendor, session_id)
    if not key[1]:
        # 没有会话号的（新建一条）不存在"同一条"可撞，直接放行。
        yield {"queued_seconds": 0.0, "queue_depth": 0}
        return
    lock = _lock_for(key)
    started = time.time()
    with _LOCK:
        _WAITING[key] = int(_WAITING.get(key, 0)) + 1
    try:
        got = lock.acquire(timeout=timeout) if timeout is not None else lock.acquire()
        if not got:
            raise QueueTimeout(
                f"这条会话前面那句还在跑（已等 {time.time() - started:.0f}s）")
    finally:
        with _LOCK:
            _WAITING[key] = max(0, int(_WAITING.get(key, 1)) - 1)
    try:
        yield {"queued_seconds": round(time.time() - started, 2),
               "queue_depth": queue_depth(vendor, session_id) + 1}
    finally:
        lock.release()


class QueueTimeout(RuntimeError):
    """排队等到超时：不是它失败了，是前面那句还没发完。"""
