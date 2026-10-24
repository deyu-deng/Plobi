"""vault 档案的 HTTP 面：两条路由的信封与状态码（WP-VAULT-AGENTS-VIEW）。

直接调路由函数（不经过 ASGI），把"空表 / 读失败 / 名字不在名册"三种答案分开钉住。
"""

from __future__ import annotations

import asyncio
import json
import textwrap

import pytest

from plobi.mind import agent_dossier as ad
from plobi.mind import vault_api

ROSTER = textwrap.dedent(
    """
    version: 1
    agents:
      Aura:
        role: l2_project
        category: projects
        mind_subtree: Vault/projects/Aura
        project_path: D:/projects/Aura
      电工电子学:
        role: l2_course
        category: courses
    """
).lstrip()


@pytest.fixture()
def live(monkeypatch, tmp_path):
    mind = tmp_path / "mind"
    vault = tmp_path / "vault"
    (mind / "wiki").mkdir(parents=True)
    (vault / "projects" / "Aura").mkdir(parents=True)
    (vault / "projects" / "Aura" / "plan.md").write_text("# Aura\n\n目标。\n", encoding="utf-8")
    (tmp_path / "projects.yaml").write_text(ROSTER, encoding="utf-8")
    monkeypatch.setenv("MIND_ROOT", str(mind))
    monkeypatch.setenv("VAULT_ROOT", str(vault))
    monkeypatch.setenv("PLOBI_PROJECTS_CONFIG", str(tmp_path / "projects.yaml"))
    return tmp_path


def test_list_route_returns_every_agent(live):
    response = asyncio.run(vault_api.list_vault_agents())
    body = response if isinstance(response, dict) else json.loads(response.body)
    assert body["ok"] is True
    assert {row["id"] for row in body["data"]["agents"]} == {"Aura", "电工电子学"}
    assert body["data"]["totals"] == {"agents": 2, "withHome": 1, "withoutHome": 1}


def test_detail_route_returns_bodies(live):
    response = asyncio.run(vault_api.get_vault_agent("Aura"))
    body = response if isinstance(response, dict) else json.loads(response.body)
    assert body["ok"] is True
    assert "目标。" in body["data"]["docs"][0]["text"]


def test_unknown_agent_is_404_not_an_empty_page(live):
    response = asyncio.run(vault_api.get_vault_agent("不存在"))
    assert response.status_code == 404
    assert json.loads(response.body)["ok"] is False


def test_roster_failure_is_503(monkeypatch, tmp_path):
    monkeypatch.setenv("PLOBI_PROJECTS_CONFIG", str(tmp_path / "missing.yaml"))
    monkeypatch.setattr(
        ad, "build_index", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("root gone"))
    )
    response = asyncio.run(vault_api.list_vault_agents())
    assert response.status_code == 503
    assert "root gone" in json.loads(response.body)["error"]
