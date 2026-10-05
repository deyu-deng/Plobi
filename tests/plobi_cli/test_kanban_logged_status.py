"""``logged`` — the kernel's one status with identity but no dispatchability.

Docs 裁定 59.1 ruled that a secretary turn counts as a task; 裁定 62 found the
kernel had no place for "recorded, never queued", and that ``triage`` (the only
inert status) is already owned by the L1 approval queue. ``logged`` is that
place. These tests pin the property that makes it safe: every path that can
turn a row into *work* must ignore it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from plobi_cli import kanban_db as kb


@pytest.fixture
def conn(tmp_path, monkeypatch):
    """A board in a throwaway PLOBI_HOME (never the real one)."""
    home = tmp_path / ".plobi"
    home.mkdir()
    monkeypatch.setenv("PLOBI_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("PLOBI_KANBAN_DB", raising=False)
    monkeypatch.delenv("PLOBI_KANBAN_BOARD", raising=False)
    kb.init_db()
    with kb.connect_closing() as board_conn:
        yield board_conn


def _created_status(conn, task_id: str) -> str:
    row = conn.execute(
        "SELECT payload FROM task_events WHERE task_id = ? AND kind = 'created'",
        (task_id,),
    ).fetchone()
    return json.loads(row["payload"])["status"]


def test_logged_task_lands_in_logged_and_says_so_in_its_event(conn):
    task_id = kb.create_task(
        conn,
        title="secretary:query_agenda · 明天有什么安排",
        body='{"intent": "query_agenda"}',
        created_by="secretary",
        initial_status="logged",
    )

    assert kb.get_task(conn, task_id).status == "logged"
    assert _created_status(conn, task_id) == "logged"


def test_logged_ignores_parents_instead_of_waiting_on_them(conn):
    """``ready``/``todo`` is derived from parent state; a record is not."""
    parent = kb.create_task(conn, title="real work", assignee="worker")
    logged = kb.create_task(
        conn, title="secretary:plan_day", created_by="secretary",
        initial_status="logged", parents=[parent],
    )

    assert kb.get_task(conn, logged).status == "logged"


def test_logged_task_rejects_unknown_parent(conn):
    with pytest.raises(ValueError, match="unknown parent"):
        kb.create_task(
            conn, title="secretary:x", created_by="secretary",
            initial_status="logged", parents=["t_missing"],
        )


def test_recompute_ready_promotes_the_todo_twin_but_never_the_record(conn):
    parent = kb.create_task(conn, title="parent", assignee="worker")
    waiting = kb.create_task(
        conn, title="child waiting on parent", parents=[parent], assignee="worker"
    )
    record = kb.create_task(
        conn, title="secretary:refresh_agenda", created_by="secretary",
        initial_status="logged", parents=[parent],
    )
    assert kb.get_task(conn, waiting).status == "todo"

    # Close the parent out, then run the promotion pass the dispatcher does.
    conn.execute("UPDATE tasks SET status = 'done' WHERE id = ?", (parent,))
    promoted = kb.recompute_ready(conn)

    assert promoted >= 1
    assert kb.get_task(conn, waiting).status == "ready"
    assert kb.get_task(conn, record).status == "logged"


def test_claim_task_cannot_pick_up_a_record(conn):
    task_id = kb.create_task(
        conn, title="secretary:decide_pending", created_by="secretary",
        initial_status="logged",
    )

    assert kb.claim_task(conn, task_id, claimer="worker-1") is None
    assert kb.get_task(conn, task_id).status == "logged"


def test_promote_task_refuses_a_record(conn):
    task_id = kb.create_task(
        conn, title="secretary:write_briefing", created_by="secretary",
        initial_status="logged",
    )

    ok, reason = kb.promote_task(conn, task_id, actor="operator")

    assert ok is False
    assert "'logged'" in (reason or "")


def test_a_logged_record_is_listable_and_carries_its_own_cursor_window(conn):
    task_id = kb.create_task(
        conn, title="secretary:query_agenda", created_by="secretary",
        initial_status="logged",
    )

    rows = kb.list_tasks(conn, status="logged")

    assert [r.id for r in rows] == [task_id]
