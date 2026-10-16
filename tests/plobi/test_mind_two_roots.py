"""Two roots, three layouts, one resolver (WP-MIND-STRUCTURE-MOVE).

The ruling is that ``Vault/…`` and ``Loom/…`` are *logical* paths: the human-facing
surface moved to a sibling repository of its own (``VAULT_ROOT``), while the brain
gets flattened (``Loom/wiki`` → ``wiki``). This suite pins the resolver itself, the
one guard that stops a writer from resurrecting ``<mind>/Vault`` inside the AI repo,
and the notes view reading the real thing through the split.

Layouts are pinned by what is on disk and what ``VAULT_ROOT`` says — never by a
flag, because the desktop, the backend and the scheduled jobs start in any order.
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from plobi.mind import paths
from plobi.mind.notes_view import known_names, scan_notes
from plobi.mind.paths import (
    Roots,
    UnsafeMindPath,
    is_safe_relative,
    logical_target,
    real_target,
    resolve_roots,
    safe_target,
)

VAULT_LOGICAL = "Vault/notes"
MIND_LOGICAL = "Loom/wiki/concepts"

# ``tests/conftest.py`` swaps MindWriter._commit for a stub on every test; the commit
# leg is exactly what the split-layout assertions below need to see unstubbed.
from plobi.mind.writer import MindWriter as _PristineMindWriter  # noqa: E402

_REAL_MIND_COMMIT = _PristineMindWriter._commit


@pytest.fixture()
def mind(tmp_path):
    root = tmp_path / "mind"
    root.mkdir(parents=True)
    (root / "AGENTS.md").write_text("# Mind", encoding="utf-8")
    return root


@pytest.fixture()
def sibling_vault(tmp_path):
    vault = tmp_path / "vault"
    (vault / "notes").mkdir(parents=True)
    return vault


# --- the resolver itself -----------------------------------------------------


def test_unpinned_pointer_keeps_the_legacy_layout(mind, monkeypatch):
    monkeypatch.delenv("VAULT_ROOT", raising=False)
    assert real_target(VAULT_LOGICAL, mind) == mind / "Vault" / "notes"


def test_pinned_pointer_moves_the_human_surface_out(mind, sibling_vault, monkeypatch):
    monkeypatch.setenv("VAULT_ROOT", str(sibling_vault))
    assert real_target(VAULT_LOGICAL, mind) == sibling_vault / "notes"
    # the brain half is the flattened one: Loom/ is a logical name only
    assert real_target(MIND_LOGICAL, mind) == mind / "wiki" / "concepts"


def test_flattened_brain_drops_the_loom_level(mind, monkeypatch):
    """The flag is the whole switch: False = 旧 Loom 壳，True = 平铺（现在是 True）."""
    monkeypatch.delenv("VAULT_ROOT", raising=False)
    (mind / "Loom" / "wiki" / "concepts").mkdir(parents=True)
    monkeypatch.setattr(paths, "BRAIN_IS_FLATTENED", False)
    assert real_target(MIND_LOGICAL, mind) == mind / "Loom" / "wiki" / "concepts"
    monkeypatch.setattr(paths, "BRAIN_IS_FLATTENED", True)
    assert real_target(MIND_LOGICAL, mind) == mind / "wiki" / "concepts"


def test_an_empty_brain_is_not_mistaken_for_a_flattened_one(mind, monkeypatch):
    """Why the flatten is a declared flag and not read off the disk: inference
    answers "flattened" for every test fake vault and every fresh clone, which
    would relocate AI-authored writes to the brain root without anyone deciding it."""
    monkeypatch.delenv("VAULT_ROOT", raising=False)
    monkeypatch.setattr(paths, "BRAIN_IS_FLATTENED", False)
    assert not (mind / "Loom").exists()
    assert real_target(MIND_LOGICAL, mind) == mind / "Loom" / "wiki" / "concepts"


def test_resolve_roots_reads_the_env_once_and_never_guesses(mind, sibling_vault, monkeypatch):
    monkeypatch.setenv("VAULT_ROOT", str(sibling_vault))
    roots = resolve_roots(mind)
    assert isinstance(roots, Roots) and roots.vault == sibling_vault
    # a pinned pointer that does not exist yet is still the pointer
    monkeypatch.setenv("VAULT_ROOT", str(sibling_vault / "nope"))
    assert resolve_roots(mind).vault == sibling_vault / "nope"


def test_base_for_does_not_touch_the_disk(mind, sibling_vault):
    split = Roots(mind=mind, vault=sibling_vault)
    assert split.base_for("Vault/projects/Plobi") == sibling_vault
    assert split.base_for("Loom/raw") == mind
    assert Roots(mind=mind).base_for("Vault/notes") == mind / "Vault"


# --- the writer's guard ------------------------------------------------------


def test_writer_refuses_to_recreate_the_old_surface(mind, monkeypatch):
    """No ``VAULT_ROOT`` and no ``<mind>/Vault`` means the surface moved; creating
    the directory here would leave a second, unbacked-up copy of the user's notes
    inside the AI repo."""
    monkeypatch.delenv("VAULT_ROOT", raising=False)
    with pytest.raises(UnsafeMindPath, match="VAULT_ROOT"):
        safe_target(mind, "Vault/notes/new.md", creating=True)


def test_writer_still_writes_the_legacy_layout_it_was_given(mind, monkeypatch):
    monkeypatch.delenv("VAULT_ROOT", raising=False)
    (mind / "Vault" / "notes").mkdir(parents=True)
    assert safe_target(mind, "Vault/notes/new.md", creating=True) == (
        mind / "Vault" / "notes" / "new.md"
    )


def test_writer_into_the_sibling_needs_no_existing_directory(mind, sibling_vault, monkeypatch):
    monkeypatch.setenv("VAULT_ROOT", str(sibling_vault))
    assert safe_target(mind, "Vault/notes/物理 Physics/new.md", creating=True) == (
        sibling_vault / "notes" / "物理 Physics" / "new.md"
    )


def test_traversal_still_escapes_nothing(mind, sibling_vault, monkeypatch):
    monkeypatch.setenv("VAULT_ROOT", str(sibling_vault))
    assert not is_safe_relative("Vault/../../etc/passwd")
    with pytest.raises(UnsafeMindPath):
        safe_target(mind, "Vault/../../etc/passwd", creating=True)


# --- the inverse the provider needs ------------------------------------------


def test_logical_target_names_the_zone_a_file_belongs_to(mind, sibling_vault, monkeypatch):
    monkeypatch.setenv("VAULT_ROOT", str(sibling_vault))
    note = sibling_vault / "notes" / "数学 Math" / "极限.md"
    note.parent.mkdir(parents=True)
    note.write_text("---\ntitle: 极限\n---\nbody\n", encoding="utf-8")
    assert logical_target(note, mind) == "Vault/notes/数学 Math/极限.md"
    assert logical_target(mind / "unrelated.md", mind) is None


def test_logical_target_relabels_a_flattened_brain_path(mind, monkeypatch):
    monkeypatch.delenv("VAULT_ROOT", raising=False)
    monkeypatch.setattr(paths, "BRAIN_IS_FLATTENED", True)
    concept = mind / "wiki" / "concepts" / "srtp.md"
    concept.parent.mkdir(parents=True)
    concept.write_text("# x", encoding="utf-8")
    assert logical_target(concept, mind) == "Loom/wiki/concepts/srtp.md"


def test_first_file_in_an_absent_zone_is_still_inside_it(mind, monkeypatch):
    """The zones are defined by the path, not by whether the directory exists yet.

    A ``is_dir()`` guard here made the provider refuse ``sync_turn``'s first export
    write — caught by the existing adapter family, not by this suite.
    """
    monkeypatch.delenv("VAULT_ROOT", raising=False)
    assert not (mind / "raw" / "chat-logs" / "exports").exists()
    assert logical_target(mind / "raw/chat-logs/exports/chat.md", mind) == (
        "Loom/raw/chat-logs/exports/chat.md"
    )


# --- the consumer the user actually sees ---------------------------------------


def test_notes_view_reads_the_split_layout(mind, sibling_vault, monkeypatch):
    monkeypatch.setenv("VAULT_ROOT", str(sibling_vault))
    note = sibling_vault / "notes" / "物理 Physics" / "ch01.md"
    note.parent.mkdir(parents=True)
    note.write_text(
        "---\ntitle: 质点运动学\nbelongs: [大学物理（甲）Ⅱ]\n---\n正文\n", encoding="utf-8"
    )
    scan = scan_notes(mind)
    assert scan.scanned == 1
    entry = scan.notes[0]
    assert entry.path == "Vault/notes/物理 Physics/ch01.md"   # logical, stable for the desktop
    assert Path(entry.fs_path) == note                          # real file, in the other repo


def test_notes_view_reports_a_missing_surface_instead_of_zero_notes(mind, monkeypatch):
    monkeypatch.setenv("VAULT_ROOT", str(mind.parent / "vault-that-is-not-there"))
    scan = scan_notes(mind)
    assert scan.scanned == 0
    assert any("目录不存在" in line for line in scan.skipped)


def test_agent_aliases_still_match_projects_through_the_split(mind, sibling_vault, monkeypatch):
    monkeypatch.setenv("VAULT_ROOT", str(sibling_vault))
    (sibling_vault / "projects" / "SRTP").mkdir(parents=True)
    entry = types.SimpleNamespace(
        name="srtp", mind_subtree="Vault/projects/SRTP", category="research", role="l2_course"
    )
    from plobi.mind.notes_view import agent_aliases

    assert "SRTP" in agent_aliases(entry)
    assert "SRTP" in known_names(mind, [entry])


def test_a_research_tree_is_a_known_owner_too(mind, sibling_vault, monkeypatch):
    """Counting only ``Vault/projects`` would report ``research/SRTP`` as a note
    tagged with an owner nobody registered — the sidebar's dangling-tag card."""
    monkeypatch.setenv("VAULT_ROOT", str(sibling_vault))
    (sibling_vault / "research" / "SRTP").mkdir(parents=True)
    assert "SRTP" in known_names(mind, [])


