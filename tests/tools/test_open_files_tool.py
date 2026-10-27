import json
from pathlib import Path

from tools import open_files_tool


def _write_snapshot(root: Path, payload: dict) -> None:
    runtime = root / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / "open-files.json").write_text(json.dumps(payload), encoding="utf-8")


def test_missing_snapshot_says_desktop_may_not_be_open(tmp_path, monkeypatch):
    monkeypatch.setattr(open_files_tool, "get_default_plobi_root", lambda: tmp_path)

    result = json.loads(open_files_tool._handle_list_open_files({}))

    assert result["files"] == []
    assert "desktop" in result["note"].lower()


def test_reports_dirty_text_and_unknown_office(tmp_path, monkeypatch):
    monkeypatch.setattr(open_files_tool, "get_default_plobi_root", lambda: tmp_path)
    _write_snapshot(
        tmp_path,
        {
            "agentId": "l2:coder",
            "files": [
                {"active": True, "dirty": True, "editor": "text", "path": "/w/a.md", "title": "a.md"},
                {"active": False, "dirty": None, "editor": "genoffice", "path": "/w/b.xlsx", "title": "b.xlsx"},
            ],
            "updatedAt": "2999-01-01T00:00:00.000Z",
            "workspace": "/w",
        },
    )

    result = json.loads(open_files_tool._handle_list_open_files({}))

    assert [file["path"] for file in result["files"]] == ["/w/a.md", "/w/b.xlsx"]
    assert result["files"][0]["dirty"] is True
    assert result["files"][1]["dirty"] is None
    assert "unsaved" in result["note"].lower()


def test_reports_office_dirty_when_the_editor_answers(tmp_path, monkeypatch):
    monkeypatch.setattr(open_files_tool, "get_default_plobi_root", lambda: tmp_path)
    _write_snapshot(
        tmp_path,
        {
            "agentId": "l2:coder",
            "files": [
                {"active": True, "dirty": False, "editor": "genoffice", "path": "/w/a.xlsx", "title": "a.xlsx"},
                {"active": False, "dirty": True, "editor": "genoffice", "path": "/w/b.docx", "title": "b.docx"},
            ],
            "updatedAt": "2999-01-01T00:00:00.000Z",
            "workspace": "/w",
        },
    )

    result = json.loads(open_files_tool._handle_list_open_files({}))

    assert [file["dirty"] for file in result["files"]] == [False, True]
    assert "unsaved" in result["note"].lower()


def test_marks_an_old_snapshot_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(open_files_tool, "get_default_plobi_root", lambda: tmp_path)
    _write_snapshot(
        tmp_path,
        {"agentId": "", "files": [], "updatedAt": "2000-01-01T00:00:00.000Z", "workspace": ""},
    )

    result = json.loads(open_files_tool._handle_list_open_files({}))

    assert "older than" in result["note"]