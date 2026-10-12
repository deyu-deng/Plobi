"""Notes-view index + route (WP-MIND-NOTES-VIEW).

Shapes this suite is built around, all of them seen in the real vault:
a note whose file starts with a BOM, paths carrying spaces and CJK, notes with no
frontmatter at all, and malformed YAML fences. None of those may turn into a
silent "no notes".
"""

from __future__ import annotations

import types

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

NOTE_ROOT = "Vault/notes/数学 Math"


def _entry(name: str, subtree: str = "", category: str = "projects") -> types.SimpleNamespace:
    return types.SimpleNamespace(
        name=name, mind_subtree=subtree, category=category, role="l2_project"
    )


@pytest.fixture()
def vault(tmp_path):
    root = tmp_path / "Mind"
    notes = root / NOTE_ROOT
    notes.mkdir(parents=True)
    (root / "Vault" / "projects" / "SRTP").mkdir(parents=True)
    (root / "AGENTS.md").write_text("# Mind", encoding="utf-8")

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
    return root


# --- tallying ---------------------------------------------------------------


def test_scan_counts_every_file_not_just_the_readable_ones(vault):
    scan = scan_notes(vault)

    assert scan.scanned == 4
    assert scan.parsed == 2  # the BOM note parses; the other two do not
    assert len(scan.skipped) == 2


def test_missing_note_root_is_reported_not_empty(tmp_path):
    scan = scan_notes(tmp_path)

    assert scan.scanned == 0
    assert any("目录不存在" in item for item in scan.skipped)


# --- matching ---------------------------------------------------------------


def test_belongs_matches_through_agent_subtree(vault):
    scan = scan_notes(vault)
    entry = _entry("srtp", "Vault/projects/SRTP")

    matched = notes_for(scan, agent_aliases(entry))

    assert [note.title for note in matched] == ["Rust 所有权"]
    assert matched[0].path == "Vault/notes/数学 Math/Rust 所有权.md"
    assert matched[0].chars > 0


def test_unregistered_belongs_is_surfaced_not_dropped(vault):
    scan = scan_notes(vault)
    known = known_names(vault, [_entry("srtp", "Vault/projects/SRTP")])

    assert unknown_belongs(scan, known) == ["Ghost"]


def test_no_aliases_matches_nothing(vault):
    assert notes_for(scan_notes(vault), []) == []


# --- budget ----------------------------------------------------------------


def test_entry_cap_reports_what_it_cut(tmp_path):
    notes = tmp_path / "Vault" / "notes"
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


def test_payload_byte_cap_holds_below_the_declared_limit(tmp_path):
    notes = tmp_path / "Vault" / "notes"
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


def test_agent_notes_route_shapes(monkeypatch, vault):
    from plobi.console import router

    registry = types.SimpleNamespace(
        get=lambda agent_id: _entry(agent_id, "Vault/projects/SRTP") if agent_id == "srtp" else None,
        live_entries=lambda: [_entry("srtp", "Vault/projects/SRTP")],
    )
    monkeypatch.setattr(router, "get_registry", lambda: registry)
    monkeypatch.setattr("plobi.mind.paths.resolve_root", lambda *a, **k: vault)

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
