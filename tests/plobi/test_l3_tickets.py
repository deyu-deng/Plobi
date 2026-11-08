"""WP-L3-TICKET-MANDATE：派工授权层的可查 / 可撤 / 可限权，以及外包流水的挂票。"""

from __future__ import annotations

import dataclasses

import pytest

from plobi.delegation.offload import OffloadResult, RoundLedger
from plobi.delegation.tickets import (
    ACTIVE,
    DONE,
    FAILED,
    OBSERVED,
    POLICY_VERSION,
    REVOKED,
    TicketLedger,
)


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "delegation_tickets.db"
    monkeypatch.setenv("PLOBI_TICKET_DB", str(path))
    return path


def _new(ledger, **kwargs):
    kwargs.setdefault("parent_agent", "agenda")
    kwargs.setdefault("leg", "expert")
    kwargs.setdefault("goal", "整理本周资料")
    return ledger.open_ticket(**kwargs)


def test_lifecycle_observed_active_revoked(db_path):
    ledger = TicketLedger()
    ticket = _new(ledger, scope={"allowed_capabilities": ["file"]})

    assert ticket.status == OBSERVED
    assert ticket.policy_version == POLICY_VERSION
    assert ledger.is_executable(ticket.id) is False

    assert ledger.activate(ticket.id) is True
    assert ledger.is_executable(ticket.id) is True
    assert ledger.activate(ticket.id) is False

    assert ledger.revoke(ticket.id, reason="人喊停") is True
    reloaded = TicketLedger().get(ticket.id)  # 换实例 = 重启后仍可读
    assert reloaded is not None
    assert reloaded.status == REVOKED
    assert reloaded.revoke_reason == "人喊停"
    assert ledger.is_executable(ticket.id) is False


def test_scope_is_frozen_and_limits_capabilities(db_path):
    ledger = TicketLedger()
    ticket = _new(ledger, scope={"allowed_capabilities": ["file", "browser"]})

    assert ledger.check_scope(ticket.id, "file") is False  # observed 不可执行
    ledger.activate(ticket.id)
    assert ledger.check_scope(ticket.id, "file") is True
    assert ledger.check_scope(ticket.id, "terminal") is False

    with pytest.raises(TypeError):
        ticket.scope["allowed_capabilities"] = ["terminal"]  # 后续消息改不了授权
    assert ledger.check_scope(ticket.id, "terminal") is False
    assert ledger.get(ticket.id).scope["allowed_capabilities"] == ["file", "browser"]


def test_finish_only_from_active_and_lists(db_path):
    ledger = TicketLedger()
    first = _new(ledger)
    second = _new(ledger, parent_agent="planner")

    assert ledger.finish(first.id) is False  # observed 不能直接完结
    assert ledger.activate(first.id) is True
    assert ledger.finish(first.id, refs={"session_ref": "sid-9"}) is True
    assert ledger.get(first.id).status == DONE
    assert ledger.get(first.id).refs["session_ref"] == "sid-9"
    assert ledger.activate(second.id) is True
    assert ledger.finish(second.id, ok=False) is True
    assert ledger.get(second.id).status == FAILED

    done = ledger.list_tickets(status=DONE)
    assert [t.id for t in done] == [first.id]
    assert [t.id for t in ledger.list_tickets(parent_agent="planner")] == [second.id]


def test_offload_round_gets_a_ticket(db_path):
    result = OffloadResult(
        agent="marvis",
        cwd="",
        permission_flag="-y",
        job="job-1",
        external_session="sid-1",
        reply_to="agenda",
        answer="做完了，正文很长" * 20,
    )
    ledger = RoundLedger(root=db_path.parent)
    row = ledger.append(result, brief="把这一周的资料归档")

    assert row["ticket_id"].startswith("tkt_")
    assert ledger.rows()[0]["ticket_id"] == row["ticket_id"]
    assert ledger.rows()[0]["brief"] == "把这一周的资料归档"

    ticket = TicketLedger().get(row["ticket_id"])
    assert ticket is not None
    assert ticket.status == DONE
    assert ticket.leg == "offload"
    assert ticket.refs == {"job": "job-1", "external_session": "sid-1", "reply_to": "agenda"}
    assert "做完了" not in ticket.refs.get("answer", "")  # 只存关联键，不复制回包正文
    assert "把这一周的资料归档" not in str(ticket.refs)


def test_offload_failure_ticket_and_explicit_ticket(db_path):
    failed = OffloadResult(agent="kiro", cwd="", permission_flag="", failure="timeout")
    row = RoundLedger(root=db_path.parent).append(failed, brief="试一次")
    assert TicketLedger().get(row["ticket_id"]).status == FAILED

    tickets = TicketLedger()
    preauthorized = _new(tickets, leg="offload")
    assert tickets.activate(preauthorized.id) is True
    explicit = dataclasses.replace(
        failed, ticket_id=preauthorized.id, job="job-2", failure=""
    )
    row2 = RoundLedger(root=db_path.parent).append(explicit, brief="带票的第二次")
    assert row2["ticket_id"] == preauthorized.id
    assert tickets.get(preauthorized.id).status == DONE
    assert tickets.get(preauthorized.id).refs["job"] == "job-2"


def test_explicit_append_argument_is_not_overwritten(db_path):
    tickets = TicketLedger()
    ticket = _new(tickets)
    tickets.activate(ticket.id)
    row = RoundLedger(root=db_path.parent).append(
        OffloadResult(agent="qwen", cwd="", permission_flag=""),
        brief="调用方自己管票",
        ticket_id=ticket.id,
    )
    assert row["ticket_id"] == ticket.id
    assert tickets.get(ticket.id).status == ACTIVE  # 调用方决定何时 finish
