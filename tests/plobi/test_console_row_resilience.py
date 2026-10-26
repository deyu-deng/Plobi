"""`/api/agents` 某一行读不到库时，名单照出、那一格降级——别整单 500 让左栏"一个 Agent 都没有"。

事故形状（2026-10-24 23:2x 用户看到）：左栏「智能体」整段空，聊天顶上弹
「恢复失败 resume failed: database is locked」。真因只是某一行去 `state.db` 读
「今天打了几次卡」时撞上锁；前端把失败画成了空。

DB、用量、可点性全部走桩：这一档不碰真 `PLOBI_HOME`。
"""

from __future__ import annotations

import sqlite3

import pytest

from plobi.agents.registry import AgentEntry
from plobi.console import router


@pytest.fixture
def stub_common(monkeypatch):
    monkeypatch.setattr(router, "status_for", lambda agent_id: "idle")
    monkeypatch.setattr(router, "model_for", lambda entry: None)
    monkeypatch.setattr(router, "agent_askability", lambda entry: (True, ""))
    monkeypatch.setattr(router, "agent_navigability", lambda entry: (True, ""))
    monkeypatch.setattr(router, "leg_for_sessions", lambda sessions, **kw: None)


def _locked(*_a, **_kw):
    raise sqlite3.OperationalError("database is locked")


def test_a_locked_db_degrades_one_row_instead_of_the_whole_list(stub_common, monkeypatch):
    monkeypatch.setattr(router, "totals_for_agent", _locked)
    monkeypatch.setattr(router, "sessions_for_agent", _locked)

    row = router.agent_row(AgentEntry(name="aura", role="l2_project"))

    assert row["id"] == "aura", "这一行必须还在——锁的是读数，不是这个 Agent"
    assert row["todayCalls"] == 0, "读不到就按 0 说，别让整张单子 500"
    assert row["profile"] == "aura"


def test_a_non_lock_sql_error_still_raises(stub_common, monkeypatch):
    """只吞 locked/busy：真的 schema 漂移必须照抛，不能被这条兜底藏起来。"""

    def _broken(*_a, **_kw):
        raise sqlite3.OperationalError("no such column: nope")

    monkeypatch.setattr(router, "totals_for_agent", _broken)
    monkeypatch.setattr(router, "sessions_for_agent", lambda agent_id: [])

    with pytest.raises(sqlite3.OperationalError):
        router.agent_row(AgentEntry(name="aura", role="l2_project"))
