"""专家腿派发前预授权票 ＋ 桌面放行/撤销入口（WP-L3-TICKET-MANDATE 收口刀）。

裁定 215：L2 主动调 ``delegate_task`` 视为已放行 ⇒ 票直接 active；
外部不可信事件走 webhook 的 observe 门，人点放行才送进 L1 那一屏。
"""
from __future__ import annotations

import asyncio
import importlib
import json
import sqlite3

import pytest

from plobi.delegation import expert_roles, experts
from plobi.delegation.tickets import (
    ACTIVE, DONE, OBSERVED, REVOKED, TicketLedger,
)
from tools import delegate_tool


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = tmp_path / "delegation_tickets.db"
    monkeypatch.setenv("PLOBI_TICKET_DB", str(db))
    monkeypatch.setenv("PLOBI_EXPERTS_DIR", str(tmp_path / "experts"))
    return tmp_path, db


def test_expert_ticket_opens_active_with_fixed_scope(env):
    ids = delegate_tool._open_expert_tickets(
        [{"goal": "整理会议纪要", "expert": "file", "role": "orchestrator"}],
        object(), "orchestrator",
    )
    assert len(ids) == 1 and ids[0].startswith("tkt_")
    ticket = TicketLedger().get(ids[0])
    assert ticket.status == ACTIVE
    assert ticket.leg == "expert"
    assert ticket.allows("delegate")
    assert not ticket.allows("terminal")
    # 授权范围连内存里那份也改不动：后续消息改不了这一跳的权限。
    with pytest.raises(TypeError):
        ticket.scope["allowed_capabilities"] = ["terminal"]
    assert TicketLedger().check_scope(ids[0], "delegate") is True


def test_ticket_ledger_failure_never_blocks_delegation(env, monkeypatch):
    def _boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(TicketLedger, "open_ticket", _boom)
    assert delegate_tool._open_expert_tickets(
        [{"goal": "照常干活"}], object(), ""
    ) == []


def test_record_role_carries_ticket_id(env):
    expert_roles.record_role("sess-1", "file", "整理纪要", model="m", ticket_id="tkt_abc")
    payload = json.loads((env[0] / "experts" / "sess-1.json").read_text(encoding="utf-8"))
    assert payload["ticket_id"] == "tkt_abc"
    assert payload["role"] == "file"


def test_expert_rows_carry_ticket_status(env, monkeypatch):
    db = env[0] / "state.db"
    con = sqlite3.connect(str(db))
    con.execute(
        "create table sessions (id text, agent_id text, source text, "
        "parent_session_id text, started_at real, ended_at real, "
        "message_count integer, tool_call_count integer, model text)"
    )
    con.execute("insert into sessions values ('p1','agenda','cli','',1,1,1,1,'')")
    con.execute("insert into sessions values ('child1','','subagent','p1',2,2,3,4,'m')")
    con.commit()
    con.close()
    monkeypatch.setattr(experts, "state_db_path", lambda: db)

    ticket = TicketLedger().open_ticket(
        parent_agent="agenda", leg="expert", goal="整理纪要", status=ACTIVE
    )
    expert_roles.record_role("child1", "file", "整理纪要", ticket_id=ticket.id)

    rows = experts.experts_for_agent("agenda")["experts"]
    assert len(rows) == 1
    assert rows[0]["ticket_id"] == ticket.id
    assert rows[0]["ticket_status"] == ACTIVE


def test_ticket_routes_approve_delivers_before_activation(env, monkeypatch):
    import plobi_user_inbox

    console_router = importlib.import_module("plobi.console.router")
    sent: list[tuple[str, str, str]] = []

    def _fake_append(body, *, asked_by, label, **_kwargs):
        sent.append((body, asked_by, label))
        return "sess-l1", "msg-1", None

    monkeypatch.setattr(plobi_user_inbox, "append_user_message", _fake_append)

    tid = TicketLedger().record_trigger(
        route="github", event_type="pull_request",
        prompt="顺手把测试补一下", reason="observe by default",
    )
    listed = asyncio.run(console_router.dispatch_tickets_route())
    assert [t["id"] for t in listed["data"]["tickets"]] == [tid]
    assert listed["data"]["tickets"][0]["refs"]["prompt"] == "顺手把测试补一下"
    assert asyncio.run(console_router.dispatch_tickets_route(leg="expert"))["data"]["tickets"] == []

    approved = asyncio.run(console_router.dispatch_ticket_activate_route(tid))
    assert approved["data"]["ticket"]["status"] == ACTIVE
    assert sent == [("顺手把测试补一下", f"ticket:{tid}", "github")]
    detail = asyncio.run(console_router.dispatch_ticket_route(tid))
    assert detail["data"]["ticket"]["scope"]["allowed_capabilities"] == []


def test_delivery_failure_keeps_ticket_observed(env, monkeypatch):
    import plobi_user_inbox

    console_router = importlib.import_module("plobi.console.router")
    monkeypatch.setattr(
        plobi_user_inbox, "append_user_message",
        lambda *a, **k: (None, None, "找不到你那一屏"),
    )
    tid = TicketLedger().record_trigger(
        route="ci", event_type="build_failed", prompt="构建挂了",
    )
    response = asyncio.run(console_router.dispatch_ticket_activate_route(tid))
    assert response.status_code == 502
    assert TicketLedger().get(tid).status == OBSERVED


def test_revoke_closes_pending_ticket(env):
    console_router = importlib.import_module("plobi.console.router")

    class _Request:
        async def json(self):
            return {"reason": "ignored from desktop"}

    tid = TicketLedger().record_trigger(
        route="chat", event_type="group_message", prompt="明天带伞",
    )
    revoked = asyncio.run(console_router.dispatch_ticket_revoke_route(tid, _Request()))
    assert revoked["data"]["ticket"]["status"] == REVOKED
    assert revoked["data"]["ticket"]["revoke_reason"] == "ignored from desktop"
    assert asyncio.run(console_router.dispatch_ticket_revoke_route(tid, _Request())).status_code == 409


def test_terminal_ticket_never_reactivated(env):
    """终态票不会被 open_ticket 之外的路径悄悄复活。"""
    tid = TicketLedger().open_ticket(
        parent_agent="agenda", leg="expert", goal="done", status=ACTIVE
    ).id
    assert TicketLedger().finish(tid, ok=True) is True
    assert TicketLedger().get(tid).status == DONE
    assert TicketLedger().activate(tid) is False
    assert TicketLedger().get(tid).status == DONE
