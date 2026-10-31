"""`session["agent"]` 槽里是**活着的 AIAgent 对象**，不是名字。

`02bdc9e6`（[WP-MIND-PENDING-VISIBLE] 会话身份显式带上）把渲染层带来的名册名
写进了同一个槽，于是凡是桌面带 `agent` 的会话下一轮就是
`'str' object has no attribute 'run_conversation'`（10-31 21:24 现读：课程 Agent
全灭；L1 的 prompt 不带 agent 参数，所以活着——这也是为什么它没在第一轮就炸）。

这一档钉三件事：名字走 `agent_name_hint`；对象槽里躺着 str 的旧形状也认；
对象本身不能被当成名字。
"""

import pytest

from tui_gateway.server import _agent_id_for_session


class _FakeAgent:
    """只证明"这是一个对象、不是 str"。"""


@pytest.fixture
def fake_identity(monkeypatch):
    import plobi.mind.identity as identity

    def install(resolved):
        monkeypatch.setattr(identity, "resolve_agent_name", lambda name: resolved, raising=False)

    return install


def test_hint_wins_and_the_object_slot_is_untouched(fake_identity, monkeypatch):
    fake_identity("电子工程训练（乙）")
    session = {"agent": _FakeAgent(), "agent_name_hint": "电子工程训练（乙）"}

    assert _agent_id_for_session(session) == "电子工程训练（乙）"


def test_legacy_str_in_the_agent_slot_still_counts(fake_identity, monkeypatch):
    fake_identity("agenda")
    session = {"agent": "agenda"}

    assert _agent_id_for_session(session) == "agenda"


def test_object_in_the_agent_slot_is_never_read_as_a_name(monkeypatch):
    import plobi.mind.identity as identity

    seen: list[str] = []

    def fake_resolve(name):
        seen.append(name)
        return ""

    monkeypatch.setattr(identity, "resolve_agent_name", fake_resolve, raising=False)
    # 目录归属那一跳也拦下，保证返回的是空（= L1），而不是把对象当名字
    monkeypatch.setattr("tui_gateway.server._agent_id_for_profile_home", lambda home: "", raising=False)
    monkeypatch.setattr(
        identity, "resolve_agent_for_path", lambda path: "", raising=False
    )
    session = {"agent": _FakeAgent()}

    assert _agent_id_for_session(session) == ""
    # 名册解析从来不该被喂一个对象——喂进去的只能是 str
    assert all(isinstance(s, str) for s in seen)
    assert "agent_name_hint" not in session or isinstance(session.get("agent_name_hint"), str)
