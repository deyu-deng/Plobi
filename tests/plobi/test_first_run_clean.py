"""首装干净（裁定 211 · WP-FIRST-RUN-CLEAN）。

产品的形状：**装上就有一个 L1，零 L2，零定时任务**。能力来自**工具**
（`agenda_read`／`agenda_write` 早就在 `_PLOBI_CORE_TOOLS` 里，所有角色都有），
不来自多造一个角色；角色只有两种人能建——用户（控制台／CLI）与 L1（按需，另一刀）。

这一档钉的是**两件事**：
1. **启动路径不写任何 Agent 行**——空家起一次后端，名册里零行（原来会多一行
   `agenda`，那是产品替用户造的 L2）。
2. **启动路径不写任何定时任务**——家里没有作业。

2026-11-15：原来还钉着「归档＝退役、不占槽」与「要回来是复活不是另建一条」两条，
那是日程秘书单例（`role: l2_agenda` + `find_agenda_agent` / `register_agenda_agent`
那条特权链）的形状。角色不再有「坑位」这回事——同类两行就是两行，只有重名才拒
（`registration_blocker`），所以这两条连同它们的 `_agenda_entry` 助手一起退场。
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

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

    with TestClient(web_server.app, headers={web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN}):
        pass

    if roster.exists():
        text = roster.read_text(encoding="utf-8")
        assert "role:" not in text
    assert not roster.exists() or "agents:" not in roster.read_text(encoding="utf-8")


def test_boot_writes_no_cron_job(tmp_path, monkeypatch):
    import os

    home = tmp_path / "home"
    (home / "cron").mkdir(parents=True)
    monkeypatch.setenv("PLOBI_HOME", str(home))

    import plobi_cli.web_server as web_server

    with TestClient(web_server.app, headers={web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN}):
        pass

    jobs_file = home / "cron" / "jobs.json"
    if jobs_file.exists():
        payload = json.loads(jobs_file.read_text(encoding="utf-8"))
        jobs = payload.get("jobs", []) if isinstance(payload, dict) else payload
        assert not jobs, jobs
    assert os.environ["PLOBI_HOME"] == str(home)
