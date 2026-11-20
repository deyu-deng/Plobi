"""裁定 232：回合入口的强制汇报上下文。

钉住五件事：
1. 指挥有待汇报件 ⇒ 注入串带上判据算好的结论，且指明用 say 说；
2. 非指挥一个字都不注入（身份闸是唯一开关）；
3. 没有 escalation（纯进度/无回执）⇒ 空串，不占上下文；
4. 身份读不出 / 读账失败 ⇒ 空串，绝不打断回合；
5. 注入前后票库逐位不变（只读），且 hook 对所有 agent 都安全。
"""

from __future__ import annotations

import importlib
import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PLUGIN_DIR = REPO / "plugins" / "plobi-core"
PKG = "plobi_test_forced_report_pkg"


@pytest.fixture(scope="module")
def pkg():
    if PKG not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            PKG, PLUGIN_DIR / "__init__.py", submodule_search_locations=[str(PLUGIN_DIR)]
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        module.__package__ = PKG
        module.__path__ = [str(PLUGIN_DIR)]
        sys.modules[PKG] = module
        spec.loader.exec_module(module)
    return sys.modules[PKG]


@pytest.fixture
def FR(pkg):
    return importlib.import_module(f"{PKG}.forced_report")


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("PLOBI_TICKET_DB", str(tmp_path / "tickets.db"))
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PLOBI_HOME", str(home))
    return home


def _as_conductor(FR, monkeypatch):
    monkeypatch.setattr(FR, "_current_name", lambda sid="": "Plobi")
    monkeypatch.setattr(FR, "_is_conductor_name", lambda name: bool(name == "Plobi"))


def _ledger_path():
    from plobi.delegation.tickets import TicketLedger

    return TicketLedger().path


def _as_other(FR, monkeypatch):
    monkeypatch.setattr(FR, "_current_name", lambda sid="": "nymo")
    monkeypatch.setattr(FR, "_is_conductor_name", lambda name: name == "Plobi")


def _ticket_with_report(*, state="blocked", goal="改配色"):
    from plobi.delegation.tickets import TicketLedger

    ledger = TicketLedger()
    t = ledger.open_ticket(
        parent_agent="Plobi", leg="router", goal=goal,
        refs={"thread_id": "thr_x", "recipient": "nymo"}, status="active",
    )
    ledger.report_state(t.id, state=state, note="缺一张图", reporter="nymo")
    return ledger


def _snapshot(db_path: Path):
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute("SELECT id, status FROM tickets ORDER BY id").fetchall(), \
            conn.execute("SELECT ticket_id, state FROM ticket_reports ORDER BY id").fetchall()
    finally:
        conn.close()


# ── 1. 指挥有待汇报 ⇒ 注入 ────────────────────────────────────────────────


def test_conductor_gets_forced_context(FR, monkeypatch, _isolate):
    _as_conductor(FR, monkeypatch)
    db = _ledger_path()
    _ticket_with_report()
    before = _snapshot(db)

    text = FR.build_forced_context("sess-1")
    assert "本轮待汇报" in text
    assert "say to=用户" in text, "必须指明唯一出口，不许模型另找嘴"
    assert "不必再读 tickets" in text
    assert _snapshot(db) == before, "回合注入不许改票/改回执"


def test_hook_returns_context_dict(FR, monkeypatch, _isolate):
    _as_conductor(FR, monkeypatch)
    _ticket_with_report()
    out = FR.on_pre_llm_call(session_id="sess-1", user_message="你好", model="x")
    assert set(out) == {"context"}
    assert "本轮待汇报" in out["context"]


# ── 2. 非指挥一个字都不注入 ───────────────────────────────────────────────


def test_non_conductor_gets_nothing(FR, monkeypatch, _isolate):
    _as_other(FR, monkeypatch)
    _ticket_with_report()
    assert FR.build_forced_context("sess-2") == ""
    assert FR.on_pre_llm_call(session_id="sess-2") == {}


def test_identity_failure_is_silent(FR, monkeypatch, _isolate):
    def boom(_sid=""):
        raise RuntimeError("registry down")

    monkeypatch.setattr(FR, "_current_name", boom)
    _ticket_with_report()
    assert FR.build_forced_context("sess-3") == "", "身份读不出 ⇒ 静默，不打断回合"


# ── 3. 没有 escalation ⇒ 空 ───────────────────────────────────────────────


def test_progress_only_is_silent(FR, monkeypatch, _isolate):
    _as_conductor(FR, monkeypatch)
    _ticket_with_report(state="progress")
    assert FR.build_forced_context("sess-4") == "", "纯进度不报"


def test_no_ticket_is_silent(FR, monkeypatch, _isolate):
    _as_conductor(FR, monkeypatch)
    assert FR.build_forced_context("sess-5") == "", "没票就没得报"


# ── 4. 一次一件：多件只念第一件 ───────────────────────────────────────────


def test_only_first_of_many(FR, monkeypatch, _isolate):
    _as_conductor(FR, monkeypatch)
    _ticket_with_report(goal="改配色")
    _ticket_with_report(goal="补文档")
    lines = FR.pending_report_lines("sess-6")
    assert len(lines) == 2
    text = FR.build_forced_context("sess-6")
    assert "另有 1 件" in text
    assert "这一轮先只说这一件" in text


# ── 5. 常驻 A/B 对偶：判据空 ⇒ 不注入（不再靠临时改源码）────────────────────


def test_switch_off_via_injected_lines(FR):
    """把判据结果喂空（等价旧 A/B 的 `lines = []`），注入必须消失。

    这是「关掉注入」的常驻对偶：不碰源码、不 monkeypatch 模块、不读盘，直接经
    注入口证明注入串完全由判据驱动——判据空就一个字都不进上下文。配合
    `test_conductor_gets_forced_context`（判据非空⇒注入），两面都钉住。
    """
    assert FR.build_forced_context("sess-x", lines=[]) == ""


def test_injected_lines_are_rendered_verbatim(FR):
    """注入口喂进来的结论逐字进上下文——证明输出不是硬编码、真的来自判据。"""
    text = FR.build_forced_context("sess-y", lines=["「独一无二的那件事」卡住了。要我继续，还是你来定？"])
    assert "独一无二的那件事" in text
    assert "say to=用户" in text


# ── 5. 真实身份闸：名册不是指挥 ⇒ 空（A/B 的落点）─────────────────────────


def test_real_identity_gate_uses_registry(FR, monkeypatch, _isolate):
    """这条路走真名册：伪造一个非指挥名，即使票在，也不许注入。"""
    CH = importlib.import_module(f"{PKG}.channel")

    monkeypatch.setattr(FR, "_current_name", lambda sid="": "SomePrincipal")
    monkeypatch.setattr(CH, "_is_conductor", lambda name: False)
    _ticket_with_report()
    # 不替换 FR._is_conductor_name，让它真去问 CH._is_conductor
    assert FR.pending_report_lines("sess-7") == []
