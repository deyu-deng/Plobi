"""裁定 225：到点主动汇报的判据，以及指挥那条受控 thread 汇总读。

这一档钉的是四件事：
1. **出厂一律先问**：判据文件不在、写坏、或没开总开关 ⇒ 件件都喊；
2. **门槛会移动但不能自抬**：开了开关 + 同类连续采纳 8 次才不问，驳一次立刻退回；
3. **不串账、不越权**：记忆写入那本账攒满不影响汇报；汇总读只对指挥、且只对自己
   发起的 thread 开放，拒绝时连 ``goal`` 都不漏；
4. **读口边界没破**：加了判据之后仍然只读、不发提示词、不改票。
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PLUGIN_DIR = REPO / "plugins" / "plobi-core"


@pytest.fixture(scope="module")
def CH():
    """按包方式加载（channel.py 有相对导入 ``from . import wake``）。"""
    pkg_name = "plobi_test_report_escalation_pkg"
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


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """票库、策略、信任账全落 tmp：这一档不写用户家目录。"""
    monkeypatch.setenv("PLOBI_TICKET_DB", str(tmp_path / "tickets.db"))
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PLOBI_HOME", str(home))
    yield home


@pytest.fixture
def gate():
    return importlib.import_module("plobi.delegation.report_gate")


@pytest.fixture
def ledger():
    from plobi.delegation.tickets import TicketLedger

    return TicketLedger()


def _as(CH, monkeypatch, name: str) -> None:
    monkeypatch.setattr(CH, "current_agent_name", lambda session_id="": name)


def _view(
    *,
    state: str = "blocked",
    note: str = "缺一张图",
    leg: str = "router",
    domain: str = "",
    goal: str = "改配色",
    with_report: bool = True,
) -> dict:
    return {
        "ticket_id": "tkt_abcdef1234",
        "leg": leg,
        "goal": goal,
        "domain": domain,
        "last_report": {"state": state, "note": note} if with_report else None,
    }


def _ticket(
    ledger, *, sender="conductor", leg="router", goal="改配色",
    thread="thr_a", domain="", status="active",
):
    refs = {"thread_id": thread, "recipient": "nymo"}
    if domain:
        refs["domain"] = domain
    return ledger.open_ticket(
        parent_agent=sender, leg=leg, goal=goal, refs=refs, status=status,
    )


def _snapshot(db_path: Path) -> tuple:
    conn = sqlite3.connect(str(db_path))
    try:
        tickets = conn.execute(
            "SELECT id, parent_agent, leg, goal, status, created_at, updated_at, revoke_reason "
            "FROM tickets ORDER BY id"
        ).fetchall()
        reports = conn.execute(
            "SELECT ticket_id, state, note, reporter, created_at FROM ticket_reports ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    return tickets, reports


# ── 判据层：出厂关 / 六态 / 门槛 ──────────────────────────────────────────


def test_factory_default_is_closed(gate, _isolate):
    assert gate.load_policy().auto_approve is False
    (_isolate / gate.POLICY_FILENAME).write_text("{broken", encoding="utf-8")
    assert gate.load_policy().auto_approve is False, "写坏了也要按关处理"
    (_isolate / gate.POLICY_FILENAME).write_text(
        json.dumps({"auto_approve": "true"}), encoding="utf-8"
    )
    assert gate.load_policy().auto_approve is False, "字符串不算开，只有用户写死 true 才算"


def test_every_non_progress_state_escalates_but_progress_does_not(gate):
    for state in ("waiting", "ready", "blocked", "failed", "dropped"):
        assert gate.should_escalate(_view(state=state)) is True, state
    assert gate.should_escalate(_view(state="progress")) is False
    assert gate.should_escalate(_view(with_report=False)) is False


def test_category_is_structural_not_guessed(gate):
    assert gate.decision_category(_view(leg="expert", domain="browser")) == "expert:browser"
    assert gate.decision_category(_view(domain="")) == "router:general"


def test_one_line_is_short_has_one_question_and_no_ticket_id(gate):
    line = gate.render_one(_view(state="blocked"))
    assert len(line) <= gate.MAX_ONE_LINE
    assert line.count("？") == 1, "一次只问一个具体问题"
    assert "tkt_" not in line, "票号不念给用户"
    assert "改配色" in line


def test_never_auto_ignores_full_trust(gate, _isolate):
    """外部事件票与日记/画像那一族，攒满多少次都不许系统替人放行。"""
    from plobi.mind.proposal import TrustLedger

    book = TrustLedger(path=gate.ledger_path())
    for _ in range(gate.AUTO_THRESHOLD):
        book.record("adopted", "router:diary", gate.TRUST_SOURCE)
    policy = gate.ReportPolicy(auto_approve=True)
    assert gate.should_escalate(
        _view(state="ready", domain="diary"), policy=policy, ledger=book
    ) is True
    assert gate.should_escalate(
        _view(state="ready", leg="trigger"), policy=policy, ledger=book
    ) is True


def test_switch_off_asks_even_when_report_ledger_is_full(gate, _isolate):
    """「开总开关」是用户的动作：账本攒满但开关没开，照样件件都问。"""
    from plobi.mind.proposal import TrustLedger

    book = TrustLedger(path=gate.ledger_path())
    for _ in range(gate.AUTO_THRESHOLD):
        book.record("adopted", "router:general", gate.TRUST_SOURCE)
    assert gate.should_escalate(
        _view(), policy=gate.ReportPolicy(auto_approve=False), ledger=book
    ) is True


def test_full_trust_skips_then_one_reject_returns_to_asking(gate, _isolate):
    from plobi.mind.proposal import TrustLedger

    book = TrustLedger(path=gate.ledger_path())
    category = gate.decision_category(_view())
    for _ in range(gate.AUTO_THRESHOLD):
        book.record("adopted", category, gate.TRUST_SOURCE)
    policy = gate.ReportPolicy(auto_approve=True)
    assert gate.should_escalate(_view(), policy=policy, ledger=book) is False

    book.record("rejected", category, gate.TRUST_SOURCE, "用户驳回")
    assert gate.should_escalate(_view(), policy=policy, ledger=book) is True


def test_two_ledgers_do_not_bleed(gate, _isolate):
    """批过一次笔记写入，不许顺带把汇报也放行。"""
    from plobi.mind.proposal import TrustLedger

    memory_book = TrustLedger(path=_isolate / "l1-trust.json")
    category = gate.decision_category(_view())
    for _ in range(gate.AUTO_THRESHOLD):
        memory_book.record("adopted", category, gate.TRUST_SOURCE)
    assert gate.should_escalate(
        _view(), policy=gate.ReportPolicy(auto_approve=True)
    ) is True, "汇报账本是空的，不该读到记忆账本的信任"


# ── 读口回包：判据有真实消费者 ────────────────────────────────────────────


def test_tickets_reply_carries_next_escalation(CH, ledger, monkeypatch):
    t = _ticket(ledger)
    ledger.report_state(t.id, "blocked", note="缺一张图", reporter="nymo")
    _as(CH, monkeypatch, "conductor")

    out = json.loads(CH.handle_tickets({}))
    assert out["ok"] is True
    assert out["next_escalation"]["ticket_id"] == t.id
    assert out["next_escalation"]["state"] == "blocked"
    assert out["escalations"][0]["one_line"] == out["next_escalation"]["one_line"]


def test_progress_does_not_open_mouth(CH, ledger, monkeypatch):
    t = _ticket(ledger)
    ledger.report_state(t.id, "progress", note="正在推", reporter="nymo")
    _as(CH, monkeypatch, "conductor")

    out = json.loads(CH.handle_tickets({}))
    assert out["escalations"] == []
    assert out["next_escalation"] is None


def test_only_one_question_per_round(CH, ledger, monkeypatch):
    for i in range(3):
        t = _ticket(ledger, goal=f"第{i}件")
        ledger.report_state(t.id, "ready", reporter="nymo")
    _as(CH, monkeypatch, "conductor")

    out = json.loads(CH.handle_tickets({}))
    assert len(out["escalations"]) == 3, "清单全给，看得见还有几件"
    assert out["next_escalation"] == out["escalations"][0], "这一轮只念第一件"


def test_single_ticket_branch_also_carries_verdict(CH, ledger, monkeypatch):
    t = _ticket(ledger)
    ledger.report_state(t.id, "failed", note="工具不通", reporter="nymo")
    _as(CH, monkeypatch, "conductor")
    out = json.loads(CH.handle_tickets({"ticket_id": t.id}))
    assert out["next_escalation"]["state"] == "failed"


def test_read_boundaries_survive_the_new_field(CH, ledger, monkeypatch, tmp_path):
    """裁定 222 三条边界一条不许松：只读、不发提示词、不改状态。"""
    t = _ticket(ledger)
    ledger.report_state(t.id, "blocked", reporter="nymo")
    before = _snapshot(tmp_path / "tickets.db")

    calls: list[str] = []
    monkeypatch.setattr(CH, "_deliver", lambda *a, **k: calls.append("_deliver") or ("", ""))
    monkeypatch.setattr(
        CH, "_deliver_to_user", lambda *a, **k: calls.append("_deliver_to_user") or ""
    )
    monkeypatch.setattr(
        CH.WK, "wake_agent", lambda *a, **k: calls.append("wake_agent") or (False, "")
    )
    _as(CH, monkeypatch, "conductor")
    CH.handle_tickets({})
    CH.handle_tickets({"ticket_id": t.id})

    assert calls == []
    assert _snapshot(tmp_path / "tickets.db") == before


# ── 受控汇总读：只对指挥、只对自己发起的 thread ──────────────────────────


def test_conductor_identity_gate_reads_the_roster(CH, ledger, monkeypatch):
    """这一例**不替换** ``_is_conductor``：钉住源码里那道真身份闸。

    别的用例为了隔离把身份闸换成替身，若只留那些，把闸改成恒 True 也不会红。
    """
    import plobi.agents.registry as reg

    class FakeEntry:
        def __init__(self, role: str):
            self.role = role

    class FakeRegistry:
        def get(self, name):
            return {
                "conductor": FakeEntry(reg.CONDUCTOR_ROLE),
                "nymo": FakeEntry("principal"),
            }.get(name)

    monkeypatch.setattr(reg, "load_registry", lambda: FakeRegistry())

    # 第一条 thread 故意让「根票归属」这把闸**也放行**：根票就是 nymo 自己发起的。
    # 于是唯一挡住他的只剩身份闸；恒开身份闸时这一例必红。
    _ticket(ledger, sender="nymo", thread="thr_a", goal="nymo 的根票")
    _ticket(ledger, sender="conductor", thread="thr_a", goal="别人的下游")
    _as(CH, monkeypatch, "nymo")
    denied = json.loads(CH.handle_tickets({"thread_id": "thr_a"}))
    assert denied["ok"] is False, "首席不该拿到指挥那条汇总读"
    assert "别人的下游" not in json.dumps(denied, ensure_ascii=False)

    # 第二条 thread 两道闸都该放行：指挥发起根票，首席接了下游。
    _ticket(ledger, sender="conductor", thread="thr_b", goal="指挥的根票")
    _ticket(ledger, sender="nymo", thread="thr_b", goal="首席的下游")
    _as(CH, monkeypatch, "conductor")
    ok = json.loads(CH.handle_tickets({"thread_id": "thr_b"}))
    assert ok["ok"] is True
    assert ok["count"] == 2


def test_thread_summary_sees_downstream_only_for_conductor(CH, ledger, monkeypatch):
    root = _ticket(ledger, goal="根票")
    downstream = _ticket(ledger, sender="nymo", goal="下游那件")
    ledger.report_state(downstream.id, "blocked", note="要人定", reporter="nymo")
    monkeypatch.setattr(CH, "_is_conductor", lambda name: name == "conductor")

    _as(CH, monkeypatch, "nymo")
    denied = json.loads(CH.handle_tickets({"thread_id": "thr_a"}))
    assert denied["ok"] is False
    assert "下游那件" not in json.dumps(denied, ensure_ascii=False), "拒绝时不许漏 goal"

    _as(CH, monkeypatch, "conductor")
    out = json.loads(CH.handle_tickets({"thread_id": "thr_a"}))
    assert out["ok"] is True
    assert out["count"] == 2
    assert any(v["goal"] == "下游那件" for v in out["tickets"])
    assert out["next_escalation"]["ticket_id"] == downstream.id
    assert root.id in {v["ticket_id"] for v in out["tickets"]}


def test_thread_summary_rejects_a_thread_the_conductor_did_not_start(
    CH, ledger, monkeypatch
):
    _ticket(ledger, sender="someone-else", goal="别人的根票")
    _ticket(ledger, goal="混进来的下游")
    monkeypatch.setattr(CH, "_is_conductor", lambda name: True)

    _as(CH, monkeypatch, "conductor")
    out = json.loads(CH.handle_tickets({"thread_id": "thr_a"}))
    assert out["ok"] is False
    assert "别人的根票" not in json.dumps(out, ensure_ascii=False)


def test_ticket_id_and_thread_id_are_mutually_exclusive(CH, ledger, monkeypatch):
    t = _ticket(ledger)
    _as(CH, monkeypatch, "conductor")
    out = json.loads(CH.handle_tickets({"ticket_id": t.id, "thread_id": "thr_a"}))
    assert out["ok"] is False


def test_normal_list_still_hides_other_peoples_tickets(CH, ledger, monkeypatch):
    """汇总读是独立路径，``_may_read_ticket`` 那两种可见性一个字没放宽。"""
    _ticket(ledger, goal="我的")
    _ticket(ledger, sender="nymo", goal="别人的")
    monkeypatch.setattr(CH, "_is_conductor", lambda name: True)
    _as(CH, monkeypatch, "conductor")

    out = json.loads(CH.handle_tickets({}))
    assert [v["goal"] for v in out["tickets"]] == ["我的"]
