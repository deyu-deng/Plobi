"""agent_dossier：名册 + 档案本体的只读读手（WP-VAULT-AGENTS-VIEW → WP-DOSSIER-SHAPE）。

钉住四件事：**档案本体**（``dossier.json``）是目标／状况／阶段的真源、缺领地如实报缺、
坏文件不许糊成空表、读一趟不写任何东西（尤其不许触发名册自愈回写）。

11-09 起 ``plan.md``／``progress.md`` 那一对退役（用户：「抽成结构化并把 md 删掉」）：
档案本体是一份结构化记录，测试也照它写。
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from plobi.human import agent_dossier as ad

ROSTER = textwrap.dedent(
    """
    version: 1
    agents:
      Aura:
        role: principal
        category: projects
        mind_subtree: Dossiers/projects/Aura
        project_path: D:/projects/Aura
      SRTP:
        role: principal
        category: research
        mind_subtree: Dossiers/research/SRTP
        project_path: D:/Research/01-SRTP
      电工电子学:
        role: principal
        category: courses
        project_path: D:/Cloud/Courses/电工电子学
      agenda:
        role: conductor
        category: butler
        mind_subtree: Dossiers/butler
    """
).lstrip()

PROFILE: dict = {
    "version": 1,
    "agent": "Aura",
    "title": "Aura",
    "category": "projects",
    "goal": "把情绪记录做成一个能用的应用。",
    "status": "当前阶段：上线准备；下一步：过审",
    "stages": [
        {"id": "s1", "title": "核心模型", "detail": "罗素模型落地", "done": True},
        {"id": "s2", "title": "交互打磨", "detail": "流体水母", "done": True},
        {"id": "s3", "title": "上架", "detail": "软著已提交", "done": False},
    ],
    "registry": {
        "project": "Aura",
        "state": "active",
        "summary": "一句摘要。",
        "updated": "2026-09-28",
    },
    "updated": "2026-09-28",
    "log": "# Aura · 流水\n\n- 2026-09-28：建档\n",
}


def _write_profile(directory: Path, profile: dict | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "dossier.json"
    path.write_text(
        json.dumps(PROFILE if profile is None else profile, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


@pytest.fixture()
def roots(tmp_path, monkeypatch):
    mind = tmp_path / "mind"
    surface = tmp_path / "surface"
    (mind / "wiki").mkdir(parents=True)
    (surface / "dossiers" / "projects" / "Aura").mkdir(parents=True)
    (surface / "dossiers" / "research" / "SRTP").mkdir(parents=True)
    (surface / "dossiers" / "butler").mkdir(parents=True)
    monkeypatch.setenv("MIND_ROOT", str(mind))
    monkeypatch.setenv("HUMAN_ROOT", str(surface))
    # 共享层也得关在 tmp 里：实例判据读 souls/<name>.md，不能让它摸到真 ~/Plobi。
    plobi_home = tmp_path / "plobi"
    (plobi_home / "souls").mkdir(parents=True)
    monkeypatch.setenv("PLOBI_HOME", str(plobi_home))
    from plobi.agents.registry import shared_soul_path_for

    for name in ("Aura", "SRTP", "电工电子学"):
        shared_soul_path_for(name).write_text("# soul\n", encoding="utf-8")
    roster = tmp_path / "projects.yaml"
    roster.write_text(ROSTER, encoding="utf-8")
    return tmp_path, surface, roster


def test_index_shape_and_categories(roots):
    _, surface, roster = roots
    _write_profile(surface / "dossiers" / "projects" / "Aura")
    (surface / "dossiers" / "projects" / "Aura" / "research.md").write_text("# r\n", encoding="utf-8")

    data = ad.build_index(roster)
    by_name = {row["id"]: row for row in data["agents"]}

    assert set(by_name) == {"Aura", "SRTP", "电工电子学", "agenda"}
    # 家目录是可选位（4 行里 3 行有，电工电子学没有）；实例健康度看 ready。
    assert data["totals"] == {
        "agents": 4,
        "withHome": 3,
        "withoutHome": 1,
        "ready": 3,
        "notReady": 1,
    }
    # 没有家 ≠ 没建：电工电子学没 mind_subtree，但身份文件在盘上，实例是就绪的。
    assert by_name["电工电子学"]["hasHome"] is False
    assert by_name["电工电子学"]["instanceReady"] is True
    # agenda 是名册里那条没有身份文件的特殊行，才该判「没落地」。
    assert by_name["agenda"]["instanceReady"] is False

    aura = by_name["Aura"]
    # md 那一对退役了：projects 只剩**研究笔记**那一份文档。
    assert [d["key"] for d in aura["docs"]] == ["research"]
    assert aura["docs"][0]["exists"] is True
    assert aura["categoryLabel"] == "项目"
    assert aura["homeLabel"] == "Dossiers/projects/Aura"
    assert aura["workPath"] == "D:/projects/Aura"

    srtp = by_name["SRTP"]
    assert [d["key"] for d in srtp["docs"]] == ["research"]
    assert [d["exists"] for d in srtp["docs"]] == [False]

    course = by_name["电工电子学"]
    assert [d["key"] for d in course["docs"]] == ["requirements", "syllabus"]
    assert course["hasHome"] is False
    assert course["homePath"] == ""
    assert all(not d["exists"] for d in course["docs"])


def test_profile_drives_goal_status_and_stages(roots):
    _, surface, roster = roots
    _write_profile(surface / "dossiers" / "projects" / "Aura")

    aura = {row["id"]: row for row in ad.build_index(roster)["agents"]}["Aura"]
    assert aura["goal"] == "把情绪记录做成一个能用的应用。"
    # **现状**在 ``summary`` 那一格；``status`` 是**生命周期状态**（侧栏那颗点认它）。
    assert aura["summary"] == "当前阶段：上线准备；下一步：过审"
    assert aura["status"] == "active"
    assert aura["milestones"] == {"done": 2, "total": 3}
    assert [s["title"] for s in aura["stages"]] == ["核心模型", "交互打磨", "上架"]
    assert aura["updated"] == "2026-09-28"
    assert aura["profileExists"] is True
    assert aura["profileError"] == ""


def test_missing_profile_reports_not_established(roots):
    _, surface, roster = roots

    aura = {row["id"]: row for row in ad.build_index(roster)["agents"]}["Aura"]
    assert aura["profileExists"] is False
    assert aura["profileError"] == ""
    assert aura["goal"] == ""
    assert aura["summary"] == ""
    assert aura["milestones"] == {"done": 0, "total": 0}


def test_broken_profile_reports_unreadable_not_empty(roots):
    _, surface, roster = roots
    (surface / "dossiers" / "projects" / "Aura" / "dossier.json").write_text(
        "{ 这不是 JSON", encoding="utf-8"
    )

    aura = {row["id"]: row for row in ad.build_index(roster)["agents"]}["Aura"]
    # 坏文件**不许糊成空档案**：读得到「读不到」，不是「什么都没有」。
    assert aura["profileExists"] is True
    assert aura["profileError"] != ""
    assert aura["summary"] == ""


def test_read_agent_returns_the_whole_profile(roots):
    _, surface, roster = roots
    _write_profile(surface / "dossiers" / "projects" / "Aura")

    page = ad.read_agent("Aura", roster)
    assert page is not None
    assert page["profile"]["exists"] is True
    assert page["profile"]["error"] is None
    assert page["profile"]["profile"]["log"].startswith("# Aura · 流水")

    course = ad.read_agent("电工电子学", roster)
    assert course is not None
    assert all(d["exists"] is False and d["text"] == "" for d in course["docs"])

    assert ad.read_agent("不存在", roster) is None


def test_read_only_leaves_disk_and_roster_untouched(roots):
    tmp, surface, roster = roots
    _write_profile(surface / "dossiers" / "projects" / "Aura")

    def snapshot() -> dict[str, tuple[int, int]]:
        return {
            str(p.relative_to(tmp)): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in tmp.rglob("*")
            if p.is_file()
        }

    before = snapshot()
    ad.build_index(roster)
    ad.read_agent("Aura", roster)
    ad.read_agent("电工电子学", roster)
    assert snapshot() == before


def test_truncates_oversized_doc(roots, monkeypatch):
    _, surface, roster = roots
    big = surface / "dossiers" / "projects" / "Aura" / "research.md"
    big.write_text("x" * (ad.MAX_DOC_BYTES + 10), encoding="utf-8")
    monkeypatch.setattr(ad, "MAX_DOC_BYTES", 64)
    monkeypatch.setattr(ad, "LIST_SCAN_BYTES", 64)

    page = ad.read_agent("Aura", roster)
    doc = page["docs"][0]
    assert doc["truncated"] is True
    assert len(doc["text"]) == 64


def test_missing_mind_root_reports_no_home(roots, monkeypatch):
    _, surface, roster = roots
    monkeypatch.delenv("MIND_ROOT", raising=False)
    monkeypatch.setattr(ad, "real_target", lambda *a, **k: (_ for _ in ()).throw(ad.MindUnavailable("no root")))

    rows = {row["id"]: row for row in ad.build_index(roster)["agents"]}
    assert rows["Aura"]["hasHome"] is False
    assert rows["Aura"]["docs"][0]["exists"] is False
    assert rows["Aura"]["profileExists"] is False
