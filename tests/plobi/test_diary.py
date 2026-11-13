"""日记：文件名、读取、保存与"只提交这一篇"（WP-VAULT-AGENTS-VIEW）。"""

from __future__ import annotations

import subprocess

import pytest

from plobi.mind import diary


@pytest.fixture()
def surface(tmp_path, monkeypatch):
    mind = tmp_path / "mind"
    root = tmp_path / "surface"
    (mind / "wiki").mkdir(parents=True)
    (root / "diary").mkdir(parents=True)
    (root / "notes").mkdir()
    (root / "notes" / "别人的笔记.md").write_text("不要带走我\n", encoding="utf-8")

    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for key, value in (
        ("GIT_AUTHOR_NAME", "Test"),
        ("GIT_AUTHOR_EMAIL", "test@example.com"),
        ("GIT_COMMITTER_NAME", "Test"),
        ("GIT_COMMITTER_EMAIL", "test@example.com"),
    ):
        monkeypatch.setenv(key, value)

    monkeypatch.setenv("MIND_ROOT", str(mind))
    monkeypatch.setenv("HUMAN_ROOT", str(root))
    return root


def test_filename_matches_the_existing_shape():
    day = diary.parse_day("2026-03-28")
    assert diary.filename_for(day) == "2026-03-28-Saturday.md"


def test_rejects_non_iso_dates():
    for bad in ("2026-3-8", "28-03-2026", "2026-02-30", "", "today"):
        with pytest.raises(ValueError):
            diary.parse_day(bad)


def test_missing_entry_reads_as_empty_not_as_error(surface):
    page = diary.read_entry("2026-10-24")
    assert page["exists"] is False
    assert page["text"] == ""
    assert page["filename"] == "2026-10-24-Saturday.md"


def test_save_writes_file_and_commits_only_that_file(surface):
    result = diary.save_entry("2026-10-24", "今天写了一篇。")
    assert result["committed"] is True
    assert result["commit"]

    path = surface / "diary" / "2026-10-24-Saturday.md"
    assert path.read_text(encoding="utf-8") == "今天写了一篇。\n"

    show = subprocess.run(
        ["git", "-C", str(surface), "show", "--name-only", "--format=%s", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    lines = [line for line in show.stdout.splitlines() if line]
    assert lines[0] == "diary: 2026-10-24"
    assert lines[1:] == ["diary/2026-10-24-Saturday.md"]
    # 同仓里别人未提交的笔记没有被带走（未跟踪目录在 porcelain 里显示成 `?? notes/`）
    status = subprocess.run(
        ["git", "-C", str(surface), "status", "--porcelain"], capture_output=True, text=True, check=True
    )
    assert "?? notes/" in status.stdout


def test_saving_again_commits_again(surface):
    diary.save_entry("2026-10-24", "第一版")
    again = diary.save_entry("2026-10-24", "第二版")
    assert again["committed"] is True
    assert (surface / "diary" / "2026-10-24-Saturday.md").read_text(encoding="utf-8") == "第二版\n"


def test_unchanged_text_does_not_make_an_empty_commit(surface):
    diary.save_entry("2026-10-24", "同一篇")
    again = diary.save_entry("2026-10-24", "同一篇")
    assert again["committed"] is False
    assert again["detail"] == "unchanged"


def test_no_commit_when_the_root_is_not_a_repo(tmp_path, monkeypatch):
    mind = tmp_path / "mind"
    root = tmp_path / "plain"
    (mind / "wiki").mkdir(parents=True)
    (root / "diary").mkdir(parents=True)
    monkeypatch.setenv("MIND_ROOT", str(mind))
    monkeypatch.setenv("HUMAN_ROOT", str(root))

    result = diary.save_entry("2026-10-24", "没有 git 也要落盘")
    assert result["committed"] is False
    assert "not a git repository" in result["detail"]
    assert (root / "diary" / "2026-10-24-Saturday.md").exists()


def test_list_entries_is_newest_first(surface):
    diary.save_entry("2026-01-02", "a", commit=False)
    diary.save_entry("2026-03-04", "b", commit=False)
    (surface / "diary" / "乱七八糟.md").write_text("x", encoding="utf-8")

    data = diary.list_entries()
    assert [row["date"] for row in data["entries"]] == ["2026-03-04", "2026-01-02"]
    assert data["entries"][0]["weekday"] == "Wednesday"


def test_capture_appends_a_stamped_line_at_the_end(surface):
    diary.save_entry("2026-10-27", "上午把管线跑通了。", commit=False)

    result = diary.append_entry("2026-10-27", "跟导师吵完，其实我担心的是时间")
    assert result["line"].startswith("- ") and result["line"].endswith("时间")

    text = (surface / "diary" / "2026-10-27-Tuesday.md").read_text(encoding="utf-8")
    assert text.splitlines() == ["上午把管线跑通了。", result["line"]]


def test_capture_on_a_day_with_nothing_yet_creates_the_file(surface):
    result = diary.append_entry("2026-11-02", "第一句")
    text = (surface / "diary" / "2026-11-02-Monday.md").read_text(encoding="utf-8")
    assert text.strip() == result["line"]


def test_capture_refuses_empty_and_multiline(surface):
    with pytest.raises(ValueError):
        diary.append_entry("2026-10-27", "   ")
    with pytest.raises(ValueError):
        diary.append_entry("2026-10-27", "两句\n拼在一起")


def test_saving_the_editor_does_not_eat_a_capture_made_while_it_was_open(surface):
    """用户 10-26 的形状：编辑器开着写，中途用快捷键记了一句，保存不能把那句冲掉。"""
    diary.save_entry("2026-10-27", "早上的原文。", commit=False)
    base = diary.read_entry("2026-10-27")["text"]
    diary.append_entry("2026-10-27", "路过记的一句", commit=False)

    saved = diary.save_entry("2026-10-27", "早上的原文。\n\n下午补了一段。", commit=False, base_text=base)
    assert saved["merged"] == 1

    text = (surface / "diary" / "2026-10-27-Tuesday.md").read_text(encoding="utf-8")
    assert "下午补了一段。" in text
    assert "- " in text and "路过记的一句" in text


def test_without_base_the_save_is_a_plain_overwrite(surface):
    diary.save_entry("2026-10-27", "原文。", commit=False)
    diary.append_entry("2026-10-27", "会被覆盖的那句", commit=False)

    diary.save_entry("2026-10-27", "整篇重写。", commit=False)
    text = (surface / "diary" / "2026-10-27-Tuesday.md").read_text(encoding="utf-8")
    assert text == "整篇重写。\n"
