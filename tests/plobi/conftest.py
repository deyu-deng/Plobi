"""微信那条腿的采集进程：**单测不许真起它、也不许借它**。

那个引擎产物（`chatlog.exe`，30 MB）就在仓里，单测一旦碰到「没人应答 ⇒ 自己起」
那条路，就会真的把它拉起来——还会占住 :5030、留下一棵进程树。

⇒ 这里把 `spawn_process` 钉成「起不来」，让那一格在单测里**快速失败**；
再把**探活**钉成「真客户端一律不在应答」——否则单测会**借**那台机器上真的在跑的那个
进程（装机版的桌面拉起它），把一次单测变成**一次真采集**：读数随机器状态变，
还会真的往池里落东西（2026-11-20 实测踩过：`test_collect_sweep.py` 那一档）。

要测起停那一格的（`tests/plobi/test_chatlog_leg_process.py`）自己在用例里把
`spawn_process` 钉成假的 ⇒ 用例照样过，且**不许**真起进程。
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _never_spawn_the_collect_process(monkeypatch):
    """单测里那个采集进程**永远起不来**（起停那一格另有专测）。"""
    from plobi.collectors.chatlog import process

    monkeypatch.setattr(process, "spawn_process", lambda: None)


@pytest.fixture(autouse=True)
def _never_borrow_the_real_collect_process(monkeypatch):
    """单测里也**借不到**那台机器上真在跑的那个进程（只对真客户端生效）。

    假客户端（各档自己造的）照旧按它们自己说的答——那一格另有专测。
    """
    from plobi.collectors.chatlog import process
    from plobi.collectors.chatlog.client import ChatlogClient

    original = process._healthy

    def _healthy(client) -> bool:
        if isinstance(client, ChatlogClient):
            return False
        return original(client)

    monkeypatch.setattr(process, "_healthy", _healthy)
