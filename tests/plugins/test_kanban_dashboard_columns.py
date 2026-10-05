"""The dashboard's column list must never lie about a task's status.

``plugin_api.BOARD_COLUMNS`` is the board's left-to-right truth, and the
bucketing step historically filed anything it didn't recognise under ``todo``
— i.e. it rendered "recorded, never dispatchable" as "waiting to be
dispatched". These tests keep that from coming back, for ``logged`` and for
whatever status is added next.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plobi_cli import kanban_db as kb

# Statuses deliberately absent from the board: ``archived`` is reachable only
# through the include_archived toggle, never as a visible column.
COLUMNLESS_STATUSES = {"archived"}


def _load_plugin():
    repo_root = Path(__file__).resolve().parents[2]
    plugin_file = repo_root / "plugins" / "kanban" / "dashboard" / "plugin_api.py"
    spec = importlib.util.spec_from_file_location(
        "plobi_kanban_dashboard_columns_test", plugin_file,
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    home = tmp_path / ".plobi"
    home.mkdir()
    monkeypatch.setenv("PLOBI_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("PLOBI_KANBAN_DB", raising=False)
    monkeypatch.delenv("PLOBI_KANBAN_BOARD", raising=False)
    kb.init_db()
    mod = _load_plugin()
    app = FastAPI()
    app.include_router(mod.router, prefix="/api/plugins/kanban")
    return mod, TestClient(app)


def test_every_status_has_a_column_or_an_exemption():
    missing = set(kb.VALID_STATUSES) - set(_load_plugin().BOARD_COLUMNS) - COLUMNLESS_STATUSES
    assert not missing, f"these statuses would be filed under the wrong column: {sorted(missing)}"


def test_the_exemption_list_has_no_dead_entries():
    assert COLUMNLESS_STATUSES <= set(kb.VALID_STATUSES)


def test_logged_tasks_get_their_own_column_on_the_board(plugin):
    _mod, client = plugin
    with kb.connect_closing() as conn:
        kb.create_task(
            conn, title="secretary:query_agenda · 今天有什么",
            created_by="secretary", initial_status="logged",
        )
        kb.create_task(conn, title="real work", assignee="worker")

    columns = {c["name"]: c["tasks"] for c in client.get("/api/plugins/kanban/board").json()["columns"]}

    assert [t["title"] for t in columns["logged"]] == ["secretary:query_agenda · 今天有什么"]
    assert columns["todo"] == []


def test_an_unknown_status_is_never_masquerading_as_todo(plugin):
    """The board may be behind the kernel; that must show up as a new column."""
    _mod, client = plugin
    with kb.connect_closing() as conn:
        task_id = kb.create_task(conn, title="from a newer kernel", assignee="worker")
        conn.execute("UPDATE tasks SET status = 'not_yet_on_the_board' WHERE id = ?", (task_id,))

    columns = {c["name"]: c["tasks"] for c in client.get("/api/plugins/kanban/board").json()["columns"]}

    assert [t["id"] for t in columns["not_yet_on_the_board"]] == [task_id]
    assert columns["todo"] == []