def test_a_tree_lands_in_its_category_folder_and_a_stale_pointer_adopts_it(
    mind, sibling_vault, monkeypatch
):
    """``category=research`` must not mint a second ``projects/SRTP`` beside the
    real tree. Live shape on 2026-10-16 08:35: the roster still said
    ``Vault/projects/SRTP`` after the move, so registering the 分身 wrote three
    boilerplate files into a brand-new folder on the human surface."""
    from plobi.mind.project_tree import ensure_project_tree

    monkeypatch.setenv("VAULT_ROOT", str(sibling_vault))

    write = ensure_project_tree(raw_name="SRTP", category="research", mind_root=mind)
    assert write.relative == "Vault/research/SRTP"
    assert (sibling_vault / "research" / "SRTP" / "plan.md").is_file()
    assert not (sibling_vault / "projects" / "SRTP").exists()

    adopted = ensure_project_tree(raw_name="SRTP", category="projects", mind_root=mind)
    assert (adopted.reused, adopted.relative) == (True, "Vault/research/SRTP")
    assert not (sibling_vault / "projects" / "SRTP").exists()


def test_the_seeder_reads_trees_from_every_category_folder(
    tmp_path, mind, sibling_vault, monkeypatch
):
    """The read side has to see the same five folders the writer uses, or a tree
    that moved to ``research/`` stops being a candidate and the two halves drift."""
    from plobi.agents import registry as registry_mod

    monkeypatch.setenv("VAULT_ROOT", str(sibling_vault))
    monkeypatch.delenv("PLOBI_PROJECTS_CONFIG", raising=False)
    monkeypatch.setenv("PLOBI_HOME", str(tmp_path / ".plobi"))
    tree = sibling_vault / "research" / "Wealth-Lab"
    tree.mkdir(parents=True)
    (tree / "plan.md").write_text(
        "---\nproject: Wealth-Lab\ntitle: Wealth-Lab\ngroup: research\n"
        "status: active\n---\n\n# Wealth-Lab\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        registry_mod.AgentRegistry,
        "spawn",
        lambda self, name, **kw: {"name": name, "profile_dir": str(tmp_path)},
    )

    reg, seeded = registry_mod.ensure_mind_project_agents(
        registry_mod.AgentRegistry.load(tmp_path / "projects.yaml"), mind_root=mind
    )
    assert seeded, "the tree under research/ was not discovered at all"
    entry = next(e for e in reg.entries() if e.mind_subtree.endswith("Wealth-Lab"))
    assert entry.mind_subtree == "Vault/research/Wealth-Lab"
    assert entry.category == "research"


