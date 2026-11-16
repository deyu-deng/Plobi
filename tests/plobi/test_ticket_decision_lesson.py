"""裁定 226：三态决定账 ＋ 会移动的语义授权。

钉住四件事：
1. 人写的 ``ticket_decisions`` 与 Agent 写的 ``ticket_reports`` 分开，表态不改票；
2. lesson 的生命周期：proposed 不生效、approve 才召回、revoke 立刻退回；
3. 8 次计数只作**提议门槛**，不是放行门槛（与裁定 199 的关系改写在这里）；
4. 评论挂进 ``tickets`` 回包等指挥起草，草稿关联后不再重复出现。
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PLUGIN_DIR = REPO / "plugins" / "plobi-core"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("PLOBI_TICKET_DB", str(tmp_path / "tickets.db"))
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PLOBI_HOME", str(home))
    yield tmp_path


@pytest.fixture(scope="module")
def CH():
    pkg_name = "plobi_test_decision_lesson_pkg"
    if pkg_name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            pkg_name, PLUGIN_DIR / "__init__.py", submodule_search_locations=[str(PLUGIN_DIR)]
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        module.__package__ = pkg_name
        module.__path__ = [str(PLUGIN_DIR)]
        sys.modules[pkg_name] = module
        spec.loader.exec_module(module)
    return importlib.import_module(f"{pkg_name}.channel")


@pytest.fixture
def ticket():
    from plobi.delegation.tickets import TicketLedger

    return TicketLedger().open_ticket(
        parent_agent="conductor", leg="router", goal="改配色", refs={"thread_id": "thr_x"}
    )


# ── 人决定账 ──────────────────────────────────────────────────────────────


def test_three_kinds_are_recorded_and_ticket_is_untouched(ticket):
    from plobi.delegation.decision_ledger import KINDS, DecisionLedger
    from plobi.delegation.tickets import TicketLedger

    ledger = DecisionLedger()
    before = TicketLedger().get(ticket.id)
    for kind in KINDS:
        ledger.record(ticket.id, kind, comment="以后这类你定")
    rows = ledger.list_for_ticket(ticket.id)

    assert {r.kind for r in rows} == set(KINDS)
    assert ledger.counts(ticket.id) == {"accept": 1, "reject": 1, "comment": 1}
    assert all("以后这类你定" in r.comment for r in rows if r.kind == "comment")
    after = TicketLedger().get(ticket.id)
    assert (before.status, before.updated_at) == (after.status, after.updated_at), "表态不许改票"


def test_decision_rejects_unknown_kind_and_empty_ticket(ticket):
    from plobi.delegation.decision_ledger import DecisionLedger

    ledger = DecisionLedger()
    with pytest.raises(ValueError):
        ledger.record(ticket.id, "maybe")
    with pytest.raises(ValueError):
        ledger.record("", "accept")


# ── lesson 生命周期 ───────────────────────────────────────────────────────


def test_lesson_lifecycle_proposed_approved_revoked():
    from plobi.delegation.lesson_ledger import LessonLedger

    book = LessonLedger()
    lesson = book.propose(
        scope="router:general", rule="改配色这类你自己定", decision_ids=["dec_1"]
    )
    assert lesson.status == "proposed"
    assert book.approved() == []
    assert book.recall("改配色") == [], "草稿不许被召回"

    assert book.approve(lesson.id).status == "approved"
    assert len(book.recall("改配色")) == 1
    assert book.recall("改配色")[0][0] > 0

    assert book.revoke(lesson.id).status == "revoked"
    assert book.recall("改配色") == [], "你一句话撤回，下一次召回就看不见"


def test_question_without_rule_stays_a_question():
    from plobi.delegation.lesson_ledger import LessonLedger

    book = LessonLedger()
    lesson = book.propose(scope="router:general", question="这类我能自己定吗？")
    book.approve(lesson.id)
    assert lesson.rule == ""
    assert not book.recall("自己定"), "拿不准时留的是问题，不是自造的规则"


def test_rejected_lesson_never_recalls():
    from plobi.delegation.lesson_ledger import LessonLedger

    book = LessonLedger()
    lesson = book.propose(scope="router:general", rule="这类你自己定")
    book.reject(lesson.id)
    assert book.recall("自己定") == []


# ── 提议门槛 ≠ 放行门槛 ───────────────────────────────────────────────────


def test_count_only_opens_the_door_to_drafting(_isolate):
    gate = importlib.import_module("plobi.delegation.report_gate")
    from plobi.delegation.lesson_ledger import LessonLedger
    from plobi.mind.proposal import TrustLedger

    book = TrustLedger(path=_isolate / "l1-trust.json")
    category = "router:general"
    for _ in range(gate.AUTO_THRESHOLD - 1):
        book.record("adopted", category, gate.TRUST_SOURCE)
    assert gate.can_propose(category, ledger=book) is False
    book.record("adopted", category, gate.TRUST_SOURCE)
    assert gate.can_propose(category, ledger=book) is True

    view = {
        "ticket_id": "tkt_x", "leg": "router", "goal": "改配色", "domain": "",
        "last_report": {"state": "blocked", "note": "缺一张图"},
    }
    assert gate.should_escalate(
        view, policy=gate.ReportPolicy(auto_approve=True), lesson_ledger=LessonLedger()
    ) is True, "攒够 8 次只是可以起草，不是可以不问"


# ── 评论回到读口，等指挥起草 ──────────────────────────────────────────────


def test_comment_reaches_the_conductor_via_tickets(CH, ticket, monkeypatch):
    from plobi.delegation.decision_ledger import DecisionLedger

    decision = DecisionLedger().record(ticket.id, "comment", comment="以后配色这类你定")
    monkeypatch.setattr(CH, "current_agent_name", lambda session_id="": "conductor")

    out = json.loads(CH.handle_tickets({}))
    assert out["ok"] is True
    assert [f["decision_id"] for f in out["pending_feedback"]] == [decision.id]
    assert out["pending_feedback"][0]["comment"] == "以后配色这类你定"


def test_drafted_comment_disappears_from_pending(CH, ticket, monkeypatch):
    from plobi.delegation.decision_ledger import DecisionLedger
    from plobi.delegation.lesson_ledger import LessonLedger

    decision = DecisionLedger().record(ticket.id, "comment", comment="以后这类你定")
    LessonLedger().propose(
        scope="router:general", rule="配色这类你自己定", decision_ids=[decision.id]
    )
    monkeypatch.setattr(CH, "current_agent_name", lambda session_id="": "conductor")

    out = json.loads(CH.handle_tickets({}))
    assert out["pending_feedback"] == []


def test_lesson_write_handler_only_creates_a_draft(CH):
    from plobi.delegation.lesson_ledger import LessonLedger

    out = json.loads(CH.handle_lesson_write({
        "scope": "router:general", "rule": "配色这类我自己定", "decision_id": "dec_1"
    }))
    assert out["ok"] is True
    assert out["status"] == "proposed"
    assert LessonLedger().approved() == [], "工具本身不许让草稿生效"
