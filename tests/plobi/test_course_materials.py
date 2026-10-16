"""`WP-COURSE-DATA-MOUTH` 第二手（教材清单）的钉子。

跑法：`bash scripts/run_tests.sh tests/plobi/test_course_materials.py -q`（唯一入口，不传 `--basetemp`）。
钉的是两件事：**读不到要说成读不到**（不许退成"这门没资料"），
**同一门课两种拼法认得同一份目录**（真源那颗，不在这里再写一套归一化）。
"""

from __future__ import annotations

import builtins
from types import SimpleNamespace

from plobi.agenda.course_materials import PLACEHOLDER, READABLE, materials_for_course


class FakeRegistry:
    def __init__(self, entries):
        self._entries = entries

    def live_entries(self):
        return list(self._entries)


def _entry(name, display, path, paths=None):
    return SimpleNamespace(name=name, display_name=display, project_path=path,
                           project_paths=tuple(paths or ((path,) if path else ())),
                           category="courses")


def test_states_are_told_apart(tmp_path, monkeypatch):
    """本地读得到的报 local，一开就报错的报 cloud-placeholder 并带原因；目录不在要说出来。"""
    root = tmp_path / "2026-2027秋冬-电工电子学"
    root.mkdir()
    (root / "电工电子学(第5版).pdf").write_bytes(b"%PDF-1.7 fake")
    (root / "讲义.docx").write_bytes(b"not really a docx")

    real_open = builtins.open

    def fake_open(file, mode="r", *args, **kwargs):
        if "讲义" in str(file):
            raise OSError(22, "Invalid argument")     # 这台 Windows 云占位的真实错形
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", fake_open)
    reg = FakeRegistry([_entry("电工电子学", "电工电子学", str(root))])

    got = materials_for_course("电工电子学", registry=reg)

    assert got["ok"] is True
    by_state = {f["name"]: f["state"] for f in got["files"]}
    assert by_state["电工电子学(第5版).pdf"] == READABLE
    assert by_state["讲义.docx"] == PLACEHOLDER
    assert got["counts"][READABLE] == 1 and got["counts"][PLACEHOLDER] == 1
    assert "读不到" in got["detail"] or "读得到" in got["detail"]

    missing = materials_for_course("电工电子学", registry=FakeRegistry(
        [_entry("电工电子学", "电工电子学", str(tmp_path / "不在这儿"))]))
    assert missing["ok"] is False and "这个目录现在不在" in missing["detail"]


def test_two_spellings_one_folder_and_unbound_says_so(tmp_path):
    """`…Ⅱ`(U+2161) 与 `…II` 认同一门；没写 `project_path` 的给那句人话，不降级成家务。"""
    root = tmp_path / "2026-2027秋冬-大学物理（甲）II"
    root.mkdir()
    (root / "目录.pdf").write_bytes(b"%PDF-1.7")
    reg = FakeRegistry([_entry("l2-course-phya-ii", "大学物理（甲）\u2161", str(root))])

    for asked in ("大学物理（甲）II", "大学物理（甲）\u2161", "2026-2027秋冬-大学物理（甲）II"):
        got = materials_for_course(asked, registry=reg)
        assert got["ok"] is True and got["course"] == "l2-course-phya-ii", asked
        assert [f["state"] for f in got["files"]] == [READABLE]

    unbound = materials_for_course("设计与制造\u2160", registry=FakeRegistry(
        [_entry("l2-course-design", "设计与制造Ⅰ", "")]))
    assert unbound["ok"] is False
    assert "没写 `project_path`" in unbound["detail"] and "家务" in unbound["detail"]

    assert materials_for_course("", registry=reg)["ok"] is False
    assert materials_for_course("没这一门", registry=reg)["ok"] is False
    assert "查过的结果" in materials_for_course("没这一门", registry=reg)["detail"]
