"""桌面那条腿要认名册行的工具开关（2026-11-02）。

用户要把 L2 从"自己干活"改成"派活"，办法是去 Capacity 里关它的工具。那个开关
写的是**名册行的** ``disabled_toolsets``（``PUT /api/agents/:id/tool-prefs``），
而 ``_create_agent`` 以前只读全局 ``platform_toolsets.api_server``——关了等于没关，
屏上翻绿、下一轮 L2 手上还是全套工具。

这一档钉四件事：
1. 名册行关掉的工具集真的从这一轮的工具集里减掉；
2. 没关的一件不动（只做减法，不做排序/重排）；
3. 会话没归属 / 名册读不到 / 名字对不上 ⇒ 原样放行，不许把会话连工具一起搞死；
4. 专家（子智能体）不吃父的这一刀——关 L2 的 terminal 不该把「电脑」专家也废掉。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from gateway.platforms.api_server import APIServerAdapter


class _FakeSessionDB:
    def __init__(self, row):
        self._row = row

    def get_session(self, _session_id):
        return self._row


def _stub(row):
    """只给这一条方法要用的那一个依赖。"""
    return SimpleNamespace(_ensure_session_db=lambda: _FakeSessionDB(row))


def _entry(disabled):
    return SimpleNamespace(name="Sindri", disabled_toolsets=tuple(disabled))


def test_disabled_toolsets_are_subtracted(monkeypatch):
    import plobi.agents.registry as registry

    monkeypatch.setattr(
        registry, "load_registry", lambda: SimpleNamespace(get=lambda _n: _entry(["terminal"]))
    )

    kept = APIServerAdapter._apply_agent_toolset_prefs(
        _stub({"agent_id": "Sindri"}),
        ["delegation", "offload", "terminal", "web"],
        "sess-1",
    )

    assert kept == ["delegation", "offload", "web"]


def test_matching_is_case_insensitive(monkeypatch):
    import plobi.agents.registry as registry

    monkeypatch.setattr(
        registry, "load_registry", lambda: SimpleNamespace(get=lambda _n: _entry(["Terminal"]))
    )

    kept = APIServerAdapter._apply_agent_toolset_prefs(
        _stub({"agent_id": "Sindri"}), ["terminal", "web"], "sess-1"
    )

    assert kept == ["web"]


def test_no_disabled_list_leaves_toolsets_untouched(monkeypatch):
    import plobi.agents.registry as registry

    monkeypatch.setattr(
        registry, "load_registry", lambda: SimpleNamespace(get=lambda _n: _entry([]))
    )

    given = ["web", "delegation", "terminal"]
    assert APIServerAdapter._apply_agent_toolset_prefs(
        _stub({"agent_id": "Sindri"}), given, "sess-1"
    ) == given


@pytest.mark.parametrize(
    "row",
    [
        None,                     # 会话读不到
        {},                       # 没有 agent_id 列
        {"agent_id": ""},         # 未归属（L1）
    ],
)
def test_session_without_agent_passes_through(monkeypatch, row):
    given = ["web", "terminal"]
    assert APIServerAdapter._apply_agent_toolset_prefs(_stub(row), given, "sess-1") == given


def test_unknown_agent_passes_through(monkeypatch):
    import plobi.agents.registry as registry

    monkeypatch.setattr(registry, "load_registry", lambda: SimpleNamespace(get=lambda _n: None))

    given = ["web", "terminal"]
    assert APIServerAdapter._apply_agent_toolset_prefs(
        _stub({"agent_id": "Ghost"}), given, "sess-1"
    ) == given


def test_missing_session_id_passes_through():
    given = ["web", "terminal"]
    assert APIServerAdapter._apply_agent_toolset_prefs(_stub({"agent_id": "Sindri"}), given, None) == given


def test_registry_failure_passes_through(monkeypatch):
    import plobi.agents.registry as registry

    def _boom():
        raise RuntimeError("registry unreadable")

    monkeypatch.setattr(registry, "load_registry", _boom)

    given = ["web", "terminal"]
    assert APIServerAdapter._apply_agent_toolset_prefs(
        _stub({"agent_id": "Sindri"}), given, "sess-1"
    ) == given


def test_experts_do_not_inherit_the_parent_cut():
    """L2 关掉 terminal 之后，「电脑」专家还得能跑命令。

    ``delegate_tool`` 给子智能体的是 ``expert_roles`` 里那一层工具面，从不把父的
    ``disabled_toolsets`` 递下去——这条钉住那个契约，免得以后有人"顺手"让它继承。
    """
    import inspect

    from tools import delegate_tool

    source = inspect.getsource(delegate_tool._build_child_agent)

    assert "disabled_toolsets" not in source
