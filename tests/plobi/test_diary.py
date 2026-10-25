"""日记：文件名、读取、保存与"只提交这一篇"（WP-VAULT-AGENTS-VIEW）。"""

from __future__ import annotations

import subprocess

import pytest

from plobi.mind import diary


@pytest.fixture()
def vault(tmp_path, monkeypatch):
    mind = tmp_path / "mind"
    root = tmp_path / "vault"
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
    monkeypatch.setenv("VAULT_ROOT", str(root))
    return root


def test_filename_matches_the_existing_shape():
    day = diary.parse_day("2026-03-28")
    assert diary.filename_for(day) == "2026-03-28-Saturday.md"


def test_rejects_non_iso_dates():
    for bad in ("2026-3-8", "28-03-2026", "2026-02-30", "", "today"):
        with pytest.raises(ValueError):
            diary.parse_day(bad)


def test_missing_entry_reads_as_empty_not_as_error(vault):
    page = diary.read_entry("2026-10-24")
    assert page["exists"] is False
    assert page["text"] == ""
    assert page["filename"] == "2026-10-24-Saturday.md"


def test_save_writes_file_and_commits_only_that_file(vault):
    result = diary.save_entry("2026-10-24", "今天写了一篇。")
    assert result["committed"] is True
    assert result["commit"]

    path = vault / "diary" / "2026-10-24-Saturday.md"
    assert path.read_text(encoding="utf-8") == "今天写了一篇。\n"

    show = subprocess.run(
        ["git", "-C", str(vault), "show", "--name-only", "--format=%s", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    lines = [line for line in show.stdout.splitlines() if line]
    assert lines[0] == "diary: 2026-10-24"
    assert lines[1:] == ["diary/2026-10-24-Saturday.md"]
    # 同仓里别人未提交的笔记没有被带走（未跟踪目录在 porcelain 里显示成 `?? notes/`）
    status = subprocess.run(
        ["git", "-C", str(vault), "status", "--porcelain"], capture_output=True, text=True, check=True
    )
    assert "?? notes/" in status.stdout


def test_saving_again_commits_again(vault):
    diary.save_entry("2026-10-24", "第一版")
    again = diary.save_entry("2026-10-24", "第二版")
    assert again["committed"] is True
    assert (vault / "diary" / "2026-10-24-Saturday.md").read_text(encoding="utf-8") == "第二版\n"


def test_unchanged_text_does_not_make_an_empty_commit(vault):
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
    monkeypatch.setenv("VAULT_ROOT", str(root))

    result = diary.save_entry("2026-10-24", "没有 git 也要落盘")
    assert result["committed"] is False
    assert "not a git repository" in result["detail"]
    assert (root / "diary" / "2026-10-24-Saturday.md").exists()


def test_list_entries_is_newest_first(vault):
    diary.save_entry("2026-01-02", "a", commit=False)
    diary.save_entry("2026-03-04", "b", commit=False)
    (vault / "diary" / "乱七八糟.md").write_text("x", encoding="utf-8")

    data = diary.list_entries()
    assert [row["date"] for row in data["entries"]] == ["2026-03-04", "2026-01-02"]
    assert data["entries"][0]["weekday"] == "Wednesday"
