"""WP-WORKSPACE-CONTEXT：把工作区打开文件快照注入成简洁上下文块。

纪律：快照是只写不读的桌面产物，读它必须容错（缺失/坏 JSON/结构不对一律静默）；
只报元数据、不读文件内容；激活文件排最前。
"""

import json

from agent.workspace_context import (
    format_workspace_context,
    load_open_files_snapshot,
)


def _snapshot(files, **extra):
    data = {"agentId": "l1", "files": files, "workspace": "D:\\work", **extra}
    return data


def test_missing_snapshot_returns_none(tmp_path):
    assert load_open_files_snapshot(tmp_path) is None


def test_malformed_json_returns_none(tmp_path):
    (tmp_path / "runtime").mkdir()
    (tmp_path / "runtime" / "open-files.json").write_text("{not json", encoding="utf-8")
    assert load_open_files_snapshot(tmp_path) is None


def test_top_level_list_returns_none(tmp_path):
    (tmp_path / "runtime").mkdir()
    (tmp_path / "runtime" / "open-files.json").write_text("[]", encoding="utf-8")
    assert load_open_files_snapshot(tmp_path) is None


def test_valid_snapshot_loads(tmp_path):
    (tmp_path / "runtime").mkdir()
    payload = _snapshot([{"active": True, "path": "D:\\a.py", "editor": "text", "dirty": False}])
    (tmp_path / "runtime" / "open-files.json").write_text(json.dumps(payload), encoding="utf-8")
    assert load_open_files_snapshot(tmp_path) == payload


def test_no_files_is_empty_string():
    assert format_workspace_context(_snapshot([])) == ""
    assert format_workspace_context(None) == ""
    assert format_workspace_context({"files": "not-a-list"}) == ""


def test_active_file_comes_first_with_flags():
    snap = _snapshot(
        [
            {"active": False, "path": "D:\\second.py", "editor": "text", "dirty": False},
            {"active": True, "path": "D:\\first.docx", "editor": "genoffice", "dirty": True},
        ]
    )
    text = format_workspace_context(snap)
    assert text.index("D:\\first.docx") < text.index("D:\\second.py")
    assert "active" in text
    assert "unsaved" in text
    assert "genoffice" in text


def test_files_without_path_are_dropped():
    snap = _snapshot(
        [
            {"active": True, "title": "no path"},
            {"active": False, "path": "D:\\real.py", "editor": "text", "dirty": False},
        ]
    )
    text = format_workspace_context(snap)
    assert "no path" not in text
    assert "D:\\real.py" in text


def test_extra_files_are_counted_not_listed():
    files = [
        {"active": i == 0, "path": f"D:\\f{i}.py", "editor": "text", "dirty": False}
        for i in range(12)
    ]
    text = format_workspace_context(_snapshot(files), max_files=3)
    assert "D:\\f0.py" in text
    assert "D:\\f3.py" not in text
    assert "and 9 more" in text


def test_no_content_of_file_is_injected():
    """只报元数据，不读/不注入文件内容。"""

    snap = _snapshot([{"active": True, "path": "D:\\secret.py", "editor": "text", "dirty": False}])
    text = format_workspace_context(snap)
    assert "secret.py" in text
    assert "def " not in text
