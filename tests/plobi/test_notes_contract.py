"""笔记硬约束：不合规就拒写，并逐条说清缺了什么（WP-NOTES-CONTRACT）。

用户 2026-10-25 答的是最硬那一档：「硬约束，不合规拒写」。这里钉住四条规则，
以及一条边界：**只管 Agent 写笔记这一手**，人自己写不需要闸门。
"""

from __future__ import annotations

import json
import textwrap

import pytest

from plobi.mind import provider as provider_module
from plobi.mind.notes_contract import validate_note
from plobi.mind.provider import MindProvider

ROSTER = textwrap.dedent(
    """
    version: 1
    agents:
      Aura:
        role: l2_project
        category: projects
        mind_subtree: Vault/projects/Aura
    """
).lstrip()

GOOD = textwrap.dedent(
    """\
    ---
    title: 傅里叶级数
    tags: [数学, 信号]
    belongs: [Aura]
    ---

    # 傅里叶级数

    一段正文。
    """
)


@pytest.fixture()
def notes(tmp_path, monkeypatch):
    mind = tmp_path / "mind"
    vault = tmp_path / "vault"
    (mind / "wiki").mkdir(parents=True)
    (vault / "notes" / "数学 Math").mkdir(parents=True)
    (vault / "projects").mkdir()
    (tmp_path / "projects.yaml").write_text(ROSTER, encoding="utf-8")
    monkeypatch.setenv("MIND_ROOT", str(mind))
    monkeypatch.setenv("VAULT_ROOT", str(vault))
    monkeypatch.setenv("PLOBI_PROJECTS_CONFIG", str(tmp_path / "projects.yaml"))
    return vault / "notes"


def test_a_well_formed_note_passes(notes):
    assert validate_note("Vault/notes/数学 Math/傅里叶.md", GOOD, notes) == []


def test_missing_frontmatter_names_the_three_required_keys(notes):
    problems = validate_note("Vault/notes/数学 Math/x.md", "# 只有正文\n", notes)
    assert any("frontmatter" in p for p in problems)


def test_each_missing_field_is_reported_on_its_own(notes):
    body = "# 傅里叶级数\n\n正文。\n"
    without_tags = "---\ntitle: 傅里叶级数\nbelongs: [Aura]\n---\n\n" + body
    without_belongs = "---\ntitle: 傅里叶级数\ntags: [数学]\n---\n\n" + body
    assert any("`tags`" in p for p in validate_note("Vault/notes/数学 Math/a.md", without_tags, notes))
    problems = validate_note("Vault/notes/数学 Math/b.md", without_belongs, notes)
    assert any("`belongs`" in p for p in problems)


def test_belongs_must_name_a_registered_agent(notes):
    content = GOOD.replace("belongs: [Aura]", "belongs: [不存在的分身]")
    problems = validate_note("Vault/notes/数学 Math/c.md", content, notes)
    assert any("不存在的分身" in p for p in problems)


def test_one_topic_per_note_and_the_heading_must_match_the_title(notes):
    two_headings = GOOD.replace("一段正文。", "一段正文。\n\n# 另一个主题\n\n第二段。")
    assert any("一级标题" in p for p in validate_note("Vault/notes/数学 Math/d.md", two_headings, notes))

    mismatched = GOOD.replace("title: 傅里叶级数", "title: 拉普拉斯变换")
    assert any("不一致" in p for p in validate_note("Vault/notes/数学 Math/e.md", mismatched, notes))


def test_new_topic_folders_are_not_invented_by_a_write(notes):
    problems = validate_note("Vault/notes/新发明主题/x.md", GOOD, notes)
    assert any("新发明主题" in p for p in problems)


def test_the_gate_only_covers_notes(notes):
    # 档案与索引件不走这一道：一条都没有 = 交给闸门与 verifier 去管。
    assert validate_note("Vault/projects/Aura/progress.md", "随便什么", notes) == []
    assert validate_note("Vault/notes/INDEX.md", "# 索引\n", notes) == []
    assert validate_note("Vault/notes/数学 Math/x.txt", "x", notes)


def test_mind_write_refuses_a_non_compliant_note_and_writes_nothing(notes, monkeypatch):
    provider = MindProvider()
    provider.initialize("sess-notes", agent_context="primary")
    monkeypatch.setattr(
        provider_module.MindWriter, "_gate_refusals", lambda self, batch: []
    )  # 闸门开着也不能放行不合规的笔记

    result = json.loads(
        provider.handle_tool_call(
            "mind_write",
            {"path": "Vault/notes/数学 Math/拉普拉斯.md", "content": "# 拉普拉斯\n\n没有 frontmatter。\n"},
        )
    )
    assert result["success"] is False
    assert result["problems"]
    assert not (notes / "拉普拉斯.md").exists()
