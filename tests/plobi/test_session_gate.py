"""会话排队闸的契约（``WP-OFFLOAD-QUEUE``，11-22）。

要守住的只有两件事：
1. **同一条会话上的两句不重叠**——叠在一起，就是我们替它把两个 turn 塞进一条上下文；
2. **排队不是失败**——占不到闸要说"前面那句还没完"，不许报成发送失败，
   更不许静默丢掉那条消息。
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent import session_gate                                   # noqa: E402
from plobi.delegation import external_sessions                   # noqa: E402


def test_two_turns_on_the_same_session_never_overlap():
    inside, peak = [], 0
    guard = threading.Lock()

    def run(tag):
        nonlocal peak
        with session_gate.session_turn("qoder", "s-1"):
            with guard:
                inside.append(tag)
                peak = max(peak, len(inside))
            time.sleep(0.05)
            with guard:
                inside.remove(tag)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak == 1


def test_different_sessions_do_not_wait_on_each_other():
    order = []

    def run(sid):
        with session_gate.session_turn("qoder", sid):
            time.sleep(0.15)
            order.append(sid)

    started = time.time()
    threads = [threading.Thread(target=run, args=(f"s-{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert time.time() - started < 0.3


def test_waiting_reports_how_long_and_how_many():
    holding = threading.Event()
    release = threading.Event()

    def hold():
        with session_gate.session_turn("qoder", "s-2"):
            holding.set()
            release.wait(2)

    t = threading.Thread(target=hold)
    t.start()
    holding.wait(2)
    try:
        with pytest.raises(session_gate.QueueTimeout) as excinfo:
            with session_gate.session_turn("qoder", "s-2", timeout=0.1):
                pass
        assert "前面那句还在跑" in str(excinfo.value)
    finally:
        release.set()
        t.join()


def test_no_session_id_means_no_queue():
    """新建一条没有"同一条"可撞，闸直接放行。"""
    with session_gate.session_turn("qoder", "") as turn:
        assert turn["queued_seconds"] == 0.0


# ── 发消息那一层：排队不是发送失败 ──────────────────────────────────

def test_send_reports_a_busy_session_as_waiting_not_failed(monkeypatch):
    """前面那句还没完 ⇒ 409 且 outcome=waiting；说清等的是**那一轮**，不是授权。"""
    release = threading.Event()

    def slow_send(vendor, session, text, *, timeout=600.0):
        release.wait(2)
        return {"ok": True, "status": 200}

    monkeypatch.setattr(external_sessions, "_send_now", slow_send)
    holding = threading.Event()

    def hold():
        with session_gate.session_turn("qoder", "s-3"):
            holding.set()
            release.wait(2)

    t = threading.Thread(target=hold)
    t.start()
    holding.wait(2)
    try:
        result = external_sessions.send_message(
            "qoder", {"id": "s-3", "project": str(Path.cwd())}, "接着说", queue_timeout=0.1)
        assert result["ok"] is False and result["status"] == 409
        assert result["outcome"] == "waiting" and result["waiting_on"] == "turn"
    finally:
        release.set()
        t.join()


def test_send_reports_how_long_it_queued(monkeypatch):
    monkeypatch.setattr(external_sessions, "_send_now",
                        lambda *a, **kw: {"ok": True, "status": 200})
    result = external_sessions.send_message("qoder", {"id": "s-4"}, "在吗")
    assert result["ok"] is True and result["queued_seconds"] >= 0
