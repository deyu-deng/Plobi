"""裁定 216：Router→Router 协议层的行为契约。

测的是**关系**，不是快照：每一跳自动开授权票、`reply_to` 续接同一 Thread、
被撤销的票在起一轮之前真的拦住、`ticket_report` 是旁路状态通道不是第三枚嘴、
授权层坏了不挡说话。全部用假 SessionDB／假名册／假 wake／独立 sqlite，
不起真子进程、不写真家。
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[2]
PLUGIN_DIR = REPO / "plugins" / "plobi-core"
PKG_NAME = "plobi_router_protocol_test_pkg"


@pytest.fixture(scope="module")
def CH():
    """按包方式加载（channel.py 有相对导入 ``from . import agent_ask``）。"""
    if PKG_NAME not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            PKG_NAME, PLUGIN_DIR / "__init__.py",
            submodule_search_locations=[str(PLUGIN_DIR)],
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        module.__package__ = PKG_NAME
        module.__path__ = [str(PLUGIN_DIR)]
        sys.modules[PKG_NAME] = module
        spec.loader.exec_module(module)
    return importlib.import_module(f"{PKG_NAME}.channel")


class _Entry:
    archived = False

    def __init__(self, name: str):
        self.name = name


class _Registry:
    def __init__(self, names):
        self.agents = {n: _Entry(n) for n in names}

    def get(self, name):
        return self.agents.get(name)


class _FakeSessionDB:
    def __init__(self):
        self.sessions: dict[str, list[dict]] = {}
        self.appended: list[tuple] = []

    def latest_conversational_session_for_agent(self, agent_id):
        return next(iter(self.sessions), "")

    def create_session(self, session_id, *, source=None, agent_id=None):
        self.sessions.setdefault(session_id, [])
        return session_id

    def append_message(self, session_id, *, role=None, content=None,
                       asked_by=None, kind=None, reply_to=None):
        rows = self.sessions.setdefault(session_id, [])
        mid = f"m_{len(self.appended) + 1}"
        rows.append({"id": mid, "role": role, "content": content})
        self.appended.append((session_id, role, content, asked_by, reply_to))
        return mid

    def get_messages(self, session_id):
        return list(self.sessions.get(session_id, []))

    def message_count(self, session_id=None, agent_id=None):
        return sum(len(v) for v in self.sessions.values())

    def get_session(self, session_id):
        return {}


@pytest.fixture
def env(monkeypatch, tmp_path, CH):
    """隔离票库 + 假名册/会话/wake；返回可观察的上下文。"""
    monkeypatch.setenv("PLOBI_TICKET_DB", str(tmp_path / "tickets.db"))
    monkeypatch.setenv("PLOBI_AGENT_ID", "l1")
    db = _FakeSessionDB()
    db.create_session("chan_a", source="agent_message", agent_id="agenda")
    monkeypatch.setattr(CH, "_session_db", lambda: db)
    monkeypatch.setattr(CH, "_roster", lambda: _Registry(["l1", "agenda"]))
    wake_calls: list[dict] = []

    def fake_wake(entry, session_id, *, sender, ticket_id=""):
        wake_calls.append({
            "entry": entry.name, "session_id": session_id,
            "sender": sender, "ticket_id": ticket_id,
        })
        return True, "ok"

    monkeypatch.setattr(CH.WK, "wake_agent", fake_wake)
    monkeypatch.setattr(CH, "_trace", lambda record: None)
    return {"db": db, "wake": wake_calls, "tmp": tmp_path}


def _ledger():
    from plobi.delegation.tickets import TicketLedger

    return TicketLedger()


def _say(CH, text="把下午的会排好", **extra):
    args = {"to": "agenda", "parts": [{"text": text}], **extra}
    return json.loads(CH.handle_say(args))


def test_say_opens_router_ticket(CH, env):
    """每一跳自动开 ACTIVE 票：架构保证分工，不靠模型声明「我在派工」。"""
    out = _say(CH)
    assert out["ok"] is True and out["woke"] is True
    assert out["ticket_id"].startswith("tkt_")
    assert out["thread_id"].startswith("thr_")
    assert out["ticket_status"] == "active"

    ticket = _ledger().get(out["ticket_id"])
    assert ticket is not None and ticket.leg == "router"
    assert ticket.parent_agent == "l1"
    assert ticket.allows("say") and ticket.allows("ask") and ticket.allows("delegate")
    assert ticket.refs["recipient"] == "agenda"
    assert ticket.refs["mode"] == "say"
    assert ticket.refs["message_id"] == out["message_id"]
    assert ticket.refs["session_id"] == out["session_id"]
    # 票号进了唤醒提示词，被唤醒的那一轮才有得汇报。
    assert env["wake"][0]["ticket_id"] == out["ticket_id"]


def test_reply_to_inherits_thread(CH, env):
    """reply_to 从「引用单条消息」长成 Thread 续接。"""
    first = _say(CH, "先排上午")
    second = _say(CH, "再把下午也排上", reply_to=first["message_id"])
    assert second["thread_id"] == first["thread_id"]
    assert second["ticket_id"] != first["ticket_id"]
    ticket = _ledger().get(second["ticket_id"])
    assert ticket.refs["inherited_from"] == first["ticket_id"]
    assert ticket.refs["reply_to"] == first["message_id"]


def test_revoked_thread_does_not_wake(CH, env):
    """撤销在**执行层**生效：话照送，但不替它起一轮。"""
    first = _say(CH)
    assert _ledger().revoke(first["ticket_id"], reason="人喊停") is True
    env["wake"].clear()

    second = _say(CH, "继续", reply_to=first["message_id"])
    assert second["ok"] is True                      # 投递成功不受影响
    assert second["woke"] is False
    assert "撤销" in second["wakeNote"]
    assert second["ticket_status"] == "revoked"
    assert env["wake"] == []                         # 一次都没起
    assert len(env["db"].appended) == 2              # 两条话都落进那一屏


def test_ticket_report_six_states(CH, env):
    """六态是结构化字段；终态推进票的生命周期，非终态不改。"""
    out = _say(CH)
    tid = out["ticket_id"]
    ledger = _ledger()

    assert json.loads(CH.handle_ticket_report(
        {"ticket_id": tid, "state": "progress", "note": "读到消息了"}
    ))["ticket_status"] == "active"
    assert json.loads(CH.handle_ticket_report(
        {"ticket_id": tid, "state": "waiting", "note": "等用户确认时间"}
    ))["ok"] is True
    assert ledger.latest_report(tid)["state"] == "waiting"
    assert len(ledger.list_reports(tid)) == 2

    done = json.loads(CH.handle_ticket_report(
        {"ticket_id": tid, "state": "ready", "note": "排好了"}
    ))
    assert done["ok"] is True and done["ticket_status"] == "done"
    assert ledger.latest_report(tid)["reporter"] == "l1"
    assert ledger.get(tid).status == "done"

    bad = json.loads(CH.handle_ticket_report({"ticket_id": tid, "state": "maybe"}))
    assert bad["ok"] is False and len(bad["states"]) == 6
    assert json.loads(CH.handle_ticket_report(
        {"ticket_id": "tkt_missing", "state": "ready"}
    ))["ok"] is False


def test_two_mouths_stay_flat(CH):
    """裁定 183 不动：消息面只有 say/ask，parts 里没有 state/intent。"""
    for schema in (CH.SAY_SCHEMA, CH.ASK_SCHEMA):
        props = schema["parameters"]["properties"]
        assert set(props) <= {"to", "parts", "timeout", "reply_to"}
        assert "state" not in props and "intent" not in props
        assert schema["parameters"]["required"] == ["to", "parts"]
    assert CH.TICKET_REPORT_SCHEMA["parameters"]["properties"]["state"]["enum"] == [
        "progress", "waiting", "ready", "blocked", "failed", "dropped",
    ]
    # ticket_report 是旁路：它不注册在 _CHANNEL_TOOLS 那两枚嘴里。
    from importlib import import_module

    plugin = import_module(PKG_NAME)
    assert {row[0] for row in plugin._CHANNEL_TOOLS} == {"say", "ask"}


def test_ask_opens_and_closes_ticket(CH, env, monkeypatch):
    """ask 也是一跳：开票、等回答、自动落 ready 与终态。"""
    monkeypatch.setattr(
        CH.AA, "handle_agent_ask",
        lambda args, **kw: json.dumps({"ok": True, "answer": "下午三点可以"}),
    )
    out = json.loads(CH.handle_ask({"to": "agenda", "parts": [{"text": "几点合适？"}]}))
    assert out["ok"] is True and out["answer"] == "下午三点可以"
    assert out["ticket_id"].startswith("tkt_") and out["ticket_status"] == "done"
    ledger = _ledger()
    assert ledger.get(out["ticket_id"]).leg == "router"
    assert ledger.latest_report(out["ticket_id"])["state"] == "ready"
    assert ledger.latest_report(out["ticket_id"])["reporter"] == "agenda"


def test_ledger_failure_does_not_block_say(CH, env, monkeypatch):
    """授权层是旁路：它坏了，话照样说、照样叫醒。"""
    from plobi.delegation import tickets as T

    def boom(*args, **kwargs):
        raise RuntimeError("db offline")

    monkeypatch.setattr(T.TicketLedger, "record_router", boom)
    out = _say(CH)
    assert out["ok"] is True and out["woke"] is True
    assert "ticket_id" not in out


def test_wake_prompt_carries_ticket_id(CH):
    """唤醒提示词带票号，不带票号时不提 ticket_report。"""
    assert "tkt_abc" in CH.WK.wake_prompt("l1", "tkt_abc")
    assert "ticket_report" in CH.WK.wake_prompt("l1", "tkt_abc")
    assert "ticket_report" not in CH.WK.wake_prompt("l1")
