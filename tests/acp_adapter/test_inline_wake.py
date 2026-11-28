"""通道一：开着的屏内联接活。

这里验的是「宿主怎么回话」，不是「port 怎么开」——所以不真起 socket，
直接调 `_serve_inline_wake`，把它当一条纯函数用。
"""

from __future__ import annotations

import asyncio

import pytest

from acp_adapter.server import PlobiACPAgent
from acp_adapter.session import SessionManager


class _Manager(SessionManager):
    """只要一个会话表，不要数据库、不要 agent 工厂。"""

    def __init__(self) -> None:
        self._table: dict[str, object] = {}

    def add(self, session_id: str, *, is_running: bool = False) -> None:
        from acp_adapter.session import SessionState

        state = SessionState(session_id=session_id, agent=None, cwd=".")
        state.is_running = is_running
        self._table[session_id] = state

    def get_session(self, session_id: str):
        return self._table.get(session_id)


@pytest.fixture()
def agent(monkeypatch) -> PlobiACPAgent:
    # 锁与身份都不参与这一层的判定，别把它们拖进来。
    monkeypatch.setattr("acp_adapter.server._inline_agent_name", lambda: "")
    return PlobiACPAgent(session_manager=_Manager())


@pytest.fixture()
def manager(agent) -> _Manager:
    return agent.session_manager


def test_a_screen_i_do_not_own_says_so_instead_of_pretending(agent):
    """不是我的会话就老实说没有——`say` 那边要照着这句话去起进程。"""

    reply = asyncio.run(agent._serve_inline_wake("sess-别人的", "干活"))

    assert reply == {"ok": False, "reason": "no_session"}


def test_an_empty_request_is_rejected(agent):
    assert asyncio.run(agent._serve_inline_wake("", "干活"))["reason"] == "bad_request"
    assert asyncio.run(agent._serve_inline_wake("sess-1", ""))["reason"] == "bad_request"


def test_my_own_screen_takes_the_turn_in_place(agent, manager, monkeypatch):
    """我的会话、我闲着 ⇒ 收下这一条，起一轮，回话「收好了」。"""
    manager.add("sess-1")
    turns: list[tuple[str, str]] = []

    async def _fake_prompt(prompt_text, session_id, **kwargs):
        turns.append((prompt_text, session_id))

    monkeypatch.setattr(agent, "prompt", _fake_prompt)

    async def _go():
        reply = await agent._serve_inline_wake("sess-1", "把表填了")
        await asyncio.sleep(0)  # 让 create_task 那一轮跑起来
        return reply

    reply = asyncio.run(_go())

    assert reply == {"ok": True, "mode": "inline"}
    assert turns == [("把表填了", "sess-1")], "投进来的话必须原样走用户打字那条 prompt()"


def test_a_busy_screen_queues_instead_of_refusing(agent, manager, monkeypatch):
    """正在跑一轮 ⇒ 这句排到那一轮后面，不能拒——拒了就等于把话丢了。"""
    manager.add("sess-1", is_running=True)
    turns: list[str] = []

    async def _fake_prompt(prompt_text, session_id, **kwargs):
        turns.append(prompt_text)

    monkeypatch.setattr(agent, "prompt", _fake_prompt)

    async def _go():
        reply = await agent._serve_inline_wake("sess-1", "排个队")
        await asyncio.sleep(0)
        return reply

    assert asyncio.run(_go()) == {"ok": True, "mode": "queued"}
    assert turns == ["排个队"]


def test_a_crashed_turn_does_not_take_the_host_down(agent, manager, monkeypatch):
    """那一轮跑挂了是它自己的事，宿主得留着——否则下一次唤醒连门都找不到。"""
    manager.add("sess-1")

    async def _boom(*args, **kwargs):
        raise RuntimeError("这一轮炸了")

    monkeypatch.setattr(agent, "prompt", _boom)

    async def _go():
        reply = await agent._serve_inline_wake("sess-1", "把这件办砸")
        await asyncio.sleep(0)
        return reply

    assert asyncio.run(_go())["ok"] is True