def _git(repo: Path, *args: str) -> str:
    import subprocess

    completed = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, timeout=60
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return completed.stdout


def _init_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init")
    _git(root, "config", "user.email", "tests@plobi.local")
    _git(root, "config", "user.name", "Plobi Tests")
    (root / "README.md").write_text("# seed\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "seed")
    return root


def test_a_batch_spanning_both_repos_commits_into_the_repo_that_owns_each_file(
    tmp_path, monkeypatch
):
    """The commit leg must follow the file, not the logical name.

    Before this the writer ran ``git add Vault/notes/x.md`` with ``cwd=MIND_ROOT``.
    After the split that path exists only in ``VAULT_ROOT``, so git matched nothing,
    the leg reported ``commit skipped`` — and every AI write sat untracked forever,
    which in turn froze ``sync_mind.py`` on its "dirty ⇒ skip both legs" rule.
    """
    from plobi.mind.writer import MindWriter, WriteRequest

    monkeypatch.setattr(MindWriter, "_commit", _REAL_MIND_COMMIT)
    mind = _init_repo(tmp_path / "mind")
    vault = _init_repo(tmp_path / "vault")
    monkeypatch.setenv("MIND_ROOT", str(mind))
    monkeypatch.setenv("VAULT_ROOT", str(vault))

    result = MindWriter(mind, commit=True, run_verifier=False).write(
        [
            WriteRequest("Vault/notes/ch01.md", "# note\n"),
            WriteRequest("Loom/wiki/concepts/srtp.md", "# concept\n"),
        ]
    )

    assert result.ok, result.detail
    assert "commit skipped" not in result.detail, result.detail

    vault_files = _git(vault, "show", "--name-only", "--pretty=format:")
    mind_files = _git(mind, "show", "--name-only", "--pretty=format:")
    assert "notes/ch01.md" in vault_files, (
        f"detail={result.detail} | vault={vault_files} | "
        f"vault_log={_git(vault, 'log', '--oneline')} | "
        f"vault_status={_git(vault, 'status', '--porcelain')} | "
        f"on_disk={(vault / 'notes' / 'ch01.md').is_file()}"
    )
    assert "Vault/notes/ch01.md" not in vault_files, vault_files
    assert "wiki/concepts/srtp.md" in mind_files, mind_files
    assert _git(vault, "status", "--porcelain").strip() == ""
    assert _git(mind, "status", "--porcelain").strip() == ""
