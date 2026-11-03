"""agent_dossier：名册 + 标准档案的只读读手（WP-VAULT-AGENTS-VIEW）。

钉住四件事：类别决定该有哪几份档案、缺领地如实报缺、里程碑/状态从真文件读、
读一趟不写任何东西（尤其不许触发名册自愈回写）。
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from plobi.mind import agent_dossier as ad

ROSTER = textwrap.dedent(
    """
    version: 1
    agents:
      Aura:
        role: l2_project
        category: projects
        mind_subtree: Vault/dossiers/projects/Aura
        project_path: D:/projects/Aura
      SRTP:
        role: l2_research
        category: research
        mind_subtree: Vault/dossiers/research/SRTP
        project_path: D:/Research/01-SRTP
      电工电子学:
        role: l2_course
        category: courses
        project_path: D:/Cloud/Courses/电工电子学
      agenda:
        role: l1_secretary
        category: butler
        mind_subtree: Vault/dossiers/butler
    """
).lstrip()

PLAN = """---
project: Aura
title: Aura
type: engineering
status: active
updated: 2026-09-28
summary: 一句摘要。
---

# Aura

## Milestones

- [x] M1
- [x] M2
- [ ] M3
- [ ] M4
"""


@pytest.fixture()
def roots(tmp_path, monkeypatch):
    mind = tmp_path / "mind"
    vault = tmp_path / "vault"
    (mind / "wiki").mkdir(parents=True)
    (vault / "dossiers" / "projects" / "Aura").mkdir(parents=True)
    (vault / "dossiers" / "research" / "SRTP").mkdir(parents=True)
    (vault / "dossiers" / "butler").mkdir(parents=True)
    monkeypatch.setenv("MIND_ROOT", str(mind))
    monkeypatch.setenv("VAULT_ROOT", str(vault))
    roster = tmp_path / "projects.yaml"
    roster.write_text(ROSTER, encoding="utf-8")
    return tmp_path, vault, roster


def test_index_shape_and_categories(roots):
    _, vault, roster = roots
    (vault / "dossiers" / "projects" / "Aura" / "plan.md").write_text(PLAN, encoding="utf-8")
    (vault / "dossiers" / "projects" / "Aura" / "progress.md").write_text("# p\n", encoding="utf-8")
    (vault / "dossiers" / "research" / "SRTP" / "plan.md").write_text("# s\n", encoding="utf-8")

    data = ad.build_index(roster)
    by_name = {row["id"]: row for row in data["agents"]}

    assert set(by_name) == {"Aura", "SRTP", "电工电子学", "agenda"}
    assert data["totals"] == {"agents": 4, "withHome": 3, "withoutHome": 1}

    aura = by_name["Aura"]
    assert [d["key"] for d in aura["docs"]] == ["plan", "progress"]
    assert all(d["exists"] for d in aura["docs"])
    assert aura["categoryLabel"] == "项目"
    assert aura["homeLabel"] == "Vault/dossiers/projects/Aura"
    assert aura["workPath"] == "D:/projects/Aura"

    srtp = by_name["SRTP"]
    assert [d["key"] for d in srtp["docs"]] == ["plan", "progress", "research"]
    assert [d["exists"] for d in srtp["docs"]] == [True, False, False]

    course = by_name["电工电子学"]
    assert [d["key"] for d in course["docs"]] == ["requirements", "syllabus"]
    assert course["hasHome"] is False
    assert course["homePath"] == ""
    assert all(not d["exists"] for d in course["docs"])


def test_milestones_status_and_summary(roots):
    _, vault, roster = roots
    (vault / "dossiers" / "projects" / "Aura" / "plan.md").write_text(PLAN, encoding="utf-8")

    aura = {row["id"]: row for row in ad.build_index(roster)["agents"]}["Aura"]
    assert aura["milestones"] == {"done": 2, "total": 4}
    assert aura["status"] == "active"
    assert aura["summary"] == "一句摘要。"
    assert aura["updated"] == "2026-09-28"


def test_plan_without_frontmatter_still_has_excerpt(roots):
    _, vault, roster = roots
    (vault / "dossiers" / "projects" / "Aura" / "plan.md").write_text(
        "# Aura\n\n> 引用不算\n\n第一段正文。\n", encoding="utf-8"
    )

    aura = {row["id"]: row for row in ad.build_index(roster)["agents"]}["Aura"]
    assert aura["status"] == ""
    assert aura["summary"] == "第一段正文。"


def test_read_agent_returns_bodies(roots):
    _, vault, roster = roots
    (vault / "dossiers" / "projects" / "Aura" / "plan.md").write_text(PLAN, encoding="utf-8")

    page = ad.read_agent("Aura", roster)
    assert page is not None
    plan = page["docs"][0]
    assert plan["exists"] is True
    assert "## Milestones" in plan["text"]
    assert plan["truncated"] is False
    assert plan["fsPath"].endswith("plan.md")

    course = ad.read_agent("电工电子学", roster)
    assert course is not None
    assert all(d["exists"] is False and d["text"] == "" for d in course["docs"])

    assert ad.read_agent("不存在", roster) is None


def test_read_only_leaves_disk_and_roster_untouched(roots):
    tmp, vault, roster = roots
    (vault / "dossiers" / "projects" / "Aura" / "plan.md").write_text(PLAN, encoding="utf-8")

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
    _, vault, roster = roots
    big = vault / "dossiers" / "projects" / "Aura" / "plan.md"
    big.write_text("x" * (ad.MAX_DOC_BYTES + 10), encoding="utf-8")
    monkeypatch.setattr(ad, "MAX_DOC_BYTES", 64)
    monkeypatch.setattr(ad, "LIST_SCAN_BYTES", 64)

    page = ad.read_agent("Aura", roster)
    plan = page["docs"][0]
    assert plan["truncated"] is True
    assert len(plan["text"]) == 64


def test_missing_mind_root_reports_no_home(roots, monkeypatch):
    _, vault, roster = roots
    monkeypatch.delenv("MIND_ROOT", raising=False)
    monkeypatch.setattr(ad, "real_target", lambda *a, **k: (_ for _ in ()).throw(ad.MindUnavailable("no root")))

    aura = ad.build_index(roster)["agents"][0]
    assert aura["hasHome"] is False
    assert aura["docs"][0]["exists"] is False
