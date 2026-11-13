"""笔记硬约束：不合规就拒写，并逐条说清缺了什么（WP-NOTES-CONTRACT）。

用户 2026-10-25 答的是最硬那一档：「硬约束，不合规拒写」。这里钉住五条规则，
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
        mind_subtree: Dossiers/projects/Aura
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
    surface = tmp_path / "surface"
    (mind / "wiki").mkdir(parents=True)
    (surface / "notes" / "数学 Math").mkdir(parents=True)
    (surface / "dossiers" / "projects").mkdir(parents=True)
    (tmp_path / "projects.yaml").write_text(ROSTER, encoding="utf-8")
    monkeypatch.setenv("MIND_ROOT", str(mind))
    monkeypatch.setenv("HUMAN_ROOT", str(surface))
    monkeypatch.setenv("PLOBI_PROJECTS_CONFIG", str(tmp_path / "projects.yaml"))
    return surface / "notes"


def test_a_well_formed_note_passes(notes):
    assert validate_note("Notes/数学 Math/傅里叶.md", GOOD, notes) == []


def test_missing_frontmatter_names_the_three_required_keys(notes):
    problems = validate_note("Notes/数学 Math/x.md", "# 只有正文\n", notes)
    assert any("frontmatter" in p for p in problems)


def test_each_missing_field_is_reported_on_its_own(notes):
    body = "# 傅里叶级数\n\n正文。\n"
    without_tags = "---\ntitle: 傅里叶级数\nbelongs: [Aura]\n---\n\n" + body
    without_belongs = "---\ntitle: 傅里叶级数\ntags: [数学]\n---\n\n" + body
    assert any("`tags`" in p for p in validate_note("Notes/数学 Math/a.md", without_tags, notes))
    problems = validate_note("Notes/数学 Math/b.md", without_belongs, notes)
    assert any("`belongs`" in p for p in problems)


def test_belongs_must_name_a_registered_agent(notes):
    content = GOOD.replace("belongs: [Aura]", "belongs: [不存在的分身]")
    problems = validate_note("Notes/数学 Math/c.md", content, notes)
    assert any("不存在的分身" in p for p in problems)


def test_one_topic_per_note_and_the_heading_must_match_the_title(notes):
    two_headings = GOOD.replace("一段正文。", "一段正文。\n\n# 另一个主题\n\n第二段。")
    assert any("一级标题" in p for p in validate_note("Notes/数学 Math/d.md", two_headings, notes))

    mismatched = GOOD.replace("title: 傅里叶级数", "title: 拉普拉斯变换")
    assert any("不一致" in p for p in validate_note("Notes/数学 Math/e.md", mismatched, notes))


def test_headings_inside_a_code_fence_are_examples_not_headings(notes):
    """讲 Markdown 的笔记会在 ``` 里示范 `# 一级标题`——那不算本篇的标题。

    存量核对时实测到的假阳性：`工具 Tools/软件 Software/Obsidian.md` 只有一个真标题，
    按行扫却数出 18 个，一篇合规笔记被判成「一篇只许一个一级标题」。
    """
    fenced = GOOD.replace(
        "一段正文。",
        "```\n# 一级标题\n# 另一个一级标题\n```\n\n~~~\n# 还有一个\n~~~\n\n一段正文。",
    )
    assert validate_note("Notes/数学 Math/fence.md", fenced, notes) == []


def test_topic_readme_is_an_index_not_a_note(notes):
    assert validate_note("Notes/数学 Math/README.md", "# 这一目录的索引\n", notes) == []


def test_new_topic_folders_are_not_invented_by_a_write(notes):
    problems = validate_note("Notes/新发明主题/x.md", GOOD, notes)
    assert any("新发明主题" in p for p in problems)


def test_the_gate_only_covers_notes(notes):
    # 档案与索引件不走这一道：一条都没有 = 交给闸门与 verifier 去管。
    assert validate_note("Dossiers/projects/Aura/progress.md", "随便什么", notes) == []
    assert validate_note("Notes/INDEX.md", "# 索引\n", notes) == []
    assert validate_note("Notes/数学 Math/x.txt", "x", notes)


def test_public_knowledge_declares_an_empty_belongs(notes):
    """用户 2026-10-27：`belongs` 这一格必须在，但可以是 []（＝公共知识）。"""
    public = GOOD.replace("belongs: [Aura]", "belongs: []")
    assert validate_note("Notes/数学 Math/傅里叶.md", public, notes) == []

    absent = GOOD.replace("belongs: [Aura]\n", "")
    problems = validate_note("Notes/数学 Math/傅里叶.md", absent, notes)
    assert any("belongs" in p for p in problems)


def test_relates_is_the_second_axis_and_must_be_real(notes):
    """微积分这种：不属于谁，但很多 Agent 的任务用得上 —— 关联记在 `relates`。"""
    linked = GOOD.replace("belongs: [Aura]", "belongs: []\nrelates: [Aura]")
    assert validate_note("Notes/数学 Math/傅里叶.md", linked, notes) == []

    empty_relates = GOOD.replace("belongs: [Aura]", "belongs: []\nrelates: []")
    assert any("relates" in p for p in validate_note("Notes/数学 Math/a.md", empty_relates, notes))

    fake = GOOD.replace("belongs: [Aura]", "belongs: []\nrelates: [没这个分身]")
    problems = validate_note("Notes/数学 Math/b.md", fake, notes)
    assert any("没这个分身" in p for p in problems)


def test_note_write_refuses_a_non_compliant_note_and_writes_nothing(notes, monkeypatch):
    provider = MindProvider()
    provider.initialize("sess-notes", agent_context="primary")
    monkeypatch.setattr(
        provider_module.MindWriter, "_gate_refusals", lambda self, batch: []
    )  # 闸门开着也不能放行不合规的笔记

    result = json.loads(
        provider.handle_tool_call(
            "note_write",
            {"path": "Notes/数学 Math/拉普拉斯.md", "content": "# 拉普拉斯\n\n没有 frontmatter。\n"},
        )
    )
    assert result["success"] is False
    assert result["problems"]
    assert not (notes / "拉普拉斯.md").exists()


# ── 名字那一族（WP-AGENT-NAME-UNIFY，用户 11-12 22:32）───────────────────────
#
# 可见面一律用**用户命名的名字**：Agent 照着自己被告知的名字写 ``belongs``，
# 那道闸就得认它（用户 11-12 22:32 那三篇待批准的笔记就是这么写歪的）。

DISPLAY_ROSTER = textwrap.dedent(
    """
    version: 1
    agents:
      ii:
        role: l2_project
        category: courses
        display_name: 大物物理（甲）II
    """
).lstrip()

DISPLAY_NOTE = textwrap.dedent(
    """\
    ---
    title: 电磁学
    tags: [物理]
    belongs: [大物物理（甲）II]
    ---

    # 电磁学

    一段正文。
    """
)


def test_accepts_the_display_name_the_user_gave(tmp_path, monkeypatch):
    mind = tmp_path / "mind"
    surface = tmp_path / "surface"
    (mind / "wiki").mkdir(parents=True)
    (surface / "notes" / "物理").mkdir(parents=True)
    (tmp_path / "projects.yaml").write_text(DISPLAY_ROSTER, encoding="utf-8")
    monkeypatch.setenv("MIND_ROOT", str(mind))
    monkeypatch.setenv("HUMAN_ROOT", str(surface))
    monkeypatch.setenv("PLOBI_PROJECTS_CONFIG", str(tmp_path / "projects.yaml"))

    assert validate_note("Notes/物理/电磁学.md", DISPLAY_NOTE, surface / "notes") == []


def test_the_agent_id_still_passes_the_same_gate(tmp_path, monkeypatch):
    """向后兼容：机器键（agent_id）继续认——存量那几篇靠它。"""
    mind = tmp_path / "mind"
    surface = tmp_path / "surface"
    (mind / "wiki").mkdir(parents=True)
    (surface / "notes" / "物理").mkdir(parents=True)
    (tmp_path / "projects.yaml").write_text(DISPLAY_ROSTER, encoding="utf-8")
    monkeypatch.setenv("MIND_ROOT", str(mind))
    monkeypatch.setenv("HUMAN_ROOT", str(surface))
    monkeypatch.setenv("PLOBI_PROJECTS_CONFIG", str(tmp_path / "projects.yaml"))

    by_id = DISPLAY_NOTE.replace("belongs: [大物物理（甲）II]", "belongs: [ii]")

    assert validate_note("Notes/物理/电磁学.md", by_id, surface / "notes") == []
