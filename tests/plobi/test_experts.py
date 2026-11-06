"""临时专家（WP-L3-EXPERTS）：不进名册，只留一条 job 记录（子会话）。

11-04 起多一条判据：**只有带角色侧记的才算专家**。没带 `expert` 的普通子智能体
不进这一格——它们的工作日志归底栏那枚子智能体面板（用户 11-04：两条腿分开）。
"""

from __future__ import annotations

import json
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
    con.execute("insert into sessions values ('kid-plain','subagent','parent-1','', 4.0, 9.0, 6, 5, 'glm')")
    con.execute("insert into sessions values ('kid-other','subagent','parent-2','', 3.0, 9.0, 6, 5, 'glm')")
    con.execute("insert into messages values ('kid-1','user','把三份资料折成一份汇总')")
    con.execute("insert into messages values ('kid-2','user','查一下这门课的作业')")
    con.commit()
    con.close()

    return db


def write_sidecars(tmp_path: Path, monkeypatch, roles: dict[str, str]) -> None:
    """按 delegate_task 的落盘形状写角色侧记（`PLOBI_EXPERTS_DIR` 指到临时目录）。"""
    folder = tmp_path / "experts"
    folder.mkdir(exist_ok=True)
    for sid, role in roles.items():
        (folder / f"{sid}.json").write_text(
            json.dumps({"role": role, "goal": ""}, ensure_ascii=False), encoding="utf-8"
        )
    monkeypatch.setenv("PLOBI_EXPERTS_DIR", str(folder))


def test_experts_come_from_child_sessions_of_this_agent_only(tmp_path, monkeypatch):
    db = make_db(tmp_path)
    monkeypatch.setattr(ex, "state_db_path", lambda: db)
    write_sidecars(tmp_path, monkeypatch, {"kid-1": "file", "kid-2": "browser"})

    payload = ex.experts_for_agent("Sindri")

    rows = {item["id"]: item for item in payload["experts"]}
    assert set(rows) == {"kid-1", "kid-2"}, "只列这个 Agent 的、带角色的子会话"
    assert rows["kid-1"]["goal"] == "把三份资料折成一份汇总"
    assert rows["kid-2"]["goal"] == "查一下这门课的作业"


def test_plain_subagents_are_not_experts(tmp_path, monkeypatch):
    """没带 expert 的普通子智能体不进「专家」——它属于底栏那枚面板。"""
    db = make_db(tmp_path)
    monkeypatch.setattr(ex, "state_db_path", lambda: db)
    write_sidecars(tmp_path, monkeypatch, {"kid-1": "file"})

    payload = ex.experts_for_agent("Sindri")

    assert [item["id"] for item in payload["experts"]] == ["kid-1"]
    assert payload["experts"][0]["role"] == "file"


def test_a_live_subagent_marks_its_row_as_running(tmp_path, monkeypatch):
    db = make_db(tmp_path)
    monkeypatch.setattr(ex, "state_db_path", lambda: db)
    write_sidecars(tmp_path, monkeypatch, {"kid-1": "file", "kid-2": "browser"})
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
