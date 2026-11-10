"""裁定 215 #34：全局记忆按域分持、链式召回；#35 的人优先说话纪律。"""

from __future__ import annotations

import pytest

from plobi.delegation.recall import (
    REPORTING_RULES,
    DomainRecallError,
    recall_chain_for_ticket,
    recall_for_agent,
    reporting_rules_text,
    should_speak,
    within_report_budget,
)
from plobi.delegation.tickets import TicketLedger


class _FakeDB:
    def __init__(self):
        self.calls = []

    def search_messages(self, query, **kwargs):
        self.calls.append((query, kwargs))
        return [{"id": "m1", "snippet": "hit", "agent_id": kwargs.get("agent_id")}]


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("PLOBI_TICKET_DB", str(tmp_path / "delegation_tickets.db"))
    return TicketLedger()


def test_recall_is_always_scoped_to_one_agent():
    db = _FakeDB()
    hits = recall_for_agent(db, "agenda", "明天组会", limit=3)
    query, kwargs = db.calls[0]
    assert kwargs["agent_id"] == "agenda"
    assert kwargs["limit"] == 3
    assert kwargs["sort"] == "newest"
    assert "明天组会" in query
    assert hits[0]["id"] == "m1"


def test_l1_or_unidentified_caller_cannot_search_globally():
    db = _FakeDB()
    with pytest.raises(DomainRecallError):
        recall_for_agent(db, "", "任何事")
    with pytest.raises(DomainRecallError):
        recall_for_agent(db, "   ", "任何事")
    assert db.calls == []


def test_empty_query_does_not_hit_fts():
    db = _FakeDB()
    assert recall_for_agent(db, "agenda", "   ") == []
    assert db.calls == []


def test_chain_recall_uses_ticket_refs_without_leaking_the_domain(ledger):
    ticket = ledger.open_ticket(
        parent_agent="planner",
        leg="offload",
        refs={"job": "job-7", "session_ref": "sid-3"},
    )
    db = _FakeDB()
    hits, chain = recall_chain_for_ticket(db, ticket, "上周结论", limit=2)

    query, kwargs = db.calls[0]
    assert kwargs["agent_id"] == "planner"
    assert "job=job-7" in query and "session_ref=sid-3" in query
    assert any("ticket=" + ticket.id in step for step in chain)
    assert any("agent_id=planner" in step for step in chain)
    assert hits[0]["agent_id"] == "planner"


def test_record_trigger_ticket_is_observable_and_revocable(ledger):
    tid = ledger.record_trigger(
        route="github",
        event_type="pull_request",
        prompt="CI failed on main" * 20,
        delivery_id="del-9",
        reason="observe by default",
    )
    ticket = ledger.get(tid)
    assert ticket.status == "observed"
    assert ticket.leg == "trigger"
    assert ticket.parent_agent == "l1"
    assert len(ticket.refs["prompt_excerpt"]) <= 120
    assert ledger.check_scope(tid, "terminal") is False
    assert ledger.activate(tid) is True
    assert ledger.is_executable(tid) is True
    assert ledger.revoke(tid, reason="不看这条") is True
    assert ledger.is_executable(tid) is False


def test_reporting_rules_are_single_source_and_enforced():
    assert len(REPORTING_RULES) == 3
    text = reporting_rules_text()
    assert text.count("- ") == 3
    assert should_speak(needs_human_action=False, usable_now=False) is False
    assert should_speak(needs_human_action=True, usable_now=False) is True
    assert should_speak(needs_human_action=False, usable_now=True) is True
    assert within_report_budget("已派给日程秘书，明早 8 点给你结果。") is True
    assert within_report_budget("长" * 101) is False
