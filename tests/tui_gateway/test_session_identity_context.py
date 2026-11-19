"""会话上下文必须带上"这条会话归谁"，否则整条身份链被读成 L1。

现场（10-31 首现，11-18 复核仍在）：在某个 Principal 的屏上问「你是谁」，它答
「我是你的 Plobi 总秘书」。不是模型抽风，是**上下文被写成 L1 了**：

``_set_session_context`` 只传了 ``session_key``/``source``/``cwd``，而
``gateway.session_context.set_session_vars`` 对每个变量都是无条件 ``.set()``
⇒ ``PLOBI_AGENT_ID`` 与 ``PLOBI_SESSION_ID`` 每次都被显式置空。于是：

* ``tools.memory_tool.resolve_agent_slug`` 先看进程 env（桌面没有）、再看会话
  ContextVar（空）、再按会话行反推（``PLOBI_SESSION_ID`` 也是空）⇒ 一路空到底，
  回 ``"l1"``；
* ``agent.prompt_builder._soul_agent_id`` 见 ``l1`` 就认定"这是指挥自己"，去读
  **顶层那份 L1 秘书 SOUL** ⇒ Principal 当场自称总秘书；
* ``plobi.agents.registry._active_agent_profile_name`` 同样读空 ⇒ per-agent 的
  模型/工具集/记忆分持全部按 L1 算。

这一档钉的是那一跳：**身份从会话认出来后必须落进 ContextVar**，认不出仍是空
（= L1，不猜）。
"""

from __future__ import annotations

import pytest

import tui_gateway.server as server


@pytest.fixture
def identity_double(monkeypatch):
    """把名册那两跳换成可控的假实现（不读盘）。"""

    import plobi.mind.identity as identity

    def install(name_by_hint: str = "", name_by_path: str = ""):
        monkeypatch.setattr(
            identity,
            "resolve_agent_name",
            lambda value: name_by_hint if str(value or "").strip() else "",
            raising=False,
        )
        monkeypatch.setattr(
            identity, "resolve_agent_for_path", lambda path: name_by_path, raising=False
        )
        monkeypatch.setattr(
            server, "_agent_id_for_profile_home", lambda home: "", raising=False
        )

    return install


def _register(sid: str, key: str, **extra) -> dict:
    session = {"session_key": key, **extra}
    server._sessions[sid] = session
    return session


class _LiveAgent:
    """``session["agent"]`` 槽里的活对象——不是名字，不该被当成名字读。"""


def _drop(sid: str) -> None:
    server._sessions.pop(sid, None)


def test_context_carries_the_named_agent(identity_double, monkeypatch):
    """前端显式带名册名（``agent_name_hint``）时，上下文就报这个名字。"""
    from gateway.session_context import get_session_env
    from tools.memory_tool import resolve_agent_slug

    monkeypatch.delenv("PLOBI_AGENT_ID", raising=False)
    identity_double(name_by_hint="Stithy")
    _register("ident-hint", "ident-hint-key", agent_name_hint="Stithy")

    tokens = server._set_session_context("ident-hint-key")
    try:
        assert get_session_env("PLOBI_AGENT_ID") == "Stithy"
        assert get_session_env("PLOBI_SESSION_ID") == "ident-hint-key"
        # 记忆那颗键（以及 SOUL 那一跳）认的是同一个 ContextVar：
        # 不再是 "l1" ⇒ 读它自己那份 SOUL，不再借顶层秘书那份。
        assert resolve_agent_slug() != "l1"
        assert resolve_agent_slug() == "stithy"
    finally:
        server._clear_session_context(tokens)
        _drop("ident-hint")

    assert get_session_env("PLOBI_AGENT_ID") == ""


def test_context_falls_back_to_the_session_folder(identity_double, monkeypatch, tmp_path):
    """桌面正路不带名字 ⇒ 按会话目录归属认人（课程那些行本来就有 project_path）。"""
    from gateway.session_context import get_session_env
    from tools.memory_tool import resolve_agent_slug

    monkeypatch.delenv("PLOBI_AGENT_ID", raising=False)
    identity_double(name_by_path="agenda")
    folder = tmp_path / "agenda"
    folder.mkdir()
    _register("ident-cwd", "ident-cwd-key", cwd=str(folder), agent=_LiveAgent())

    tokens = server._set_session_context("ident-cwd-key")
    try:
        assert get_session_env("PLOBI_AGENT_ID") == "agenda"
        assert resolve_agent_slug() != "l1"
    finally:
        server._clear_session_context(tokens)
        _drop("ident-cwd")


def test_unresolvable_session_stays_conductor(identity_double, monkeypatch):
    """认不出就是 L1——不猜，也不把上一条会话的身份带进来。"""
    from gateway.session_context import get_session_env

    monkeypatch.delenv("PLOBI_AGENT_ID", raising=False)
    identity_double()
    _register("ident-none", "ident-none-key", cwd="")

    tokens = server._set_session_context("ident-none-key")
    try:
        assert get_session_env("PLOBI_AGENT_ID") == ""
    finally:
        server._clear_session_context(tokens)
        _drop("ident-none")


def test_ephemeral_task_without_a_session_does_not_invent_an_identity(
    identity_double, monkeypatch, tmp_path
):
    """后台/预览那些临时 id 不在 ``_sessions`` 里 ⇒ 身份空，cwd 仍按显式传入钉住。"""
    from agent.runtime_cwd import resolve_agent_cwd
    from gateway.session_context import get_session_env

    monkeypatch.delenv("PLOBI_AGENT_ID", raising=False)
    identity_double(name_by_path="agenda")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    tokens = server._set_session_context("bg-ephemeral", cwd=str(workspace))
    try:
        assert get_session_env("PLOBI_AGENT_ID") == ""
        assert resolve_agent_cwd() == workspace
    finally:
        server._clear_session_context(tokens)
