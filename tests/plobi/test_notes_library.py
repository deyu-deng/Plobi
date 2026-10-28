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

            极限与连续的第一段。数列极限是先固定 ε 再找 N，N 依赖 ε 而不是反过来；
            收敛数列必有界，单调加有界才直接给出极限存在。
            """
        ),
        encoding="utf-8",
    )
    (root / "工程 Engineer" / "图学.md").write_text(
        "---\ntitle: 工程图学\ntags: [工程]\nbelongs: []\n---\n\n"
        "# 工程图学\n\n正投影是三视图的基础：先定主视图的投射方向，再按长对正、高平齐、宽相等摆另外两个视图。\n"
        "剖视的目的是把内部结构画清楚，别拿虚线凑。\n",
        encoding="utf-8",
    )
    (root / "根目录笔记.md").write_text(
        "没有 frontmatter 的笔记。这一篇连一级标题都没有，正文写成一段，"
        "为的是让「标题拿文件名兜底」那一条判据真的走到。\n",
        encoding="utf-8",
    )
    (root / "数学 Math" / "空壳.md").write_text(
        "---\ntitle: 空壳\ntags: [数学]\nbelongs: []\n---\n\n# 空壳\n\n待补。\n",
        encoding="utf-8",
    )
    (root / "INDEX.md").write_text("# Mind · Knowledge Index\n", encoding="utf-8")
    (root / ".hidden.md").write_text("不该出现\n", encoding="utf-8")
    (vault / "projects").mkdir()

    monkeypatch.setenv("MIND_ROOT", str(mind))
    monkeypatch.setenv("VAULT_ROOT", str(vault))
    return root


def test_library_lists_only_notes(notes):
    data = nl.build_library()

    # INDEX.md 是目录件不是笔记（和 notes_contract 那一闸同一口径），别让它混进知识库里。
    assert data["totals"]["listed"] == 3
    assert {row["path"] for row in data["notes"]} >= {
        "Vault/notes/数学 Math/微积分.md",
        "Vault/notes/工程 Engineer/图学.md",
        "Vault/notes/根目录笔记.md",
    }
    assert all(row["path"] != "Vault/notes/INDEX.md" for row in data["notes"])
    assert all(".hidden" not in row["path"] for row in data["notes"])
    assert {t["name"] for t in data["topics"]} == {"数学 Math", "工程 Engineer", "未分类"}


def test_an_empty_shell_is_not_shown_but_counted(notes):
    """用户 2026-10-27：「只留有内容的」。屏上不出、但数字要留着——藏了几篇要数得出。"""
    data = nl.build_library()

    assert "Vault/notes/数学 Math/空壳.md" not in {row["path"] for row in data["notes"]}
    assert data["totals"]["hidden"] == 1
    assert data["totals"]["scanned"] == 4  # 扫到 4 篇（INDEX 不算），列 3 藏 1


def test_a_code_heavy_note_is_not_judged_by_its_fences(notes):
    """净字只数正文那几句，围栏里的示范代码不算内容。"""
    (notes / "工具 Tools").mkdir()
    (notes / "工具 Tools" / "骨架.md").write_text(
        "---\ntitle: 骨架\ntags: [工具]\nbelongs: []\n---\n\n# 骨架\n\n"
        "```python\n" + "print('示范')\n" * 40 + "```\n\n就这一句。\n",
        encoding="utf-8",
    )
    assert "Vault/notes/工具 Tools/骨架.md" not in {
        row["path"] for row in nl.build_library()["notes"]
    }


def test_title_tags_and_excerpt_come_from_the_file(notes):
    data = nl.build_library()
    by_path = {row["path"]: row for row in data["notes"]}

    calculus = by_path["Vault/notes/数学 Math/微积分.md"]
    assert calculus["title"] == "微积分笔记"
    assert calculus["tags"] == ["math", "微积分"]
    assert calculus["belongs"] == ["Aura"]
    assert calculus["updated"] == "2026-09-20"
    assert calculus["excerpt"] == "极限与连续的第一段。数列极限是先固定 ε 再找 N，N 依赖 ε 而不是反过来；"

    drawing = by_path["Vault/notes/工程 Engineer/图学.md"]
    assert drawing["title"] == "工程图学"
    assert drawing["excerpt"].startswith("正投影是三视图的基础")

    fallback = by_path["Vault/notes/根目录笔记.md"]
    assert fallback["title"] == "根目录笔记"
    assert fallback["topic"] == "未分类"


def test_tags_are_unioned_and_sorted(notes):
    data = nl.build_library()
    assert data["tags"] == ["math", "工程", "微积分"]


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
