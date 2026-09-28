"""Platform-aware ``project_path`` defaults + stale-binding self-heal.

Why this is a separate file: the symptom is a *chain*, not one function. The
seed table hardcoded a Windows drive layout, the registry rows carried on
``D:\\Cloud\\Projects\\<name>`` after the machine was scrapped, and
``apply_l2_project_cwd`` then refused to fabricate a cwd — so every project
分身 silently started outside its own directory. These tests pin the four
invariants that close that chain, and none of them asserts a literal path
string (AGENTS.md: behavior contracts over snapshots).

Isolation: every test runs against a temp home + temp ``PLOBI_HOME``. Nothing
here may read or write the real ``~/.plobi``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path, PureWindowsPath

import pytest
import yaml

from plobi.agents import registry as registry_mod
from plobi.agents.registry import (
    AGENT_CATEGORIES,
    POSIX_PROJECTS_ROOT_NAME,
    AgentEntry,
    AgentRegistry,
    build_default_project_paths,
)

# A drive-letter prefix ("D:\..." or "C:/...") — the shape that must never
# reach a non-Windows resolution.
DRIVE_LEADER = re.compile(r"^[A-Za-z]:[\\/]")

# The literal shape the scrapped Windows box left behind in projects.yaml.
# Used as *input* (a path that cannot exist here), never as an expectation.
WINDOWS_LEFTOVER = r"D:\Cloud\Projects\{name}"


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """Temp OS home + temp PLOBI_HOME, with the seed table re-resolved for a
    non-Windows platform under that temp home."""
    real_home = tmp_path / "home"
    real_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: real_home)

    plobi_home = tmp_path / "plobi_home"
    (plobi_home / "plobi").mkdir(parents=True)
    monkeypatch.setenv("PLOBI_HOME", str(plobi_home))
    monkeypatch.setenv(
        "PLOBI_PROJECTS_CONFIG", str(plobi_home / "plobi" / "projects.yaml")
    )
    monkeypatch.delenv("PLOBI_MODELS_CONFIG", raising=False)

    # The live table is built at import time (like every profile-aware module
    # constant); re-resolve it against the temp home so no test can leak a
    # path into the real filesystem.
    monkeypatch.setattr(
        registry_mod,
        "DEFAULT_PROJECT_PATHS",
        build_default_project_paths(home=real_home, is_windows=False),
    )
    return real_home


def _registry_file(tmp_path) -> Path:
    return tmp_path / "plobi_home" / "plobi" / "projects.yaml"


def _write_registry(path: Path, agents: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {"version": 1, "agents": agents}, sort_keys=False, allow_unicode=True
        ),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Invariant 1 — a non-Windows resolution must never carry a drive letter
# ---------------------------------------------------------------------------


def test_non_windows_defaults_carry_no_drive_letter(home):
    """Requirement A: off Windows, no default binding may be a drive path —
    and the roots must be anchored under the user's home, not fabricated
    somewhere else."""
    resolved = build_default_project_paths(home=home, is_windows=False)

    assert set(resolved) == set(AGENT_CATEGORIES)
    projects_root = Path(resolved["projects"])
    for category, value in resolved.items():
        if category == "butler":
            # butler is housekeeping, not project-scoped: no binding at all.
            assert value == ""
            continue
        assert not DRIVE_LEADER.match(value), (category, value)
        assert Path(value).is_absolute(), (category, value)
        assert projects_root in Path(value).parents or Path(value) == projects_root, (
            category,
            value,
        )

    # events / research are sub-directories of the same projects root.
    assert projects_root.name == POSIX_PROJECTS_ROOT_NAME
    assert Path(resolved["events"]).parent == projects_root
    assert Path(resolved["research"]).parent == projects_root
    assert Path(resolved["events"]) != Path(resolved["research"])


def test_live_table_on_this_runner_has_no_drive_letter(home):
    """Same invariant, but through the live accessor the product actually
    uses (``default_project_path``) — not just the pure builder."""
    for category in AGENT_CATEGORIES:
        value = registry_mod.default_project_path(category)
        if not value:
            continue
        if os.name != "nt":
            assert not DRIVE_LEADER.match(value), (category, value)
            assert Path(value).is_absolute(), (category, value)


def test_windows_defaults_keep_their_bindings():
    """Requirement A's other half: don't break the Windows layout. Asserted as
    a relation (drive-anchored, events/research siblings) rather than three
    hardcoded strings, so it stays a contract and not a snapshot."""
    resolved = build_default_project_paths(is_windows=True)

    assert set(resolved) == set(AGENT_CATEGORIES)
    assert resolved["butler"] == ""
    for category in AGENT_CATEGORIES:
        if category == "butler":
            continue
        assert DRIVE_LEADER.match(resolved[category]), (category, resolved[category])
    # Compared as Windows paths so the relation is checked on any runner.
    assert PureWindowsPath(resolved["events"]).parent == PureWindowsPath(
        resolved["research"]
    ).parent


def test_default_project_paths_build_never_creates_directories(home, tmp_path):
    """Resolution is read-only: a binding that does not exist must not be
    materialised on the way out."""
    untouched = tmp_path / "never-made"
    resolved = build_default_project_paths(home=untouched, is_windows=False)
    assert resolved["projects"] == str(untouched / POSIX_PROJECTS_ROOT_NAME)
    assert not (untouched / POSIX_PROJECTS_ROOT_NAME).exists()


# ---------------------------------------------------------------------------
# Invariant 2 — a stale binding whose name matches a real dir is corrected,
# written back, and never corrected twice
# ---------------------------------------------------------------------------


def test_stale_binding_is_rebased_onto_the_real_directory(home, tmp_path):
    """Requirement B: leftover Windows path + a project dir that really exists
    under the platform root → the load fixes the row and persists it."""
    projects_root = home / POSIX_PROJECTS_ROOT_NAME
    real_project = projects_root / "Resonote"
    real_project.mkdir(parents=True)

    path = _registry_file(tmp_path)
    _write_registry(
        path,
        {
            "Resonote": {
                "role": "l2_project",
                "category": "projects",
                "project_path": WINDOWS_LEFTOVER.format(name="Resonote"),
            }
        },
    )

    reg = AgentRegistry.load(path)
    entry = reg.get("Resonote")
    assert entry is not None
    assert Path(entry.project_path) == real_project
    assert Path(entry.project_path).is_dir()

    # persisted, not just in memory
    on_disk = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert on_disk["agents"]["Resonote"]["project_path"] == str(real_project)

    # ... and the corrected binding now actually feeds a cwd, which is the
    # whole point of the slice.
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    assert registry_mod.apply_l2_project_cwd(profile_dir, entry.project_path) is True
    cfg = yaml.safe_load((profile_dir / "config.yaml").read_text(encoding="utf-8"))
    assert cfg["terminal"]["cwd"] == str(real_project)


def test_healed_registry_reloads_unchanged(home, tmp_path):
    """Idempotence: a second load must find every binding live and rewrite
    nothing — byte for byte."""
    projects_root = home / POSIX_PROJECTS_ROOT_NAME
    (projects_root / "Aura").mkdir(parents=True)
    (projects_root / "Kit").mkdir(parents=True)

    path = _registry_file(tmp_path)
    _write_registry(
        path,
        {
            name: {
                "role": "l2_project",
                "category": "projects",
                "project_path": WINDOWS_LEFTOVER.format(name=name),
            }
            for name in ("Aura", "Kit")
        },
    )

    first = AgentRegistry.load(path)
    assert sorted(first.heal_stale_project_paths()) == []  # already healed
    after_first = path.read_text(encoding="utf-8")

    second = AgentRegistry.load(path)
    assert path.read_text(encoding="utf-8") == after_first
    assert second.heal_stale_project_paths(save=False) == []
    for name in ("Aura", "Kit"):
        assert Path(second.get(name).project_path).is_dir()


def test_research_row_resolves_against_the_projects_root(home, tmp_path):
    """The Mind scanner always filed research projects under the projects
    root, so the heal must offer that root too — otherwise a research 分身
    stays bound to nothing."""
    real_project = home / POSIX_PROJECTS_ROOT_NAME / "Wealth-Lab"
    real_project.mkdir(parents=True)

    path = _registry_file(tmp_path)
    _write_registry(
        path,
        {
            "Wealth-Lab": {
                "role": "l2_project",
                "category": "research",
                "project_path": WINDOWS_LEFTOVER.format(name="Wealth-Lab"),
            }
        },
    )

    reg = AgentRegistry.load(path)
    assert Path(reg.get("Wealth-Lab").project_path) == real_project


# ---------------------------------------------------------------------------
# Invariant 3 — an unresolvable stale binding gets no fake cwd and no
#               fabricated directory
# ---------------------------------------------------------------------------


def test_unresolvable_stale_binding_is_never_fabricated(home, tmp_path):
    """Requirement B's fail-closed branch: nothing on this machine matches the
    name → keep the original value, create nothing, and let the cwd write stay
    a no-op. A 分身 with no cwd is honest; one pointed at a made-up directory
    is not."""
    projects_root = home / POSIX_PROJECTS_ROOT_NAME
    projects_root.mkdir(parents=True)

    stale = WINDOWS_LEFTOVER.format(name="Animation")
    path = _registry_file(tmp_path)
    _write_registry(
        path,
        {
            "Animation": {
                "role": "l2_project",
                "category": "projects",
                "project_path": stale,
            }
        },
    )
    before = path.read_text(encoding="utf-8")

    reg = AgentRegistry.load(path)
    entry = reg.get("Animation")
    assert entry.project_path == stale  # kept, not replaced with a guess
    assert not entry.project_path.startswith(str(projects_root))

    # nothing was created anywhere
    assert not (projects_root / "Animation").exists()
    assert [p.name for p in projects_root.iterdir()] == []
    assert not Path(stale).exists()

    # no write-back happened
    assert path.read_text(encoding="utf-8") == before

    # and the downstream cwd write still refuses to invent a workdir
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    assert registry_mod.apply_l2_project_cwd(profile_dir, entry.project_path) is False
    assert not (profile_dir / "config.yaml").exists()


def test_heal_does_not_follow_a_path_traversal_entry_name(home, tmp_path):
    """A hand-edited ``projects.yaml`` is untrusted input: an entry name that
    escapes the default root must not steer the heal outside it."""
    escapee = home / POSIX_PROJECTS_ROOT_NAME / ".." / "outside"
    escapee.parent.mkdir(parents=True, exist_ok=True)
    escapee.mkdir(exist_ok=True)

    entry = AgentEntry(
        name="../outside",
        role="l2_project",
        category="projects",
        project_path=WINDOWS_LEFTOVER.format(name="outside"),
    )
    assert registry_mod.resolve_stale_project_path(entry) == ""


# ---------------------------------------------------------------------------
# Invariant 4 — a live, human-chosen binding is off-limits
# ---------------------------------------------------------------------------


def test_human_set_existing_path_is_left_alone(home, tmp_path):
    """Requirement B: existence beats name-matching. A path a human pointed at
    a real directory is never swapped for the default root, even when a
    same-named directory exists there too."""
    chosen = tmp_path / "elsewhere" / "my-project"
    chosen.mkdir(parents=True)
    # A same-named directory under the default root, i.e. a *tempting* match.
    (home / POSIX_PROJECTS_ROOT_NAME / "Resonote").mkdir(parents=True)

    path = _registry_file(tmp_path)
    _write_registry(
        path,
        {
            "Resonote": {
                "role": "l2_project",
                "category": "projects",
                "project_path": str(chosen),
            }
        },
    )

    reg = AgentRegistry.load(path)
    assert reg.get("Resonote").project_path == str(chosen)
    assert reg.heal_stale_project_paths(save=False) == []


def test_butler_row_with_a_stale_path_is_not_rebased(home, tmp_path):
    """``butler`` has no folder binding by design, so there is no default root
    to re-base it against — the row stays as the human wrote it."""
    stale = WINDOWS_LEFTOVER.format(name="Butler")
    path = _registry_file(tmp_path)
    _write_registry(
        path,
        {
            "butler": {
                "role": "l2_butler",
                "category": "butler",
                "project_path": stale,
            }
        },
    )

    reg = AgentRegistry.load(path)
    assert reg.get("butler").project_path == stale
    assert reg.heal_stale_project_paths(save=False) == []


def test_unbound_row_is_untouched(home, tmp_path):
    """Empty binding (the agenda row's shape) is not "stale" — nothing to
    heal, nothing to warn about."""
    path = _registry_file(tmp_path)
    _write_registry(path, {"agenda": {"role": "l2_agenda", "category": "butler"}})

    before = path.read_text(encoding="utf-8")
    reg = AgentRegistry.load(path)
    assert reg.get("agenda").project_path == ""
    assert path.read_text(encoding="utf-8") == before


def test_seeding_a_new_row_uses_the_platform_root(home, tmp_path):
    """Requirement A at the seed site: a fresh entry with no binding picks up
    the platform root, and no directory is created by the registry."""
    reg = AgentRegistry(path=_registry_file(tmp_path))
    projects_root = home / POSIX_PROJECTS_ROOT_NAME

    entry = reg.upsert(AgentEntry(name="newproj", role="l2_project", category="projects"))
    assert Path(entry.project_path) == projects_root
    # the registry itself must not materialise the binding
    assert not projects_root.exists()
