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
