"""停口 ``ticket_revoke``（裁定 224）＋ 隔离端到端闭环。

用户 11-15 22:4x「不用管装机版。我们开始吧」——原纸
``Docs/tasks/tickets-read-task.md`` 里那四步真机验收要重启桌面，这一档把同一条
链路搬进源码树：真 ``handle_say`` / ``handle_ticket_report`` / ``handle_tickets``
/ ``handle_ticket_revoke``，只有投递、唤醒、痕文件和身份用替身。**不碰安装位、
不碰 ``PLOBI_HOME``、不重启任何进程**。

停口不是第三枚嘴（同 ``ticket_report`` 的旁路口径）：它**会改状态**，但
① 不向任何 Agent 发提示词；② 只动自己发起的那一张票；③ 不往 ``ticket_reports``
插一条（撤销是发起方的动作，不该伪装成收件人的六态回执）。
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[2]
PLUGIN_DIR = REPO / "plugins" / "plobi-core"


@pytest.fixture(scope="module")
def CH():
    """按包方式加载（channel.py 有相对导入 ``from . import wake``）。"""
    pkg_name = "plobi_test_ticket_revoke_pkg"
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
    monkeypatch.setattr(CH, "current_agent_name", lambda session_id="": name)


def _snapshot(db_path: Path) -> tuple:
    conn = sqlite3.connect(str(db_path))
    try:
        tickets = conn.execute(
            "SELECT id, parent_agent, leg, goal, status, created_at, updated_at, revoke_reason "
            "FROM tickets ORDER BY id"
        ).fetchall()
        reports = conn.execute(
            "SELECT ticket_id, state, note, reporter FROM ticket_reports ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    return tickets, reports


# ── 停口本身的判据 ────────────────────────────────────────────────────────


def test_revoke_freezes_own_active_ticket(CH, ledger, monkeypatch, _isolate_ticket_db):
    mine = ledger.record_router(sender="conductor", recipient="nymo", mode="say", goal="改配色")
    assert mine.status == "active"

    _as(CH, monkeypatch, "conductor")
    out = json.loads(CH.handle_ticket_revoke({"ticket_id": mine.id, "reason": "不需要了"}))

    assert out["ok"] is True, out
    assert out["status"] == "revoked"
    assert out["mode"] == "ticket_revoke"
    row = ledger.get(mine.id)
    assert row.status == "revoked"
    assert row.revoke_reason == "不需要了"


def test_revoke_is_idempotent_and_does_not_rewrite(CH, ledger, monkeypatch, _isolate_ticket_db):
    mine = ledger.record_router(sender="conductor", recipient="nymo", mode="say", goal="改配色")
    _as(CH, monkeypatch, "conductor")
    CH.handle_ticket_revoke({"ticket_id": mine.id})
    before = _snapshot(_isolate_ticket_db)

    again = json.loads(CH.handle_ticket_revoke({"ticket_id": mine.id}))

    assert again["ok"] is True and again["already_revoked"] is True
    assert _snapshot(_isolate_ticket_db) == before, "重复撤销不该再动一次库"


def test_only_initiator_can_revoke(CH, ledger, monkeypatch):
    mine = ledger.record_router(sender="conductor", recipient="nymo", mode="say", goal="改配色")

    for other in ("someone-else", "nymo"):  # 连收件人也不许替发起方叫停
        _as(CH, monkeypatch, other)
        out = json.loads(CH.handle_ticket_revoke({"ticket_id": mine.id}))
        assert out["ok"] is False, out
        assert "goal" not in out, "越权者不该看到票的内容"
    assert ledger.get(mine.id).status == "active"


def test_terminal_ticket_cannot_be_revoked(CH, ledger, monkeypatch):
    done = ledger.record_router(sender="conductor", recipient="nymo", mode="ask", goal="查进度")
    ledger.finish(done.id, ok=True)
    _as(CH, monkeypatch, "conductor")

    out = json.loads(CH.handle_ticket_revoke({"ticket_id": done.id}))

    assert out["ok"] is False and "已经收口" in out["error"]
    assert ledger.get(done.id).status == "done"


def test_revoke_sends_no_prompt(CH, ledger, monkeypatch):
    mine = ledger.record_router(sender="conductor", recipient="nymo", mode="say", goal="改配色")
    calls: list[str] = []
    monkeypatch.setattr(CH, "_deliver", lambda *a, **k: calls.append("_deliver") or ("", ""))
    monkeypatch.setattr(CH, "_deliver_to_user", lambda *a, **k: calls.append("_deliver_to_user") or "")
    monkeypatch.setattr(
        CH.WK, "wake_agent", lambda *a, **k: calls.append("wake_agent") or (False, "")
    )
    _as(CH, monkeypatch, "conductor")

    CH.handle_ticket_revoke({"ticket_id": mine.id})

    assert calls == [], f"停口叫了人：{calls}"


def test_revoke_touches_only_that_ticket_and_writes_no_fake_report(
    CH, ledger, monkeypatch, _isolate_ticket_db
):
    mine = ledger.record_router(sender="conductor", recipient="nymo", mode="say", goal="改配色")
    other = ledger.record_router(sender="conductor", recipient="sindri", mode="say", goal="写报告")
    ledger.report_state(mine.id, "progress", note="在做", reporter="nymo")
    before_other = ledger.get(other.id).as_dict()
    before_reports = _snapshot(_isolate_ticket_db)[1]

    _as(CH, monkeypatch, "conductor")
    CH.handle_ticket_revoke({"ticket_id": mine.id})

    assert ledger.get(other.id).as_dict() == before_other, "撤销串到别的票上了"
    assert _snapshot(_isolate_ticket_db)[1] == before_reports, (
        "撤销不该伪装成收件人的六态回执"
    )


def test_missing_and_unknown_ticket(CH, ledger, monkeypatch):
    _as(CH, monkeypatch, "conductor")
    assert json.loads(CH.handle_ticket_revoke({}))["ok"] is False
    assert json.loads(CH.handle_ticket_revoke({"ticket_id": "t_does_not_exist"}))["ok"] is False


# ── 隔离端到端闭环：原纸上那四步，外加撤销与反向对照 ────────────────────────


def test_isolated_end_to_end_closed_loop(CH, ledger, monkeypatch):
    """真 handler 链路跑一遍（投递／唤醒／痕文件用替身，不碰任何真实家目录）。

    对应 ``Docs/tasks/tickets-read-task.md`` 的四步：派一件 → 等回执 → 读回 →
    另一个 Agent 读同一张被拒。再补上这一刀的停口与「撤完再派」的执行层判据。
    """
    entry = SimpleNamespace(name="nymo")
    message_ids = iter(("msg-1", "msg-2"))
    wakes: list[str] = []

    monkeypatch.setattr(CH, "_deliver_or_fail", lambda to, parts: (entry, "改配色", None))
    monkeypatch.setattr(
        CH, "_deliver",
        lambda e, body, *, sender, reply_to="": ("sess-1", next(message_ids)),
    )
    monkeypatch.setattr(
        CH.WK, "wake_agent",
        lambda *a, **k: wakes.append("wake") or (True, "woken"),
    )
    monkeypatch.setattr(CH, "_trace", lambda record: None)

    # ① 指挥派一件活（say 自动开票 + 起轮）
    _as(CH, monkeypatch, "conductor")
    first = json.loads(CH.handle_say({"to": "nymo", "parts": ["改配色"]}))
    assert first["ok"] is True and first["woke"] is True, first
    ticket_id = first["ticket_id"]
    assert ledger.get(ticket_id).status == "active"

    # ② 收件方报一次 progress（旁路状态通道）
    _as(CH, monkeypatch, "nymo")
    report = json.loads(
        CH.handle_ticket_report({"ticket_id": ticket_id, "state": "progress", "note": "在做"})
    )
    assert report["ok"] is True and report["reporter"] == "nymo"

    # ③ 发起方读回来
    _as(CH, monkeypatch, "conductor")
    view = json.loads(CH.handle_tickets({"ticket_id": ticket_id}))
    assert view["ok"] is True
    assert view["ticket"]["last_report"]["state"] == "progress", view

    # ④ 反向对照：别人读同一张，被拒且看不到 goal
    _as(CH, monkeypatch, "someone-else")
    denied = json.loads(CH.handle_tickets({"ticket_id": ticket_id}))
    assert denied["ok"] is False and "goal" not in denied
    assert json.loads(CH.handle_ticket_revoke({"ticket_id": ticket_id}))["ok"] is False

    # ⑤ 发起方叫停
    _as(CH, monkeypatch, "conductor")
    stopped = json.loads(
        CH.handle_ticket_revoke({"ticket_id": ticket_id, "reason": "改主意了"})
    )
    assert stopped["ok"] is True and stopped["status"] == "revoked"
    after = json.loads(CH.handle_tickets({"ticket_id": ticket_id}))
    assert after["ticket"]["status"] == "revoked", "读口要看得见撤销"

    # ⑥ 同一条 thread 再派一次：话照旧送到，但执行层拒绝起轮
    wakes_before = len(wakes)
    followup = json.loads(
        CH.handle_say({"to": "nymo", "parts": ["继续"], "reply_to": "msg-1"})
    )
    assert followup["ok"] is True, "撤销不该堵住说话这条路"
    assert followup["woke"] is False and "撤销" in followup["wakeNote"], followup
    assert followup["ticket_status"] == "revoked", "thread 继承没把新票也冻住"
    assert len(wakes) == wakes_before, "被撤销的票不该再叫醒收件人"
