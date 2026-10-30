"""身份两种写法要落到同一份记忆（`tools/memory_tool.resolve_agent_slug`）。

现核的坑：桌面会话 `sessions.agent_id` 全空、`$PLOBI_HOME/memory/agents/` 只有 `l1` 一份——
profile 退休后"家名 → 名册"那一跳断了，于是所有课程共用 l1 的记忆。
这一条钉最底下那一步：上游递来名册 name 还是旧的家名，解析出的 slug 必须相同；都没有才是 l1。
"""

from __future__ import annotations

from types import SimpleNamespace

from tools.memory_tool import L1_SLUG, resolve_agent_slug


def test_profile_home_and_roster_name_resolve_to_the_same_slug(monkeypatch):
    import plobi.agents.registry as registry

    monkeypatch.setattr(
        registry,
        "entry_for_profile",
        lambda profile: SimpleNamespace(name="l2-course-theomech-a")
        if profile == "l2-course-theomech-a"
        else None,
    )

    monkeypatch.setenv("PLOBI_AGENT_ID", "l2-course-theomech-a")
    by_either_spelling = resolve_agent_slug()
    assert by_either_spelling == "l2-course-theomech-a", by_either_spelling


def test_no_identity_still_falls_back_to_l1(monkeypatch):
    monkeypatch.delenv("PLOBI_AGENT_ID", raising=False)
    assert resolve_agent_slug() == L1_SLUG


def test_the_session_row_binds_identity_when_there_is_no_env(tmp_path, monkeypatch):
    """没有 PLOBI_AGENT_ID 时，按**会话行**认：agent_id 优先，其次 cwd 的目录归属。

    这是桌面正路（渲染层不传 agent、profile 也退休了）：记忆落到哪一份，由这条会话
    属于哪个 Agent 决定，而不是永远落到 l1。
    """
    from types import SimpleNamespace

    import plobi.agents.registry as R
    import plobi.mind.identity as I
    from gateway import session_context
    from plobi_state import SessionDB

    db_path = tmp_path / "state.db"
    db = SessionDB(db_path=db_path)
    db.create_session("s1", "desktop", agent_id="", cwd=r"D:\Cloud\Courses\2026-秋-甲")
    db.create_session("s2", "desktop", agent_id="course-b", cwd="")

    import plobi_constants

    monkeypatch.setattr(plobi_constants, "get_plobi_state_home", lambda: tmp_path)
    monkeypatch.setattr(I, "resolve_agent_name", lambda value: value if value == "course-b" else "")
    monkeypatch.setattr(I, "resolve_agent_for_path", lambda path: "course-a" if "2026-秋-甲" in path else "")

    monkeypatch.delenv("PLOBI_AGENT_ID", raising=False)
    monkeypatch.setattr(session_context, "get_session_env", lambda name: "s1" if name == "PLOBI_SESSION_ID" else "")

    from tools import memory_tool

    assert memory_tool.resolve_agent_slug() == "course-a", "会话没写 agent_id ⇒ 按 cwd 认"

    monkeypatch.setattr(session_context, "get_session_env", lambda name: "s2" if name == "PLOBI_SESSION_ID" else "")
    assert memory_tool.resolve_agent_slug() == "course-b", "会话写了 agent_id ⇒ 直接用它"
