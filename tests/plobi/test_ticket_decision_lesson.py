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
    """没标来源（默认 comment）仍是草稿——裁定 226 的原行为不许被 227 改坏。"""
    from plobi.delegation.lesson_ledger import LessonLedger

    out = json.loads(CH.handle_lesson_write({
        "scope": "router:general", "rule": "配色这类我自己定", "decision_id": "dec_1"
    }))
    assert out["ok"] is True
    assert out["status"] == "proposed"
    assert out["source"] == "comment"
    assert LessonLedger().approved() == [], "从评论推断的草稿不许借对话入口生效"


# ── 裁定 227：用户亲口授权那句话本身就是点头 ──────────────────────────────


def test_user_utterance_authorization_takes_effect_immediately(CH):
    from plobi.delegation.lesson_ledger import LessonLedger

    words = "以后配色这类你自己定，不用再问我"
    out = json.loads(CH.handle_lesson_write({
        "scope": "router:general", "rule": "配色这类我自己定",
        "source": "conversation", "user_words": words,
    }))
    assert out["ok"] is True
    assert out["status"] == "approved"
    assert out["source"] == "conversation"

    row = LessonLedger().get(out["lesson_id"])
    assert row.status == "approved"
    assert row.user_words == words
    assert row.evidence == words, "原话要落进账本供回核"
    assert row.approved_by == "user_utterance"
    assert row.question == "", "授权明确时不该留问句"


def test_conversation_without_user_words_is_refused(CH):
    """没有用户原话，模型不许凭一句『我觉得他同意』就放权。"""
    from plobi.delegation.lesson_ledger import LessonLedger

    out = json.loads(CH.handle_lesson_write({
        "scope": "router:general", "rule": "配色这类我自己定",
        "source": "conversation",
    }))
    assert out["ok"] is False
    assert "user_words" in out["error"]
    assert LessonLedger().all() == [], "被拒的授权连草稿都不该留"


def test_ledger_refuses_utterance_without_words_even_if_called_directly():
    """账本层自己也有牙：绕过工具直接调 propose 也不许无原话放权（纵深第二层）。"""
    import pytest

    from plobi.delegation.lesson_ledger import LessonLedger

    with pytest.raises(ValueError, match="user_words"):
        LessonLedger().propose(
            scope="router:general", rule="我自己放权", source="conversation"
        )
    assert LessonLedger().all() == []


def test_utterance_authorization_silences_the_next_report(CH):
    """用户说完那句话，下一次同类 blocked 不再生成 next_escalation。"""
    gate = importlib.import_module("plobi.delegation.report_gate")
    from plobi.delegation.lesson_ledger import LessonLedger

    words = "以后改配色这类你自己定"
    LessonLedger().propose(
        scope="router:general", rule="改配色这类自己定",
        source="conversation", user_words=words,
    )
    view = {
        "ticket_id": "tkt_x", "leg": "router", "goal": "改配色", "domain": "",
        "last_report": {"state": "blocked", "note": "缺一张图"},
    }
    assert gate.should_escalate(
        view, policy=gate.ReportPolicy(auto_approve=True), lesson_ledger=LessonLedger()
    ) is False


def test_revoked_utterance_authorization_stops_being_effective():
    """一句话撤回后召回里立刻没有它——撤回是失效，不是再加一条反向 lesson。"""
    from plobi.delegation.lesson_ledger import LessonLedger

    book = LessonLedger()
    row = book.propose(
        scope="router:general", rule="改配色这类自己定",
        source="conversation", user_words="以后改配色你定",
    )
    assert book.recall("改配色")
    book.revoke(row.id)
    assert book.recall("改配色") == []
