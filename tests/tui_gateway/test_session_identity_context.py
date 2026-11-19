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

这一档钉三件事：上下文里能带身份；身份从会话怎么认（含缓存）；**上下文这一档
自己不去读名册**（resume/后台那些路径的测试把 ``plobi_constants`` 整个 mock 掉，
在那一层读名册会在 yaml 解析里打转 —— 11-19 现读，test_protocol 的 resume
那两个线程就卡在这）。
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


def test_context_carries_the_agent_id_it_is_given(monkeypatch):
    """给了身份就落进 ContextVar——记忆那颗键与 SOUL 那一跳都认这一格。"""
    from gateway.session_context import get_session_env
    from tools.memory_tool import resolve_agent_slug

    monkeypatch.delenv("PLOBI_AGENT_ID", raising=False)
    _register("ident-carry", "ident-carry-key")

    tokens = server._set_session_context("ident-carry-key", agent_id="Stithy")
    try:
        assert get_session_env("PLOBI_AGENT_ID") == "Stithy"
        # 会话 id 与 session_key 同源：memory_tool 那条"会话行反推"的兜底才走得通
        assert get_session_env("PLOBI_SESSION_ID") == "ident-carry-key"
        assert resolve_agent_slug() != "l1"
        assert resolve_agent_slug() == "stithy"
    finally:
        server._clear_session_context(tokens)
        _drop("ident-carry")

    assert get_session_env("PLOBI_AGENT_ID") == ""


def test_session_identity_prefers_the_explicit_roster_name(identity_double, monkeypatch):
    """前端显式带名册名（``agent_name_hint``）⇒ 会话归那个 Agent。"""
    monkeypatch.delenv("PLOBI_AGENT_ID", raising=False)
    identity_double(name_by_hint="Stithy")
    session = _register("ident-hint", "ident-hint-key", agent_name_hint="Stithy")

    try:
        assert server._session_agent_id(session) == "Stithy"
    finally:
        _drop("ident-hint")


def test_session_identity_falls_back_to_the_session_folder(
    identity_double, monkeypatch, tmp_path
):
    """桌面正路不带名字 ⇒ 按会话目录归属认人（课程那些行本来就有 project_path）。"""
    monkeypatch.delenv("PLOBI_AGENT_ID", raising=False)
    identity_double(name_by_path="agenda")
    folder = tmp_path / "agenda"
    folder.mkdir()
    session = _register("ident-cwd", "ident-cwd-key", cwd=str(folder), agent=_LiveAgent())

    try:
        assert server._session_agent_id(session) == "agenda"
    finally:
        _drop("ident-cwd")


def test_session_identity_is_cached_and_self_invalidating(identity_double, monkeypatch):
    """同一个会话第二次问不再读名册；名字提示晚到 ⇒ 键变了自动重算。"""
    import plobi.mind.identity as identity

    monkeypatch.delenv("PLOBI_AGENT_ID", raising=False)
    identity_double()
    calls: list[str] = []

    def counting_resolve(path):
        calls.append(str(path))
        return "agenda" if "agenda" in str(path) else ""

    monkeypatch.setattr(identity, "resolve_agent_for_path", counting_resolve, raising=False)
    session = _register("ident-cache", "ident-cache-key", cwd="/x/agenda")

    try:
        assert server._session_agent_id(session) == "agenda"
        assert len(calls) == 1
        assert server._session_agent_id(session) == "agenda"
        assert len(calls) == 1, "第二次必须走缓存"

        # 名册名提示后来才到（prompt.submit 那一跳）⇒ 缓存键变了，重新认
        monkeypatch.setattr(
            identity, "resolve_agent_name", lambda value: "Stithy", raising=False
        )
        session["agent_name_hint"] = "Stithy"
        assert server._session_agent_id(session) == "Stithy"
    finally:
        _drop("ident-cache")


def test_unresolvable_session_stays_conductor(identity_double, monkeypatch):
    """认不出就是 L1——不猜，也不把上一条会话的身份带进来。"""
    from gateway.session_context import get_session_env

    monkeypatch.delenv("PLOBI_AGENT_ID", raising=False)
    identity_double()
    session = _register("ident-none", "ident-none-key", cwd="")

    tokens = server._set_session_context(
        "ident-none-key", agent_id=server._session_agent_id(session)
    )
    try:
        assert get_session_env("PLOBI_AGENT_ID") == ""
    finally:
        server._clear_session_context(tokens)
        _drop("ident-none")


def test_agent_id_from_row_only_trusts_a_real_string():
    """行上的 ``agent_id`` 只认真字符串；Mock/None/缺列一律空（= L1）。"""
    from unittest.mock import MagicMock

    assert server._agent_id_from_row({"agent_id": " Stithy "}) == "Stithy"
    assert server._agent_id_from_row({"agent_id": ""}) == ""
    assert server._agent_id_from_row({"agent_id": None}) == ""
    assert server._agent_id_from_row({}) == ""
    assert server._agent_id_from_row(None) == ""
    assert server._agent_id_from_row(MagicMock()) == "", "整档被 mock 掉时不许把 Mock 当名字"


def test_setting_the_context_does_not_read_the_roster(identity_double, monkeypatch):
    """上下文这一档自己不去读名册：resume/后台那几条路把它当纯搬运工。"""
    import plobi.mind.identity as identity

    monkeypatch.delenv("PLOBI_AGENT_ID", raising=False)
    touched: list[str] = []
    monkeypatch.setattr(
        identity,
        "resolve_agent_for_path",
        lambda path: touched.append("path") or "agenda",
        raising=False,
    )
    monkeypatch.setattr(
        identity,
        "resolve_agent_name",
        lambda value: touched.append("name") or "agenda",
        raising=False,
    )
    _register("ident-norooster", "ident-norooster-key", cwd="/x/agenda")

    tokens = server._set_session_context("ident-norooster-key")
    try:
        assert touched == [], "不带 agent_id 时不该去认身份"
    finally:
        server._clear_session_context(tokens)
        _drop("ident-norooster")


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
