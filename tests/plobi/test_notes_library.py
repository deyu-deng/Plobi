"""知识库：只有笔记的那一面（WP-VAULT-AGENTS-VIEW）。"""

from __future__ import annotations

import textwrap

import pytest

from plobi.mind import notes_library as nl


@pytest.fixture()
def notes(tmp_path, monkeypatch):
    mind = tmp_path / "mind"
    vault = tmp_path / "vault"
    root = vault / "notes"
    (mind / "wiki").mkdir(parents=True)
    (root / "数学 Math").mkdir(parents=True)
    (root / "工程 Engineer").mkdir(parents=True)

    (root / "数学 Math" / "微积分.md").write_text(
        textwrap.dedent(
            """\
            ---
            title: 微积分笔记
            tags: [math, 微积分]
            updated: 2026-09-20
            belongs: [Aura]
            ---

            # 微积分

            极限与连续的第一段。
            """
        ),
        encoding="utf-8",
    )
    (root / "工程 Engineer" / "图学.md").write_text("# 工程图学\n\n投影法。\n", encoding="utf-8")
    (root / "根目录笔记.md").write_text("没有 frontmatter 的笔记。\n", encoding="utf-8")
    (root / "INDEX.md").write_text("# Mind · Knowledge Index\n", encoding="utf-8")
    (root / ".hidden.md").write_text("不该出现\n", encoding="utf-8")
    (vault / "projects").mkdir()

    monkeypatch.setenv("MIND_ROOT", str(mind))
    monkeypatch.setenv("VAULT_ROOT", str(vault))
    return root


def test_library_lists_only_notes(notes):
    data = nl.build_library()

    assert data["totals"]["listed"] == 4
    assert {row["path"] for row in data["notes"]} >= {
        "Vault/notes/数学 Math/微积分.md",
        "Vault/notes/工程 Engineer/图学.md",
        "Vault/notes/根目录笔记.md",
        "Vault/notes/INDEX.md",
    }
    assert all(".hidden" not in row["path"] for row in data["notes"])
    assert {t["name"] for t in data["topics"]} == {"数学 Math", "工程 Engineer", "未分类"}


def test_title_tags_and_excerpt_come_from_the_file(notes):
    data = nl.build_library()
    by_path = {row["path"]: row for row in data["notes"]}

    calculus = by_path["Vault/notes/数学 Math/微积分.md"]
    assert calculus["title"] == "微积分笔记"
    assert calculus["tags"] == ["math", "微积分"]
    assert calculus["belongs"] == ["Aura"]
    assert calculus["updated"] == "2026-09-20"
    assert calculus["excerpt"] == "极限与连续的第一段。"

    drawing = by_path["Vault/notes/工程 Engineer/图学.md"]
    assert drawing["title"] == "工程图学"
    assert drawing["excerpt"] == "投影法。"

    fallback = by_path["Vault/notes/根目录笔记.md"]
    assert fallback["title"] == "根目录笔记"
    assert fallback["topic"] == "未分类"


def test_tags_are_unioned_and_sorted(notes):
    data = nl.build_library()
    assert data["tags"] == ["math", "微积分"]


def test_read_note_returns_body(notes):
    page = nl.read_note("Vault/notes/数学 Math/微积分.md")
    assert page is not None
    assert "极限与连续" in page["text"]
    assert page["fsPath"].endswith("微积分.md")


def test_traversal_and_non_markdown_are_refused(notes):
    assert nl.read_note("Vault/notes/../../config.yaml") is None
    assert nl.read_note("Vault/projects/Aura/plan.md") is None
    assert nl.read_note("Vault/notes/数学 Math/微积分.txt") is None
    assert nl.read_note("") is None


def test_missing_root_is_an_error_not_an_empty_list(tmp_path, monkeypatch):
    mind = tmp_path / "mind"
    mind.mkdir()
    monkeypatch.setenv("MIND_ROOT", str(mind))
    monkeypatch.setenv("VAULT_ROOT", str(tmp_path / "nowhere"))

    with pytest.raises(nl.MindUnavailable):
        nl.build_library()
