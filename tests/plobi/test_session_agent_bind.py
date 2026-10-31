"""会话身份只填空、不覆盖（用户 2026-10-30：改名只动 display_name，身份绑过就不动）。"""
from __future__ import annotations

from plobi.mind.identity import bind_session_agent
from plobi_state import SessionDB


def test_set_session_agent_only_fills_an_empty_row(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session("s-empty", "desktop", agent_id="")
    db.create_session("s-bound", "desktop", agent_id="course-a")

    assert bind_session_agent("s-empty", "course-b", db=db) is True
    assert (db.get_session("s-empty") or {}).get("agent_id") == "course-b"

    # 已绑过的不许被改（改名只改显示名，不搬身份）
    assert bind_session_agent("s-bound", "course-b", db=db) is False
    assert (db.get_session("s-bound") or {}).get("agent_id") == "course-a"

    # 空身份是 no-op
    assert bind_session_agent("s-empty", "", db=db) is False
