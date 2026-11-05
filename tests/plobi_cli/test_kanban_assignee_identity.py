# -*- coding: utf-8 -*-
"""看板那一格的键＝名册那一行（`WP-PROFILE-CANCEL` 第五轮）。

家退休之后这里断过两处，都是**功能**上的：

① 「可以指派给谁」从前是扫盘上的家（``<根>/profiles/<名>/``）。家没了 ⇒ 只剩
   ``default`` 一项，仪表盘的收件人下拉里新登记的 Agent 根本不出现在列（这台现读
   从 25 行掉到 1 项）。
② ``_canonical_assignee`` 走的是家名归一（小写、非法字符换 ``-``）。身份的写法被
   它改掉：这台 25 行里 **13 行**受影响（``Aura``→``aura``、``大学物理（甲）Ⅱ``→
   ``大学物理（甲）ⅱ``），于是同一块板上一个 Agent 有两种拼法，和改名那条链按
   名册 name 做的搬移对不上（现读 ``tasks.assignee`` 里还留着旧家名 ``l2-agenda``）。

判据：认得出那一行 ⇒ 键就是那一行的 ``name``（原样）；认不出 ⇒ 保持旧归一
（terminal lane、人类自取的收件人不改行为）。可指派名单问名册，不问盘。
"""

from __future__ import annotations

import pytest

from plobi.agents.registry import AgentEntry, AgentRegistry
from plobi_cli import kanban_db as kb

ROSTER = (
    ("Aura", {"role": "l2_project", "profile": "aura", "category": "projects"}),
    ("大学物理（甲）Ⅱ", {"role": "l2_project", "profile": "l2-course-physics-a2", "category": "courses"}),
    ("agenda", {"role": "l2_agenda", "profile": "l2-agenda"}),
)


@pytest.fixture()
def roster_home(tmp_path, monkeypatch):
    """一份只有名册、**盘上没有家**的隔离环境（家退休后的真实形状）。"""
    roster = tmp_path / "projects.yaml"
    monkeypatch.delenv("PLOBI_PROJECTS_CONFIG", raising=False)
    monkeypatch.setattr("plobi.agents.registry.default_path", lambda: roster)
    (tmp_path / "mind").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MIND_ROOT", str(tmp_path / "mind"))
    reg = AgentRegistry(path=roster)
    for name, raw in ROSTER:
        reg.upsert(AgentEntry.from_dict(name, raw))
    reg.save()
    # 家目录这条腿已经退休：让它恒为空，测的就是"只靠名册也要能派工"
    monkeypatch.setattr(kb, "list_profiles_on_disk", lambda: [])
    return reg


def test_the_assignee_key_is_the_roster_name_verbatim(roster_home):
    """名册认得出的写法一律回到那一行的 ``name``，**大小写与全角都留着**。"""
    assert kb._canonical_assignee("Aura") == "Aura"
    assert kb._canonical_assignee("aura") == "Aura"          # 旧家名的拼法也认
    assert kb._canonical_assignee("大学物理（甲）Ⅱ") == "大学物理（甲）Ⅱ"
    assert kb._canonical_assignee("l2-course-physics-a2") == "大学物理（甲）Ⅱ"
    assert kb._canonical_assignee("l2-agenda") == "agenda"
    # 认不出的（terminal lane／人类自取的收件人）不改行为：仍走旧的归一
    assert kb._canonical_assignee("Researcher") == "researcher"
    assert kb._canonical_assignee(None) is None
    assert kb._canonical_assignee("   ") is None


def test_assignable_agents_come_from_the_roster_not_from_disk(roster_home):
    """判据①：可指派名单＝名册那些行；扫盘只剩一项也不能把名单缩成一项。"""
    names = kb.list_assignable_agents()

    assert {"Aura", "大学物理（甲）Ⅱ", "agenda"} <= set(names)
    assert "default" in names          # L1 那一份存储仍在（老形状兼容）
    assert len(names) >= 3             # 盘上 0 具家的时候，从前这里只剩 1 项


def test_known_assignees_lists_a_fresh_agent_before_any_task(roster_home, tmp_path):
    """判据①的另一半：一张卡都还没给过的新 Agent，也要在收件人名单里。"""
    conn = kb.connect(tmp_path / "kanban.db")
    try:
        rows = {entry["name"]: entry for entry in kb.known_assignees(conn)}
    finally:
        conn.close()

    assert "Aura" in rows and rows["Aura"]["in_roster"] is True
    assert rows["Aura"]["on_disk"] is False      # 家退休了，这一列只说明旧家还在不在
    assert rows["Aura"]["counts"] == {}          # 没派过活也必须出现在名单里
