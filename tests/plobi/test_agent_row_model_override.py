"""`GET /api/agents` 那一行到底带不带「这行有没有覆盖」的标记（WP-AGENT-MODEL-PICKER-MOUNT 第一档）。

判据不是"这个键存在"，而是那件**事实**：两行解析到同一个生效模型时，界面必须还分得出
「跟随默认」与「显式选了同一个模型」。在此之前桌面拿不到这个区分，只能猜——而猜出来的
「跟随默认」是个会撒谎的默认态（裁定 166 决定二 / 裁定 172）。

DB、用量、可点性全部走桩：这一档不碰真 `PLOBI_HOME`，也不碰任何人的家目录。
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from plobi.agents.registry import AgentEntry
from plobi.console import router

EFFECTIVE = "deepseek/deepseek-chat"


@pytest.fixture
def stub_reads(monkeypatch):
    """把 agent_row 里那些「读侧其实会碰库」的调用全部按桩接掉。"""

    def _stub(effective: str | None = EFFECTIVE):
        monkeypatch.setattr(router, "totals_for_agent", lambda agent_id, **kw: SimpleNamespace(calls=3))
        monkeypatch.setattr(router, "sessions_for_agent", lambda agent_id: [])
        monkeypatch.setattr(router, "leg_for_sessions", lambda sessions, **kw: None)
        monkeypatch.setattr(router, "status_for", lambda agent_id: "idle")
        monkeypatch.setattr(router, "model_for", lambda entry: effective)
        monkeypatch.setattr(router, "agent_askability", lambda entry: (True, ""))
        monkeypatch.setattr(router, "agent_navigability", lambda entry: (True, ""))

    return _stub


def _row(entry: AgentEntry) -> dict:
    return router.agent_row(entry)


def test_following_default_carries_the_flag_false(stub_reads):
    stub_reads()

    row = _row(AgentEntry(name="aura", role="l2_project"))

    assert row["modelOverride"] is False, "没带覆盖的行必须说'没带'，不能缺键"
    assert "modelOverride" in row


def test_row_override_carries_the_flag_true(stub_reads):
    stub_reads()

    overridden = replace(AgentEntry(name="aura", role="l2_project"), provider="deepseek", model="deepseek-chat")

    assert _row(overridden)["modelOverride"] is True


def test_same_effective_model_is_still_distinguishable(stub_reads):
    """核心不变量：生效值相同时，两行必须还能分开——这正是拿 `model` 猜会输的地方。"""
    stub_reads(effective=EFFECTIVE)

    following = _row(AgentEntry(name="aura", role="l2_project"))
    explicit = _row(replace(AgentEntry(name="kit", role="l2_project"), provider="deepseek", model="deepseek-chat"))

    assert following["model"] == explicit["model"] == EFFECTIVE
    assert following["modelOverride"] is False
    assert explicit["modelOverride"] is True


def test_provider_only_row_counts_as_an_override(stub_reads):
    """`has_model_override` 是 provider 或 model 任一非空：只写 provider 也是显式选择。"""
    stub_reads()

    row = _row(replace(AgentEntry(name="prism", role="l2_project"), provider="openrouter"))

    assert row["modelOverride"] is True


def test_flag_survives_the_none_stripping(stub_reads):
    """行末那道「剥掉 None」的清洗不许把这个布尔一起剥了——缺键＝没读数，不等于没覆盖。"""
    stub_reads(effective=None)

    row = _row(AgentEntry(name="sindri", role="l2_project"))

    assert "model" not in row, "生效值读不到时那个键该被剥掉（既有契约）"
    assert row["modelOverride"] is False, "但覆盖标记恒在：它是名册那行的事实，不依赖路由解析"
