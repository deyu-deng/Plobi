"""`reply_to`（引用条：这一轮在回哪一条）在三层之间必须接得上。

2026-10-25 `b0449dd2` 把它加进了 `tui_gateway` 的 run_kwargs、`AIAgent.run_conversation`
的签名和 `build_turn_context` 的入参，**唯独漏了 `agent.conversation_loop.run_conversation`
自己的那一层**。于是桌面每带一次引用就抛
`TypeError: run_conversation() got an unexpected keyword argument 'reply_to'`，整轮不跑
（这台 10-26 22:2x 的 `logs/tui_gateway_crash.log` 里连着三次）。

这一档只钉"链条完整"这一件事：给中间那层传 `reply_to` 必须不炸，而且必须真的落到
`build_turn_context` 手上——否则下一轮还是会静默丢掉"在回哪一条"。
"""

import pytest

import agent.conversation_loop as conversation_loop
import run_agent


class _StopAfterPrologue(Exception):
    """走到 build_turn_context 就停：这一档只验管线，不跑模型。"""


def test_conversation_loop_accepts_reply_to_and_forwards_it(monkeypatch):
    captured = {}

    def fake_build_turn_context(*args, **kwargs):
        captured.update(kwargs)
        raise _StopAfterPrologue()

    monkeypatch.setattr(conversation_loop, "build_turn_context", fake_build_turn_context)

    with pytest.raises(_StopAfterPrologue):
        conversation_loop.run_conversation(object(), "在回哪一条", reply_to="owner")

    assert captured.get("reply_to") == "owner"


def test_none_reply_to_also_travels(monkeypatch):
    """不引用时传 None 也一样要通——这条路上不能只认有值的那一半。"""

    captured = {}

    def fake_build_turn_context(*args, **kwargs):
        captured.update(kwargs)
        raise _StopAfterPrologue()

    monkeypatch.setattr(conversation_loop, "build_turn_context", fake_build_turn_context)

    with pytest.raises(_StopAfterPrologue):
        conversation_loop.run_conversation(object(), "普通一轮")

    assert captured.get("reply_to") is None


def test_agent_forwarder_passes_reply_to_down(monkeypatch):
    """`AIAgent.run_conversation` 是转发层：它漏了，上面那层修好也没用。"""

    seen = {}

    def fake_run_conversation(*args, **kwargs):
        seen.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(conversation_loop, "run_conversation", fake_run_conversation)

    result = run_agent.AIAgent.run_conversation(object(), "hi", reply_to="l2:agenda")

    assert result == {"ok": True}
    assert seen.get("reply_to") == "l2:agenda"
