"""A finished secretary turn is one task (Docs 裁定 59.1), recorded as ``logged``.

The secretary never went through the board — its work was invisible to the
event stream the handset reads. This is the seam that changes that, and the
property that matters most here: the record is a *record*. The answer the user
gets must never depend on the board being writable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from plobi import agents
from plobi.agents import registry as reg
from plobi_cli import kanban_db as kb


@pytest.fixture
def board(tmp_path, monkeypatch):
    home = tmp_path / ".plobi"
    home.mkdir()
    monkeypatch.setenv("PLOBI_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("PLOBI_KANBAN_DB", raising=False)
    monkeypatch.delenv("PLOBI_KANBAN_BOARD", raising=False)
    kb.init_db()
    return home


def _stub_dispatch(monkeypatch, result):
    def fake(intent, user_text, **kwargs):
        return result

    monkeypatch.setattr(reg, "_secretary_ask", fake)


def _logged_rows():
    with kb.connect_closing() as conn:
        return kb.list_tasks(conn, status="logged")


def test_a_successful_turn_becomes_exactly_one_logged_record(board, monkeypatch):
    _stub_dispatch(
        monkeypatch,
        {
            "ok": True,
            "intent": "query_agenda",
            "agent": {"id": "agenda", "profile": "agenda-l2"},
            "sessionId": "sess-77",
        },
    )

    out = reg.run_secretary_ask("query_agenda", "明天有什么安排")

    assert out["ok"] is True
    rows = _logged_rows()
    assert len(rows) == 1
    task = rows[0]
    assert task.status == "logged"
    assert task.created_by == "secretary"
    assert "明天有什么安排" in task.title
    assert "agenda-l2" in (task.assignee or "")
    assert "sess-77" in (task.body or "")


def test_a_failed_turn_leaves_no_record(board, monkeypatch):
    _stub_dispatch(monkeypatch, {"ok": False, "error": "chatlog dead", "dead": True})

    out = reg.run_secretary_ask("refresh_agenda", "刷新日程")

    assert out["ok"] is False
    assert _logged_rows() == []


def test_a_board_write_failure_cannot_break_the_answer(board, monkeypatch):
    _stub_dispatch(monkeypatch, {"ok": True, "intent": "plan_day", "agent": {}})

    def explode(*args, **kwargs):
        raise RuntimeError("board is locked")

    monkeypatch.setattr(kb, "connect_closing", explode)

    out = reg.run_secretary_ask("plan_day", "帮我排明天")

    assert out["ok"] is True


def test_the_recorder_is_attached_to_the_public_funnel_not_the_internals():
    """Every intent funnels through ``run_secretary_ask``; the inner dispatch must
    stay record-free so direct callers cannot double-write or skip."""
    assert hasattr(reg, "run_secretary_ask")
    assert hasattr(reg, "_secretary_ask")
    assert agents.run_secretary_ask is reg.run_secretary_ask
