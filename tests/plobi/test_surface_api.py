"""surface 档案的 HTTP 面：两条路由的信封与状态码（WP-VAULT-AGENTS-VIEW）。

直接调路由函数（不经过 ASGI），把"空表 / 读失败 / 名字不在名册"三种答案分开钉住。
"""

from __future__ import annotations

import asyncio
import json
import textwrap

import pytest

from plobi.mind import agent_dossier as ad
from plobi.mind import surface_api

ROSTER = textwrap.dedent(
    """
    version: 1
    agents:
      Aura:
        role: l2_project
        category: projects
        mind_subtree: Dossiers/projects/Aura
        project_path: D:/projects/Aura
      电工电子学:
        role: l2_course
        category: courses
    """
).lstrip()


@pytest.fixture()
def live(monkeypatch, tmp_path):
    mind = tmp_path / "mind"
    surface = tmp_path / "surface"
    (mind / "wiki").mkdir(parents=True)
    (surface / "dossiers" / "projects" / "Aura").mkdir(parents=True)
    # 档案本体（11-09 起真源）：目标／现状／阶段。
    (surface / "dossiers" / "projects" / "Aura" / "dossier.json").write_text(
        json.dumps(
            {
                "agent": "Aura",
                "goal": "目标。",
                "status": "当前阶段：立项。",
                "stages": [{"id": "s1", "title": "立项", "done": False}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "projects.yaml").write_text(ROSTER, encoding="utf-8")
    monkeypatch.setenv("MIND_ROOT", str(mind))
    monkeypatch.setenv("HUMAN_ROOT", str(surface))
    monkeypatch.setenv("PLOBI_PROJECTS_CONFIG", str(tmp_path / "projects.yaml"))
    return tmp_path


def test_list_route_returns_every_agent(live):
    response = asyncio.run(surface_api.list_surface_agents())
    body = response if isinstance(response, dict) else json.loads(response.body)
    assert body["ok"] is True
    assert {row["id"] for row in body["data"]["agents"]} == {"Aura", "电工电子学"}
    assert body["data"]["totals"] == {"agents": 2, "withHome": 1, "withoutHome": 1}


def test_detail_route_returns_bodies(live):
    response = asyncio.run(surface_api.get_surface_agent("Aura"))
    body = response if isinstance(response, dict) else json.loads(response.body)
    assert body["ok"] is True
    # 档案本体整份都在这一页里（目标／状况／阶段／流水）。
    assert body["data"]["profile"]["profile"]["goal"] == "目标。"
    assert body["data"]["profile"]["profile"]["status"] == "当前阶段：立项。"
    assert body["data"]["goal"] == "目标。"
    assert body["data"]["summary"] == "当前阶段：立项。"


def test_profile_route_returns_the_whole_record(live):
    response = asyncio.run(surface_api.get_surface_agent_profile("Aura"))
    body = response if isinstance(response, dict) else json.loads(response.body)
    assert body["ok"] is True
    assert body["data"]["exists"] is True
    assert body["data"]["error"] is None
    assert body["data"]["profile"]["stages"][0]["title"] == "立项"


def test_profile_route_separates_missing_from_broken(live):
    """**没建立**与**读不到**是两件事：空档案会被读成「什么都没有」，而那是假的。"""
    profile_path = live / "surface" / "dossiers" / "projects" / "Aura" / "dossier.json"
    profile_path.unlink()
    response = asyncio.run(surface_api.get_surface_agent_profile("Aura"))
    body = response if isinstance(response, dict) else json.loads(response.body)
    assert body["data"]["exists"] is False
    assert body["data"]["error"] is None

    profile_path.write_text("{ 坏的", encoding="utf-8")
    response = asyncio.run(surface_api.get_surface_agent_profile("Aura"))
    body = response if isinstance(response, dict) else json.loads(response.body)
    assert body["data"]["exists"] is True
    assert body["data"]["error"] is not None


def test_unknown_agent_is_404_not_an_empty_page(live):
    response = asyncio.run(surface_api.get_surface_agent("不存在"))
    assert response.status_code == 404
    assert json.loads(response.body)["ok"] is False


def test_roster_failure_is_503(monkeypatch, tmp_path):
    monkeypatch.setenv("PLOBI_PROJECTS_CONFIG", str(tmp_path / "missing.yaml"))
    monkeypatch.setattr(
        ad, "build_index", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("root gone"))
    )
    response = asyncio.run(surface_api.list_surface_agents())
    assert response.status_code == 503
    assert "root gone" in json.loads(response.body)["error"]


# ── 作用域哨兵 → 名册那一行（WP-VAULT-TAKEOVER 11-08 续）─────────────────────


@pytest.fixture()
def l1_scope_live(monkeypatch, tmp_path):
    """名册里有 L1 那一行（``role=l1_secretary``）时的档案读面。"""
    mind = tmp_path / "mind"
    surface = tmp_path / "surface"
    mind.mkdir(parents=True)
    surface.mkdir(parents=True)
    roster = textwrap.dedent(
        """
        version: 1
        agents:
          secretary:
            role: l1_secretary
            display_name: 总秘书
        """
    ).lstrip()
    (tmp_path / "projects.yaml").write_text(roster, encoding="utf-8")
    monkeypatch.setenv("MIND_ROOT", str(mind))
    monkeypatch.setenv("HUMAN_ROOT", str(surface))
    monkeypatch.setenv("PLOBI_PROJECTS_CONFIG", str(tmp_path / "projects.yaml"))
    return tmp_path


def test_l1_scope_resolves_to_the_roster_row(l1_scope_live):
    """``'l1'`` 是作用域哨兵，**不是**名册 id：档案读面要把它翻译成 L1 那一行。

    不翻译的话档案卡去要 ``/api/surface/agents/l1`` ⇒ 404「agent 'l1' is not
    registered」——那正是用户 11-08 16:4x 问「档案的图标怎么还没有出现」的另一半。
    """
    response = asyncio.run(surface_api.get_surface_agent("l1"))
    body = response if isinstance(response, dict) else json.loads(response.body)
    assert body["ok"] is True
    assert body["data"]["id"] == "secretary"
    assert body["data"]["displayName"] == "总秘书"


def test_l1_scope_without_a_roster_row_is_404(l1_scope_live, tmp_path):
    """名册里没有 L1 那一行 ⇒ 照旧 404，不许瞎猜一个 id 出来。"""
    (tmp_path / "projects.yaml").write_text(
        "version: 1\nagents:\n  Aura:\n    role: l2_project\n", encoding="utf-8"
    )
    response = asyncio.run(surface_api.get_surface_agent("l1"))
    assert response.status_code == 404
    assert json.loads(response.body)["ok"] is False


def test_a_roster_row_name_still_works_as_before(l1_scope_live):
    """翻译只认哨兵：名册里的 name 原样能用，不被改写。"""
    response = asyncio.run(surface_api.get_surface_agent("secretary"))
    body = response if isinstance(response, dict) else json.loads(response.body)
    assert body["ok"] is True
    assert body["data"]["id"] == "secretary"


# ── 笔记栏：人在 Plobi 里编辑后整篇存回（WP-NOTES-WORKBENCH）─────────────────


@pytest.fixture()
def notes_live(live, monkeypatch):
    """notes/ 下先有一篇带 frontmatter 的长笔记；git 提交那一段不进单测。"""
    root = live / "surface" / "notes" / "数学"
    root.mkdir(parents=True)
    (root / "微积分.md").write_text("---\ntitle: 微积分\n---\n\n## 第1章 极限\n\n原文", encoding="utf-8")
    monkeypatch.setattr(
        "plobi.mind.notes_library._commit_note",
        lambda *a, **k: {"committed": False, "commit": "", "detail": "test"},
    )
    return root


def _put(path, **body):
    return asyncio.run(surface_api.put_surface_note(body, path))


def test_save_note_writes_exact_bytes_without_adding_a_newline(notes_live):
    text = "---\ntitle: 微积分\n---\n\n## 第1章 极限\n\n改过的正文"
    response = _put("Notes/数学/微积分.md", text=text)
    assert response["ok"] is True
    assert response["data"]["created"] is False
    assert (notes_live / "微积分.md").read_text(encoding="utf-8") == text


def test_save_note_rejects_a_stale_base_with_409(notes_live):
    response = _put(
        "Notes/数学/微积分.md",
        text="完全不同的新正文",
        base="---\ntitle: 微积分\n---\n\n## 第1章 极限\n\n我打开时看到的是旧的",
    )
    assert response.status_code == 409
    assert json.loads(response.body)["ok"] is False
    assert "原文" in (notes_live / "微积分.md").read_text(encoding="utf-8")


def test_create_makes_a_new_note_and_its_folder(notes_live):
    response = _put("Notes/物理/新的一章.md", text="# 新的一章\n", base="", create=True)
    assert response["ok"] is True
    assert response["data"]["created"] is True
    assert (notes_live.parent / "物理" / "新的一章.md").read_text(encoding="utf-8") == "# 新的一章\n"


def test_saving_an_unknown_path_without_create_is_404_not_a_new_file(notes_live):
    response = _put("Notes/数学/拼错了.md", text="不该被静默新建")
    assert response.status_code == 404
    assert not (notes_live / "拼错了.md").exists()


def test_save_note_refuses_escapes_and_non_markdown(notes_live):
    for bad in (
        "Notes/../../outside.md",
        "Diary/2026-11-04.md",
        "Notes/数学/微积分.txt",
        "Notes/.obsidian/workspace.md",
    ):
        response = _put(bad, text="x", create=True)
        assert response.status_code == 400, bad
    assert not (notes_live.parent.parent / "outside.md").exists()


def test_save_note_requires_a_text_string(notes_live):
    response = _put("Notes/数学/微积分.md", text=["不是字符串"])
    assert response.status_code == 400


def test_note_template_keeps_frontmatter_minimal():
    from plobi.mind.notes_library import note_template

    text = note_template("线性代数")
    assert text.startswith("---\n")
    assert "title: 线性代数" in text
    assert "belongs: []" in text
    assert text.rstrip().endswith("# 线性代数")


def test_http_roundtrip_binds_body_and_query(notes_live):
    """Body(...) + Query(...) 混用只能走真 ASGI 才测得出来；直接调函数会漏。"""
    from urllib.parse import quote

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from plobi.mind.surface_api import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    client = TestClient(app)
    path = "Notes/数学/微积分.md"
    url = "/api/surface/notes/content?path=" + quote(path)
    original = notes_live.joinpath("微积分.md").read_text(encoding="utf-8")

    assert client.get(url).json()["data"]["text"] == original
    saved = client.put(url, json={"text": "改过的正文", "base": original})
    assert saved.status_code == 200 and saved.json()["ok"] is True
    assert client.get(url).json()["data"]["text"] == "改过的正文"

    stale = client.put(url, json={"text": "不该覆盖", "base": original})
    assert stale.status_code == 409
    assert client.get(url).json()["data"]["text"] == "改过的正文"


# ── 人读面读不到时：回 503，不回「一个都没有」（WP-VAULT-TAKEOVER）────────────


@pytest.fixture()
def surface_gone(monkeypatch, tmp_path):
    """人读面指针指着一个**盘上不存在**的目录（指针没钉／陈旧的旧布局）。"""
    mind = tmp_path / "mind"
    mind.mkdir()
    monkeypatch.setenv("MIND_ROOT", str(mind))
    monkeypatch.setenv("HUMAN_ROOT", str(tmp_path / "surface-gone"))
    monkeypatch.setenv("PLOBI_PROJECTS_CONFIG", str(tmp_path / "projects.yaml"))
    return tmp_path


def test_notes_root_gone_is_503_not_an_empty_list(surface_gone):
    """读不到 ≠ 一个都没有：指针断了要说读不到，不能回一个空表。"""
    response = asyncio.run(surface_api.list_surface_notes())
    assert response.status_code == 503
    body = json.loads(response.body)
    assert body["ok"] is False
    assert "notes root does not exist" in body["error"]


def test_diary_root_gone_is_503_not_an_empty_list(surface_gone):
    """同上：日记这一腿也不能拿「0 篇」当答案——DB 里那几行只是索引的残影。"""
    response = asyncio.run(surface_api.list_surface_diary())
    assert response.status_code == 503
    body = json.loads(response.body)
    assert body["ok"] is False
    assert "diary root does not exist" in body["error"]


def test_root_gone_is_503_even_when_the_db_still_has_rows(surface_gone, monkeypatch, tmp_path):
    """DB 里有行、人读面读不到 ⇒ 仍然 503：索引指着读不到的源，它给的行数可能是陈的。"""
    from plobi.mind import vault

    monkeypatch.setenv("PLOBI_VAULT_DB", str(tmp_path / "vault.db"))
    vault.connect()
    vault.upsert_note(vault.VaultNote(
        path="Notes/数学/微积分.md", title="微积分", topic="数学",
        tags=[], belongs=[], updated="2026-11-08T00:00:00", chars=10, excerpt="",
    ))

    response = asyncio.run(surface_api.list_surface_notes())
    assert response.status_code == 503
    assert "notes root does not exist" in json.loads(response.body)["error"]
