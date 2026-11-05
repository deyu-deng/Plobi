# -*- coding: utf-8 -*-
"""WP-L1-SCOPE-NOT-A-ROW —— L1 是总账里的一格（空串），不是"不圈"。

判据四件，全在这一个文件里：
  ① 一只口给出三个值：``''`` = L1 那一格、``None`` = 不圈（``all``／认不出）、名册 name = L2；
  ② 名册里的 L1 行（``role=l1_secretary``，slug 恰好是 ``default``）翻回**空串**，
     不再翻成 ``secretary``（那一翻就是 11-05「我和总秘书的对话又消失了」：按
     ``secretary`` 圈 = 0 行，按空串圈才是秘书那一格）；
  ③ 总账的过滤条件把 ``agent_id=''`` 当**真过滤**，不当"没传"；
  ④ 带 L1 作用域的"删空会话"只删秘书自己的——从前 ``or None`` 会跨着删所有 Agent 的。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from plobi.agents.registry import (
    AgentEntry,
    AgentRegistry,
    agent_id_for_scope,
    identity_name_in_entries,
)
from plobi_state import SessionDB

L1 = {"role": "l1_secretary", "profile": "default"}
EE = {"role": "l2_project", "profile": "l2-course-ee", "category": "courses"}
AGENDA = {"role": "l2_agenda", "profile": "l2-agenda"}


@pytest.fixture
def roster(tmp_path, monkeypatch):
    monkeypatch.delenv("PLOBI_PROJECTS_CONFIG", raising=False)
    monkeypatch.setattr("plobi.agents.registry.default_path", lambda: tmp_path / "projects.yaml")
    (tmp_path / "mind").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MIND_ROOT", str(tmp_path / "mind"))
    reg = AgentRegistry(path=tmp_path / "projects.yaml")
    for name, raw in (("secretary", L1), ("电工电子学", EE), ("agenda", AGENDA)):
        reg.upsert(AgentEntry.from_dict(name, raw))
    reg.save()
    return reg


def test_the_scope_key_gives_three_different_things(roster):
    """``''``／``None``／名册 name 是三件事，谁都不许并成一件。"""
    entries = roster.entries()
    # L1 的两种写法都回到**空串那一格**
    assert agent_id_for_scope("default", entries=entries) == ""
    assert agent_id_for_scope("secretary", entries=entries) == ""
    # 没带作用域 = 进程默认身份 = L1（§3.4），仍然不是"全局"
    assert agent_id_for_scope("", entries=entries) == ""
    assert agent_id_for_scope(None, entries=entries) == ""
    # 全局是另一个值
    assert agent_id_for_scope("all", entries=entries) is None
    assert agent_id_for_scope("no-such-row", entries=entries) is None
    # L2：名册 name 与旧家名两种写法都算，且**不许**折进 L1
    assert agent_id_for_scope("电工电子学", entries=entries) == "电工电子学"
    assert agent_id_for_scope("l2-course-ee", entries=entries) == "电工电子学"
    assert agent_id_for_scope("agenda", entries=entries) == "agenda"
    # 同一份判据：认不出的写法不许悄悄落到别人那一格
    assert identity_name_in_entries(entries, "secretary") == "secretary"


def test_the_ledger_treats_empty_agent_id_as_a_real_filter(tmp_path):
    """总账：``agent_id=''`` 只圈秘书那一格；``None`` 才是不圈。"""
    db = SessionDB(db_path=tmp_path / "state.db")
    stamp = 1_700_000_000.0
    for sid, agent_id, source, msgs in (
        ("s-l1-a", "", "desktop", 3),
        ("s-l1-b", "", "cli", 2),
        ("s-ee", "电工电子学", "desktop", 5),
        ("s-ag", "agenda", "desktop", 4),
    ):
        db.create_session(sid, source=source, agent_id=agent_id)
    conn = sqlite3.connect(str(tmp_path / "state.db"))
    for sid, msgs in (("s-l1-a", 3), ("s-l1-b", 2), ("s-ee", 5), ("s-ag", 4)):
        conn.execute("UPDATE sessions SET message_count = ?, ended_at = ? WHERE id = ?", (msgs, stamp, sid))
    conn.commit()
    conn.close()

    l1_rows = db.list_sessions_rich(limit=50, agent_id="")
    assert sorted(r["id"] for r in l1_rows) == ["s-l1-a", "s-l1-b"]
    assert {r["agent_id"] for r in l1_rows} == {""}

    both = db.list_sessions_rich(limit=50, agent_id=None)
    assert len(both) == 4
    assert db.session_count(agent_id="") == 2
    assert db.session_count(agent_id=None) == 4
    # 「点开这个 Agent 该看到哪一条」对 L1 也要答得出，不许退成"没有"
    assert db.latest_conversational_session_for_agent("") == "s-l1-a"
    assert db.latest_conversational_session_for_agent("电工电子学") == "s-ee"
    assert db.latest_conversational_session_for_agent(None) is None


def test_l1_scoped_sweep_of_empty_sessions_does_not_touch_other_agents(tmp_path):
    """带 L1 作用域的"删空会话"只删秘书的——这是那条 ``or None`` 最危险的形状。"""
    db = SessionDB(db_path=tmp_path / "state.db")
    stamp = 1_700_000_000.0
    for sid, agent_id in (("e-l1", ""), ("e-ee", "电工电子学"), ("e-ag", "agenda")):
        db.create_session(sid, source="cli", agent_id=agent_id)
    conn = sqlite3.connect(str(tmp_path / "state.db"))
    conn.execute("UPDATE sessions SET message_count = 0, ended_at = ? WHERE 1", (stamp,))
    conn.commit()
    conn.close()

    assert db.count_empty_sessions(agent_id="") == 1
    assert db.count_empty_sessions(agent_id=None) == 3
    assert db.delete_empty_sessions(agent_id="") == 1
    left = sorted(r["id"] for r in db.list_sessions_rich(limit=50))
    assert left == ["e-ag", "e-ee"], "别的 Agent 的空会话被 L1 那一刀带走了"


def test_console_scope_key_routes_l1_and_l2_to_their_own_rows(monkeypatch, tmp_path):
    """console 那一跳用的就是这只口：L1 → 空串那一格，L2 → 自己那一行。"""
    from plobi.console import router as console_router

    rows = None

    class _Reg:
        def entries(self):
            return rows

    monkeypatch.setattr(console_router, "get_registry", lambda: _Reg())
    reg = AgentRegistry(path=tmp_path / "projects.yaml")
    reg.upsert(AgentEntry.from_dict("secretary", L1))
    reg.upsert(AgentEntry.from_dict("电工电子学", EE))
    rows = reg.entries()

    assert console_router._agent_id_for_profile("default") == ""
    assert console_router._agent_id_for_profile("secretary") == ""
    assert console_router._agent_id_for_profile("电工电子学") == "电工电子学"
    assert console_router._agent_id_for_profile("l2-course-ee") == "电工电子学"
    assert console_router._agent_id_for_profile("all") is None
    assert console_router._agent_id_for_profile("no-such-row") is None
