# -*- coding: utf-8 -*-
"""delete／export／rename 三个动作改成操作**名册那一行**（用户 10-27 拍板）。

家目录退休之后（裁 42／178 乙案），这三个动作的旧对象（``profiles/<name>/``）已经不存在，
于是它们要么必然失败、要么悄悄对错的东西动手。改名册语义之后：

* ``delete`` → 那一行从名册里去掉；它的身份文件与它那一份排程存储挪进
  ``.deleted-agents/<名字>-<时刻>/``，**留一份底**（名册文件不在任何 git 仓里）；
* ``export`` → 导出那一行（yaml）＋ 它的身份文件，成一个 tar.gz；
* ``rename`` → 改那一行的键（名册 name ＝ 身份），连同身份文件、排程存储、
  总账里那些会话的 ``agent_id`` 一起改。
"""

from pathlib import Path

import pytest

from plobi_cli import profiles as profiles_mod


ROSTER_YAML = (
    "agents:\n"
    "  Aura:\n"
    "    role: l2_project\n"
    "    profile: aura\n"
    "    description: 名册上的那一行\n"
    "  agenda:\n"
    "    role: l2_agenda\n"
    "    profile: l2-agenda\n"
    "    description: 日程秘书\n"
)


@pytest.fixture()
def roster_home(tmp_path, monkeypatch):
    """隔离 PLOBI_HOME ＋ 一份名册 ＋ 一本会话总账。"""
    home = tmp_path / ".plobi"
    (home / "plobi").mkdir(parents=True)
    (home / "profiles").mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("PLOBI_HOME", str(home))
    monkeypatch.delenv("PLOBI_STATE_HOME", raising=False)
    roster = home / "plobi" / "projects.yaml"
    roster.write_text(ROSTER_YAML, encoding="utf-8")
    monkeypatch.setenv("PLOBI_PROJECTS_CONFIG", str(roster))

    from plobi_state import SessionDB

    db = SessionDB()
    db.create_session("sess-1", "cli", agent_id="Aura")
    db.close()
    return home


def _roster_names():
    return [a["name"] for a in profiles_mod.list_roster_agents()]


# ── delete ─────────────────────────────────────────────────────────────

def test_delete_removes_the_roster_row_and_keeps_a_copy(roster_home):
    """删＝那一行从名册里去掉；身份文件与排程存储挪进留底目录。"""
    from plobi.agents.registry import shared_soul_path_for

    soul = shared_soul_path_for("Aura")
    soul.parent.mkdir(parents=True, exist_ok=True)
    soul.write_text("我是 Aura", encoding="utf-8")
    store = roster_home / "cron" / "agents" / "Aura"
    store.mkdir(parents=True, exist_ok=True)
    (store / "jobs.json").write_text("[]\n", encoding="utf-8")

    backup = profiles_mod.delete_identity("Aura", yes=True)

    assert "Aura" not in _roster_names()
    assert backup.is_dir()
    assert (backup / "agent.yaml").is_file()
    assert (backup / "Aura.md").is_file()   # 身份文件按名册 name 命名
    assert (backup / "cron" / "jobs.json").is_file()
    assert not soul.exists()
    assert not store.exists()
    # 留底里那一行的内容还在
    assert "Aura" in (backup / "agent.yaml").read_text(encoding="utf-8")


def test_delete_of_unknown_name_raises(roster_home):
    """名册上没有的名字 ⇒ 404 那句话（不是静默成功）。"""
    with pytest.raises(FileNotFoundError):
        profiles_mod.delete_identity("no-such-agent", yes=True)


def test_delete_refuses_default(roster_home):
    """default 是安装本身，不许删。"""
    with pytest.raises(ValueError):
        profiles_mod.delete_identity("default", yes=True)


# ── export ─────────────────────────────────────────────────────────────

def test_export_contains_the_row_and_the_identity_file(roster_home, tmp_path):
    """导出的归档里有那一行的 yaml ＋ 它的身份文件。"""
    import tarfile

    from plobi.agents.registry import shared_soul_path_for

    soul = shared_soul_path_for("Aura")
    soul.parent.mkdir(parents=True, exist_ok=True)
    soul.write_text("我是 Aura", encoding="utf-8")

    out = tmp_path / "aura-export.tar.gz"
    result = profiles_mod.export_identity("Aura", str(out))
    assert result.is_file()

    with tarfile.open(result, "r:gz") as tf:
        names = tf.getnames()
    assert any(n.endswith("agent.yaml") for n in names)
    assert any(n.endswith("Aura.md") for n in names)


# ── rename ─────────────────────────────────────────────────────────────

def test_rename_rekeys_the_row_and_moves_its_artifacts(roster_home):
    """改名＝改那一行的键；身份文件、排程存储、总账里的归属键一起跟。"""
    from plobi.agents.registry import shared_soul_path_for
    from plobi_state import SessionDB

    soul = shared_soul_path_for("Aura")
    soul.parent.mkdir(parents=True, exist_ok=True)
    soul.write_text("我是 Aura", encoding="utf-8")
    store = roster_home / "cron" / "agents" / "Aura"
    store.mkdir(parents=True, exist_ok=True)
    (store / "jobs.json").write_text("[]\n", encoding="utf-8")

    profiles_mod.rename_identity("Aura", "AuraRenamed")

    assert "AuraRenamed" in _roster_names()
    assert "Aura" not in _roster_names()
    assert (roster_home / "souls" / "AuraRenamed.md").is_file()
    assert not (roster_home / "souls" / "Aura.md").exists()
    assert (roster_home / "cron" / "agents" / "AuraRenamed" / "jobs.json").is_file()
    assert not (roster_home / "cron" / "agents" / "Aura").exists()

    # 总账里那些会话的归属键也跟着改（否则这一行的会话历史会掉队）
    db = SessionDB()
    try:
        rows = db.list_sessions_rich(limit=10)
        ids = [r.get("agent_id") for r in rows]
    finally:
        db.close()
    assert "AuraRenamed" in ids
    assert "Aura" not in ids


def test_rename_refuses_a_case_twin(roster_home):
    """只差大小写的名字不许新建（R-071）。"""
    with pytest.raises(ValueError):
        profiles_mod.rename_identity("Aura", "agenda")


def test_rename_of_unknown_name_raises(roster_home):
    with pytest.raises(FileNotFoundError):
        profiles_mod.rename_identity("no-such-agent", "whatever")
