"""首装干净（裁定 211 · WP-FIRST-RUN-CLEAN）。

产品的形状：**装上就有一个 L1，零 L2，零定时任务**。能力来自**工具**
（`agenda_read`／`agenda_write` 早就在 `_PLOBI_CORE_TOOLS` 里，所有角色都有），
不来自多造一个角色；角色只有两种人能建——用户（控制台／CLI）与 L1（按需，另一刀）。

这一档钉的是**四件事**：

1. **启动路径不写任何 Agent 行**——空家起一次后端，名册里零行（原来会多一行
   `agenda`，那是产品替用户造的 L2）。
2. **启动路径不写任何定时任务**——家里没有作业。
3. **归档 = 退役，不占槽**——退役的那条日程角色不再算「已经有一个了」，
   于是用户归档之后**自己**还能再建一条（旧形状会返 409「agenda L2 already exists」，
   撞的是一条已经退役的行）。
4. **要回来的时候是复活，不是另建一条**——旧形状会另建一条 `secretary-agenda`，
   于是名册里两行都显示「日程秘书」（左栏短名表两个 id 映射到同一个词），
   用户看着像「又冒出来一个」。
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from plobi.agents.registry import (
    AgentEntry,
    AgentRegistry,
    find_agenda_agent,
    register_agenda_agent,
)


def _agenda_entry(name: str = "agenda") -> AgentEntry:
    return AgentEntry(
        name=name,
        role="l2_agenda",
        mind_subtree="Surface/dossiers/projects/Plobi",
        skills=("plobi-l2-resident",),
        description="日程采集 → SQLite → 看板 → 钉钉 闭环",
    )


# --------------------------------------------------------------------------- #
# 1 + 2：启动路径不替用户写任何东西
# --------------------------------------------------------------------------- #


def test_boot_writes_no_agent_row_into_the_roster(tmp_path, monkeypatch):
    """起一次桌面后端，空家 ⇒ 名册零行。

    旧形状：lifespan startup 里那句 ``_seed_agenda_agent()`` 无条件往
    ``$PLOBI_HOME/plobi/projects.yaml`` 写一行 ``agenda`` ⇒ 每个新用户都收到
    一条自己没要的 L2。
    """
    import plobi.agents.registry as registry_mod
    from plobi_cli import web_server

    roster = tmp_path / "plobi" / "projects.yaml"
    monkeypatch.setattr(registry_mod, "default_path", lambda: roster)

    with TestClient(
        web_server.app,
        headers={web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN},
    ):
        pass  # lifespan startup 跑完了

    if roster.exists():
        text = roster.read_text(encoding="utf-8")
        # 名册要么不存在，要么一个 Agent 行都没有
        assert "role:" not in text and "l2_agenda" not in text, text
    # 首装连名册文件都不该有
    assert not roster.exists() or "agents:" not in roster.read_text(encoding="utf-8")


def test_boot_writes_no_cron_job(tmp_path, monkeypatch):
    """家里一条定时任务都没有——用户自己设（裁定 211 判据二）。"""
    import os

    home = tmp_path / "home"
    (home / "cron").mkdir(parents=True)
    monkeypatch.setenv("PLOBI_HOME", str(home))

    import plobi_cli.web_server as web_server

    with TestClient(
        web_server.app,
        headers={web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN},
    ):
        pass

    jobs_file = home / "cron" / "jobs.json"
    if jobs_file.exists():
        payload = json.loads(jobs_file.read_text(encoding="utf-8"))
        jobs = payload.get("jobs", []) if isinstance(payload, dict) else payload
        assert not jobs, jobs
    assert os.environ["PLOBI_HOME"] == str(home)


# --------------------------------------------------------------------------- #
# 3：归档 = 退役，不占槽
# --------------------------------------------------------------------------- #


def test_archived_agenda_row_does_not_hold_the_slot(tmp_path):
    """退役的那条不再算「已经有一个日程角色了」。

    否则用户归档之后连**自己**再建一条都建不成（``POST /api/agents`` 返 409
    「agenda L2 already exists」，撞的是一条已经退役的行）。
    """
    registry = AgentRegistry(path=tmp_path / "projects.yaml")
    registry.upsert(_agenda_entry())
    registry.save()
    assert find_agenda_agent(registry) is not None

    registry.dissolve("agenda")
    registry.save()

    assert find_agenda_agent(registry) is None


# --------------------------------------------------------------------------- #
# 4：要回来的时候是复活，不是另建一条
# --------------------------------------------------------------------------- #


def test_register_revives_the_retired_row_instead_of_adding_a_second_one(tmp_path):
    """复活同一行，而不是另建一条 ``secretary-agenda``。

    两个 id 在左栏都映射到「日程秘书」这个显示名 ⇒ 另建一条等于用户看着
    「又冒出来一个日程秘书」。
    """
    registry = AgentRegistry(path=tmp_path / "projects.yaml")
    registry.upsert(_agenda_entry())
    registry.save()
    registry.dissolve("agenda")
    registry.save()

    entry, spawned = register_agenda_agent(registry)

    assert entry.name == "agenda", entry.name
    assert entry.archived is False
    assert spawned is False
    assert "secretary-agenda" not in registry.agents
    assert len(registry.entries()) == 1, [e.name for e in registry.entries()]
