"""临时专家（WP-L3-EXPERTS）：不进名册，只留一条 job 记录（子会话）。"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from plobi.delegation import experts as ex  # noqa: E402


def make_db(tmp_path: Path) -> Path:
    db = tmp_path / "state.db"
    con = sqlite3.connect(db)
    con.executescript(
        "create table sessions (id text, source text, parent_session_id text, agent_id text,"
        " started_at real, ended_at real, message_count integer, tool_call_count integer, model text);"
        "create table messages (session_id text, role text, content text);"
    )
    con.execute("insert into sessions values ('parent-1','cli','', 'Sindri', 1.0, NULL, 3, 1, 'm')")
    con.execute("insert into sessions values ('parent-2','cli','', 'Other', 1.0, NULL, 3, 1, 'm')")
    con.execute("insert into sessions values ('kid-1','subagent','parent-1','', 2.0, NULL, 4, 2, 'glm')")
    con.execute("insert into sessions values ('kid-2','subagent','parent-1','', 3.0, 9.0, 6, 5, 'glm')")
    con.execute("insert into sessions values ('kid-other','subagent','parent-2','', 3.0, 9.0, 6, 5, 'glm')")
    con.execute("insert into messages values ('kid-1','user','把三份资料折成一份汇总')")
    con.execute("insert into messages values ('kid-2','user','查一下这门课的作业')")
    con.commit()
    con.close()

    return db


def test_experts_come_from_child_sessions_of_this_agent_only(tmp_path, monkeypatch):
    db = make_db(tmp_path)
    monkeypatch.setattr(ex, "state_db_path", lambda: db)

    payload = ex.experts_for_agent("Sindri")

    rows = {item["id"]: item for item in payload["experts"]}
    assert set(rows) == {"kid-1", "kid-2"}, "只列这个 Agent 的子会话"
    assert rows["kid-1"]["goal"] == "把三份资料折成一份汇总"
    assert rows["kid-2"]["goal"] == "查一下这门课的作业"


def test_a_live_subagent_marks_its_row_as_running(tmp_path, monkeypatch):
    db = make_db(tmp_path)
    monkeypatch.setattr(ex, "state_db_path", lambda: db)
    import tools.delegate_tool as delegate

    monkeypatch.setattr(
        delegate,
        "list_active_subagents",
        lambda: [{"subagent_id": "s1", "child_session_id": "kid-2", "tool_count": 7, "status": "running"}],
    )

    payload = ex.experts_for_agent("Sindri")

    rows = {item["id"]: item for item in payload["experts"]}
    assert rows["kid-1"]["status"] == "done"
    assert rows["kid-2"]["status"] == "running"
    assert rows["kid-2"]["tool_calls"] == 7
    assert payload["live"] == 1


def test_an_agent_without_sessions_says_why(tmp_path, monkeypatch):
    db = make_db(tmp_path)
    monkeypatch.setattr(ex, "state_db_path", lambda: db)

    payload = ex.experts_for_agent("Nobody")

    assert payload["experts"] == [] and payload["reason"]
