# -*- coding: utf-8 -*-
"""身份判据＝**名册那一行**（裁 192），不是家目录。

家目录那条腿退役之后（裁 42／178 乙案：10-24 隔离搬走 23 具家，10-27 用户签字把隔离区
彻底删了），``plobi_cli.profiles.profile_exists()`` 对**每一个**真身份都返回 False。
凡是拿它当「这个身份在不在」来问的地方都会变成假阴性——最要命的是看板：每一条有
assignee 的任务都被判成「不可起」，派发整体瘫死（用户 10-27：「kanban 的能力要有」）。

这一档钉住新不变量：

① 名册上有那一行 ⇒ 那个身份算数（**哪怕家目录一个都没有**）；
② 名册上没有 ⇒ 不算（终端车道 ``orion-cc`` 这类仍然跳过，不许误起）；
③ 健康遥测看得见「有活可起」，不再把真活说成「闲着」；
④ 分解器喂给 LLM 的名册来自名册，不是目录扫描（扫描版现在恒空）。
"""

from pathlib import Path

import pytest

from plobi_cli import kanban_db as kb
from plobi_cli import profiles as profiles_mod


ROSTER_YAML = (
    "agents:\n"
    "  Aura:\n"
    "    role: l2_project\n"
    "    description: 名册上的那一行\n"
    "  agenda:\n"
    "    role: l2_agenda\n"
    "    description: 日程秘书\n"
)


@pytest.fixture()
def roster_home(tmp_path, monkeypatch):
    """隔离 PLOBI_HOME ＋ 一份**名册**；家目录一个都没有（这正是退役后的形状）。"""
    home = tmp_path / ".plobi"
    home.mkdir()
    (home / "profiles").mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("PLOBI_HOME", str(home))
    roster = home / "plobi" / "projects.yaml"
    roster.parent.mkdir(parents=True, exist_ok=True)
    roster.write_text(ROSTER_YAML, encoding="utf-8")
    monkeypatch.setenv("PLOBI_PROJECTS_CONFIG", str(roster))
    kb.init_db()
    return home


# ── 判据本身 ────────────────────────────────────────────────────────────

def test_roster_row_is_an_identity_without_any_home(roster_home):
    """名册上有那一行 ⇒ 身份算数；家目录不存在不算否定。"""
    assert profiles_mod.profile_exists("Aura") is False  # 家确实没有
    assert profiles_mod.identity_exists("Aura") is True  # 但名册上有
    assert profiles_mod.identity_exists("aura") is True  # profile_name 也认
    assert profiles_mod.identity_exists("agenda") is True


def test_non_roster_name_is_not_an_identity(roster_home):
    """名册上没有的名字不算身份（终端车道那类仍然要跳过）。"""
    assert profiles_mod.identity_exists("orion-cc") is False
    assert profiles_mod.identity_exists("no-such-agent") is False
    assert profiles_mod.identity_exists("default") is True  # 伪作用域照旧算数


def test_roster_listing_is_not_a_directory_scan(roster_home):
    """名册清单走名册，不走目录扫描（扫描版现在只会回 default 一行）。"""
    names = [a["name"] for a in profiles_mod.list_roster_agents()]
    assert "Aura" in names and "agenda" in names
    assert profiles_mod.list_profiles()  # 旧的那支仍能跑（它列的是家）


# ── 看板派发 ────────────────────────────────────────────────────────────

def test_kanban_dispatches_task_for_roster_row(roster_home):
    """名册上的 assignee 照常起进程——不许被当成「不可起」跳过。"""
    spawned = []

    def fake_spawn(task, workspace, board=None):
        spawned.append(task.id)
        return 42

    with kb.connect() as conn:
        t = kb.create_task(conn, title="轮到 Aura", assignee="Aura")
        res = kb.dispatch_once(conn, spawn_fn=fake_spawn)

    assert spawned == [t]
    assert res.skipped_nonspawnable == []


def test_kanban_still_skips_non_roster_lane(roster_home):
    """终端车道（不在名册上）仍然跳过——原判据的意图不许丢。"""
    with kb.connect() as conn:
        t = kb.create_task(conn, title="等人来拉", assignee="orion-cc")
        res = kb.dispatch_once(conn, spawn_fn=lambda *a, **k: 42)

    assert res.spawned == []
    assert res.skipped_nonspawnable == [t]


def test_health_telemetry_sees_spawnable_work(roster_home):
    """健康遥测看得见「有活可起」，不再把真活说成闲着。"""
    with kb.connect() as conn:
        kb.create_task(conn, title="等派", assignee="Aura")
        assert kb.has_spawnable_ready(conn) is True


def test_review_column_uses_the_same_identity_test(roster_home):
    """review 那一列同一口径。"""
    with kb.connect() as conn:
        t = kb.create_task(conn, title="待审", assignee="Aura")
        conn.execute("UPDATE tasks SET status = 'review' WHERE id = ?", (t,))
        assert kb.has_spawnable_review(conn) is True


# ── 分解器 ──────────────────────────────────────────────────────────────

def test_decomposer_roster_comes_from_the_registry(roster_home):
    """分解器喂给 LLM 的名册来自名册（目录扫描版现在恒空 ⇒ 路由无从谈起）。"""
    from plobi_cli import kanban_decompose

    roster, valid = kanban_decompose._build_roster()
    names = [entry["name"] for entry in roster]
    assert "Aura" in names
    assert "agenda" in names
    assert "Aura" in valid and "aura" in valid  # 名字与 slug 都算认得
