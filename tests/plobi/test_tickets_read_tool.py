"""读口 ``tickets``（裁定 222）：让 Router 能问「我派出去那几件到哪了」。

这一档测的不是快照，是**三条边界**——它们正是这一枚不被算作「第三枚嘴」的前提：

1. **只读**：调完以后 ``tickets`` / ``ticket_reports`` 两张表逐行不变（含 ``updated_at``）；
2. **不发提示词**：``_deliver`` / ``_deliver_to_user`` / ``wake_agent`` 一次都不许被调；
3. **不改状态**：票的 ``status`` 不因读而推进（尤其不许把 active 读成 done）。

另加三条可见性：只看得到自己发起的与派给自己的；别人的票连 ``goal`` 都不许漏。
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
    pkg_name = "plobi_test_tickets_read_pkg"
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
def _isolate_ticket_db(monkeypatch, tmp_path):
    """票库落在 tmp：这一档不写用户家目录。"""
    path = tmp_path / "tickets.db"
    monkeypatch.setenv("PLOBI_TICKET_DB", str(path))
    yield path


@pytest.fixture()
def ledger(_isolate_ticket_db):
    from plobi.delegation.tickets import TicketLedger

    return TicketLedger()


def _as(CH, monkeypatch, name: str) -> None:
    """把「我是谁」钉死——真实现从会话行拿，这里不碰会话库。"""
    monkeypatch.setattr(CH, "current_agent_name", lambda session_id="": name)


def _snapshot(db_path: Path) -> tuple:
    """两张表的全量快照：行数、每一列、顺序都算进去。"""
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


# ── 三条边界 ──────────────────────────────────────────────────────────────


def test_read_is_read_only(CH, ledger, monkeypatch, _isolate_ticket_db):
    """只读：读多少次，两张表都一个字不动（含 updated_at 与票的 status）。"""
    mine = ledger.record_router(sender="conductor", recipient="nymo", mode="say", goal="改配色")
    ledger.report_state(mine.id, "progress", note="在做", reporter="nymo")
    before = _snapshot(_isolate_ticket_db)

    _as(CH, monkeypatch, "conductor")
    assert json.loads(CH.handle_tickets({}))["ok"] is True
    assert json.loads(CH.handle_tickets({"ticket_id": mine.id}))["ok"] is True

    assert _snapshot(_isolate_ticket_db) == before, "读口动了票库——它必须是纯 SELECT"


def test_read_sends_no_prompt(CH, ledger, monkeypatch):
    """不发提示词：三条能叫人起床的路径一次都不许被调。"""
    ledger.record_router(sender="conductor", recipient="nymo", mode="say", goal="改配色")
    calls: list[str] = []

    monkeypatch.setattr(CH, "_deliver", lambda *a, **k: calls.append("_deliver") or ("", ""))
    monkeypatch.setattr(CH, "_deliver_to_user", lambda *a, **k: calls.append("_deliver_to_user") or "")
    monkeypatch.setattr(
        CH.WK, "wake_agent", lambda *a, **k: calls.append("wake_agent") or (False, "")
    )
    _as(CH, monkeypatch, "conductor")

    CH.handle_tickets({})
    CH.handle_tickets({"status": "active"})

    assert calls == [], f"读口叫了人：{calls}"


def test_read_does_not_push_ticket_to_terminal_state(CH, ledger, monkeypatch):
    """不改状态：读一张 active 的票，它读前读后都是 active。"""
    t = ledger.record_router(sender="conductor", recipient="nymo", mode="ask", goal="问一句")
    assert t.status == "active"

    _as(CH, monkeypatch, "conductor")
    out = json.loads(CH.handle_tickets({"ticket_id": t.id}))

    assert out["ticket"]["status"] == "active"
    assert ledger.get(t.id).status == "active"


# ── 看得见谁 ──────────────────────────────────────────────────────────────


def test_lists_only_what_i_started(CH, ledger, monkeypatch):
    """列出来的只有我发起的：别人的活不进来。"""
    a = ledger.record_router(sender="conductor", recipient="nymo", mode="say", goal="改配色")
    b = ledger.record_router(sender="conductor", recipient="agenda", mode="ask", goal="问日程")
    ledger.record_router(sender="nymo", recipient="agenda", mode="say", goal="别人的活")

    _as(CH, monkeypatch, "conductor")
    out = json.loads(CH.handle_tickets({}))

    assert out["ok"] is True
    assert out["count"] == 2
    assert {t["ticket_id"] for t in out["tickets"]} == {a.id, b.id}


def test_cannot_read_someone_elses_ticket(CH, ledger, monkeypatch):
    """越权：别人的票连 goal 都不许漏，只回一句看不到。"""
    other = ledger.record_router(sender="nymo", recipient="agenda", mode="say", goal="这是别人的秘密目标")

    _as(CH, monkeypatch, "conductor")
    out = json.loads(CH.handle_tickets({"ticket_id": other.id}))

    assert out["ok"] is False
    assert "看不到" in out["error"]
    assert "秘密目标" not in json.dumps(out, ensure_ascii=False)


def test_recipient_can_read_the_ticket_addressed_to_it(CH, ledger, monkeypatch):
    """派给我的那一张，我读得到（回执是写给收件方的）。"""
    t = ledger.record_router(sender="conductor", recipient="nymo", mode="say", goal="改配色")
    ledger.report_state(t.id, "waiting", note="等你的色号", reporter="nymo")

    _as(CH, monkeypatch, "nymo")
    out = json.loads(CH.handle_tickets({"ticket_id": t.id}))

    assert out["ok"] is True
    assert out["ticket"]["to"] == "nymo"
    assert out["ticket"]["last_report"]["state"] == "waiting"


# ── 读出来的形状 ──────────────────────────────────────────────────────────


def test_single_ticket_returns_report_history(CH, ledger, monkeypatch):
    """单张读：带最近回执，且按发生顺序给出历史（10 条以内）。"""
    t = ledger.record_router(sender="conductor", recipient="nymo", mode="say", goal="改配色")
    ledger.report_state(t.id, "progress", note="开始", reporter="nymo")
    ledger.report_state(t.id, "waiting", note="等色号", reporter="nymo")

    _as(CH, monkeypatch, "conductor")
    out = json.loads(CH.handle_tickets({"ticket_id": t.id}))
    view = out["ticket"]

    assert view["ticket_id"] == t.id
    assert view["last_report"]["state"] == "waiting"
    assert [r["state"] for r in view["reports"]] == ["progress", "waiting"]
    assert view["created_at"] and view["updated_at"]


def test_filters_by_status_and_leg(CH, ledger, monkeypatch):
    """过滤：status 与 leg 各自独立生效，不认识的值直接报错并列候选。"""
    live = ledger.record_router(sender="conductor", recipient="nymo", mode="say", goal="还在跑")
    done = ledger.record_router(sender="conductor", recipient="agenda", mode="say", goal="已收口")
    ledger.finish(done.id, ok=True)

    _as(CH, monkeypatch, "conductor")
    only_active = json.loads(CH.handle_tickets({"status": "active"}))
    assert {t["ticket_id"] for t in only_active["tickets"]} == {live.id}

    only_router = json.loads(CH.handle_tickets({"leg": "router"}))
    assert {t["ticket_id"] for t in only_router["tickets"]} == {live.id, done.id}

    bad = json.loads(CH.handle_tickets({"status": "跑着"}))
    assert bad["ok"] is False and "active" in bad["states"]


def test_limit_is_clamped(CH, ledger, monkeypatch):
    """上限 50：给一个荒唐的 limit 也只列 50 张，且不报错。"""
    for i in range(55):
        ledger.record_router(sender="conductor", recipient="nymo", mode="say", goal=f"活 {i}")

    _as(CH, monkeypatch, "conductor")
    out = json.loads(CH.handle_tickets({"limit": 9999}))

    assert out["ok"] is True
    assert out["count"] == 50


def test_unknown_ticket_is_a_clean_error(CH, ledger, monkeypatch):
    """票号不存在时给一句能懂的话，不是抛栈。"""
    _as(CH, monkeypatch, "conductor")
    out = json.loads(CH.handle_tickets({"ticket_id": "t_nope"}))

    assert out["ok"] is False
    assert "找不到" in out["error"]


def test_empty_ledger_says_so(CH, monkeypatch):
    """一张都没有时给 hint，不是空对象让模型自己猜。"""
    _as(CH, monkeypatch, "conductor")
    out = json.loads(CH.handle_tickets({}))

    assert out["ok"] is True and out["count"] == 0
    assert out["hint"]
