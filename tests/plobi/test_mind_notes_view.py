"""Notes-view index + route (WP-MIND-NOTES-VIEW).

Shapes this suite is built around, all of them seen in the real surface:
a note whose file starts with a BOM, paths carrying spaces and CJK, notes with no
frontmatter at all, and malformed YAML fences. None of those may turn into a
silent "no notes".
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from plobi.mind.notes_view import (
    MAX_ENTRIES,
    BrainNotConfigured,
    agent_aliases,
    build_view,
    known_names,
    notes_for,
    scan_notes,
    unknown_belongs,
)

NOTE_ROOT = "notes/数学 Math"


def _entry(name: str, subtree: str = "", category: str = "projects") -> types.SimpleNamespace:
    return types.SimpleNamespace(
        name=name, mind_subtree=subtree, category=category, role="l2_project"
    )


@pytest.fixture()
def surface(tmp_path, monkeypatch):
    """脑根 ``mind/`` 与人读面根 ``home/`` 各一处 —— 2026-11-13 起只有这一种布局。"""
    mind = tmp_path / "mind"
    home = tmp_path / "home"
    notes = home / NOTE_ROOT
    notes.mkdir(parents=True)
    (home / "dossiers" / "projects" / "SRTP").mkdir(parents=True)
    mind.mkdir(parents=True, exist_ok=True)
    (mind / "AGENTS.md").write_text("# Mind", encoding="utf-8")
    monkeypatch.setenv("HUMAN_ROOT", str(home))

    (notes / "Rust 所有权.md").write_text(
        "---\ntitle: Rust 所有权\ntype: note\nbelongs: [SRTP]\n---\n\n正文一二三。\n",
        encoding="utf-8",
    )
    # The BOM case: a plain utf-8 read would leave \ufeff before the fence.
    (notes / "BOM 笔记.md").write_text(
        "﻿---\ntitle: 带 BOM 的笔记\nbelongs: [Ghost]\n---\nbody\n", encoding="utf-8"
    )
    (notes / "无 frontmatter.md").write_text("直接一句正文，没有栅栏。\n", encoding="utf-8")
    (notes / "坏 YAML.md").write_text("---\ntitle: ]:\n  - broken\n---\nbody\n", encoding="utf-8")
    return mind


# --- tallying ---------------------------------------------------------------


def test_scan_counts_every_file_not_just_the_readable_ones(surface):
    scan = scan_notes(surface)

    assert scan.scanned == 4
    assert scan.parsed == 2  # the BOM note parses; the other two do not
    assert len(scan.skipped) == 2


def test_missing_note_root_is_reported_not_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("HUMAN_ROOT", str(tmp_path / "empty-home"))
    scan = scan_notes(tmp_path / "mind")

    assert scan.scanned == 0
    assert any("目录不存在" in item for item in scan.skipped)


# --- matching ---------------------------------------------------------------


def test_belongs_matches_through_agent_subtree(surface):
    scan = scan_notes(surface)
    entry = _entry("srtp", "Dossiers/projects/SRTP")

    matched = notes_for(scan, agent_aliases(entry))

    assert [note.title for note in matched] == ["Rust 所有权"]
    assert matched[0].path == "Notes/数学 Math/Rust 所有权.md"
    assert matched[0].chars > 0


def test_payload_carries_an_openable_absolute_path(surface):
    """The desktop hands this to its file-preview normaliser, so it must resolve."""
    payload = build_view(surface, _entry("SRTP"), [_entry("SRTP")])

    note = payload["notes"][0]

    assert Path(note["fsPath"]).is_absolute()
    assert Path(note["fsPath"]).is_file()
    # Display path stays surface-relative — the two are not interchangeable.
    assert not note["path"].startswith("/")
    assert not Path(note["path"]).is_absolute()


def test_unregistered_belongs_is_surfaced_not_dropped(surface):
    scan = scan_notes(surface)
    known = known_names(surface, [_entry("srtp", "Dossiers/projects/SRTP")])

    assert unknown_belongs(scan, known) == ["Ghost"]


def test_no_aliases_matches_nothing(surface):
    assert notes_for(scan_notes(surface), []) == []


# --- budget ----------------------------------------------------------------


def test_entry_cap_reports_what_it_cut(tmp_path, monkeypatch):
    monkeypatch.setenv("HUMAN_ROOT", str(tmp_path))
    notes = tmp_path / "notes"
    notes.mkdir(parents=True)
    for index in range(MAX_ENTRIES + 5):
        (notes / f"n{index}.md").write_text(
            f"---\ntitle: note {index}\nbelongs: [SRTP]\n---\n", encoding="utf-8"
        )
    scan = scan_notes(tmp_path)

    payload = build_view(tmp_path, _entry("SRTP"), [_entry("SRTP")])

    assert payload["totals"]["matched"] == MAX_ENTRIES + 5
    assert len(payload["notes"]) == MAX_ENTRIES
    assert payload["budget"]["truncated"] is True


def test_payload_byte_cap_holds_below_the_declared_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("HUMAN_ROOT", str(tmp_path))
    notes = tmp_path / "notes"
    notes.mkdir(parents=True)
    for index in range(40):
        (notes / f"n{index}.md").write_text(
            "---\ntitle: " + "标" * 1200 + f"\nbelongs: [SRTP]\n---\n", encoding="utf-8"
        )

    payload = build_view(tmp_path, _entry("SRTP"), [_entry("SRTP")])

    assert payload["budget"]["used_bytes"] <= payload["budget"]["max_payload_bytes"]
    assert payload["budget"]["truncated"] is True
    assert len(payload["notes"]) < 40


# --- brain not configured ---------------------------------------------------


def test_unconfigured_brain_raises_instead_of_empty_list():
    with pytest.raises(BrainNotConfigured):
        build_view(None, _entry("SRTP"), [])


# --- route ------------------------------------------------------------------


def test_agent_notes_route_shapes(monkeypatch, surface):
    from plobi.console import router

    registry = types.SimpleNamespace(
        get=lambda agent_id: _entry(agent_id, "Dossiers/projects/SRTP") if agent_id == "srtp" else None,
        live_entries=lambda: [_entry("srtp", "Dossiers/projects/SRTP")],
    )
    monkeypatch.setattr(router, "get_registry", lambda: registry)
    monkeypatch.setattr("plobi.mind.paths.resolve_root", lambda *a, **k: surface)

    payload = router.agent_notes("srtp")

    assert payload is not None
    assert [note["title"] for note in payload["notes"]] == ["Rust 所有权"]
    assert router.agent_notes("ghost") is None


def test_agent_notes_propagates_unconfigured_brain(monkeypatch):
    from plobi.console import router

    registry = types.SimpleNamespace(
        get=lambda agent_id: _entry(agent_id),
        live_entries=lambda: [_entry("srtp")],
    )
    monkeypatch.setattr(router, "get_registry", lambda: registry)
    monkeypatch.setattr("plobi.mind.paths.resolve_root", lambda *a, **k: None)

    with pytest.raises(BrainNotConfigured):
        router.agent_notes("srtp")


# ── 名字那一族（WP-AGENT-NAME-UNIFY，用户 11-12 22:32）───────────────────────
#
# 可见面一律用**用户命名的名字**（名册行的 display_name）；机器键（agent_id）
# 继续认——存量那几篇笔记写的就是机器键，契约一改它们就找不到了。


def test_agent_aliases_lead_with_the_display_name_and_keep_the_agent_id():
    entry = _entry("ii", subtree="Surface/dossiers/courses/大学物理（甲）II")
    entry.display_name = "大物物理（甲）II"

    aliases = agent_aliases(entry)

    assert aliases[0] == "大物物理（甲）II"
    assert "ii" in aliases
    assert "大学物理（甲）II" in aliases


def test_agent_aliases_without_a_display_name_still_work():
    entry = _entry("aura")

    assert agent_aliases(entry) == ("aura",)


def test_known_names_counts_the_display_name_as_a_live_name(surface):
    entry = _entry("ii")
    entry.display_name = "大物物理（甲）II"

    names = known_names(surface, [entry])

    assert "大物物理（甲）II" in names
    assert "ii" in names
