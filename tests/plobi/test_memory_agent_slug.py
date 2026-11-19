"""身份两种写法要落到同一份记忆（`tools/memory_tool.resolve_agent_slug`）。

现核的坑：桌面会话 `sessions.agent_id` 全空、`$PLOBI_HOME/memory/agents/` 只有 `l1` 一份——
profile 退休后"家名 → 名册"那一跳断了，于是所有课程共用 l1 的记忆。
这一条钉最底下那一步：上游递来名册 name 还是旧的家名，解析出的 slug 必须相同；
没有身份时落到**指挥在名册里的 name**，不是写死的 `l1`。

2026-11-19 用户裁定「不要做旧数据兼容层了」：历史目录 `agents/l1/` 已改名为
`agents/secretary/`，别名那一条判据连同实现一起删掉。
"""

from __future__ import annotations

from types import SimpleNamespace

from tools.memory_tool import conductor_slug, resolve_agent_slug


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


def test_no_identity_falls_back_to_the_conductor_roster_name(monkeypatch):
    monkeypatch.delenv("PLOBI_AGENT_ID", raising=False)
    assert resolve_agent_slug() == conductor_slug()


def test_the_conductor_answers_to_its_roster_name(monkeypatch):
    """指挥判据认的是**名册 name**，不是写死的 `l1`（2026-11-19 现网的病根）。

    11-18 那刀把 `is_l1_slug` 改名成 `is_conductor_slug`，只换了标识符、没换比较的值
    （`CONDUCTOR_SLUG = "l1"`）。指挥在名册里叫 `secretary`，于是判据永远为假——
    连带两件事一起断：自己的 `MEMORY.md` 读不到，各首席近况摘要那一段根本不拼。

    这条钉两半：① 名册 name 判真；② 真首席判假；③ `l1` 这个历史目录名**不再**
    被判真（兼容层已拆，盘上目录也改了名）。
    """
    import plobi.agents.registry as registry
    import plobi.mind.identity as I
    from tools import memory_tool

    monkeypatch.setattr(
        registry,
        "load_registry",
        lambda: SimpleNamespace(
            entries=lambda: [
                SimpleNamespace(name="secretary", role="conductor"),
                SimpleNamespace(name="agenda", role="principal"),
            ]
        ),
    )
    monkeypatch.setattr(I, "resolve_agent_name", lambda value: value)

    assert memory_tool.conductor_slug() == "secretary"
    assert memory_tool.is_conductor_slug("secretary"), "名册 name 必须判真"
    assert not memory_tool.is_conductor_slug("l1"), "历史目录名不再有第二个身份"
    assert not memory_tool.is_conductor_slug("agenda"), "首席不是指挥"


def test_the_conductor_ledger_lives_under_its_roster_name(tmp_path, monkeypatch):
    """没有别名：指挥的记忆目录就是 `agents/<名册 name>`，不存在第二个落点。"""
    import plobi.agents.registry as registry
    import plobi.mind.identity as I
    from tools import memory_tool

    monkeypatch.setattr(
        registry,
        "load_registry",
        lambda: SimpleNamespace(entries=lambda: [SimpleNamespace(name="secretary", role="conductor")]),
    )
    monkeypatch.setattr(I, "resolve_agent_name", lambda value: value)
    monkeypatch.setattr(memory_tool, "get_memory_dir", lambda: tmp_path / "memory")

    legacy = tmp_path / "memory" / "agents" / "l1"
    legacy.mkdir(parents=True)
    (legacy / "MEMORY.md").write_text("- 我在盯排期\n", encoding="utf-8")

    assert memory_tool.agent_memory_dir("secretary") == tmp_path / "memory" / "agents" / "secretary"
    assert memory_tool.agent_memory_dir("agenda").name == "agenda"


def test_the_session_row_binds_identity_when_there_is_no_env(tmp_path, monkeypatch):
    """没有 PLOBI_AGENT_ID 时，按**会话行**认：agent_id 优先，其次 cwd 的目录归属。

    这是桌面正路（渲染层不传 agent、profile 也退休了）：记忆落到哪一份，由这条会话
    属于哪个 Agent 决定，而不是永远落到默认那一个。
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
