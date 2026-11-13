"""Two roots, three layouts, one resolver (WP-MIND-STRUCTURE-MOVE).

The ruling is that ``Notes/…``／``Diary/…``／``Dossiers/…`` and ``Mind/…`` are
*logical* paths: the human side moved to a sibling repository of its own
(``HUMAN_ROOT``; the old ``SURFACE_ROOT`` was retired on 2026-11-13), while the brain gets
flattened (``Mind/wiki`` → ``wiki``). This suite pins the resolver itself, the one
guard that stops a writer from resurrecting ``<mind>/Surface`` inside the AI repo,
and the notes view reading the real thing through the split.

Layouts are pinned by what is on disk and what ``HUMAN_ROOT`` says — never by a
flag, because the desktop, the backend and the scheduled jobs start in any order.
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from plobi.mind import paths
from plobi.mind.notes_view import known_names, scan_notes
from plobi.mind.paths import (
    MindUnavailable,
    Roots,
    UnsafeMindPath,
    is_safe_relative,
    logical_target,
    real_target,
    resolve_roots,
    safe_target,
)

# 逻辑前缀按种类分家；旧写法 ``Surface/…`` 11-13 起不再解析（留着它专为反断言）。
HUMAN_LOGICAL = "Notes"
LEGACY_LOGICAL = "Surface/notes"
MIND_LOGICAL = "Mind/wiki/concepts"

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
    surface = tmp_path / "surface"
    (surface / "notes").mkdir(parents=True)
    return surface


@pytest.fixture(autouse=True)
def no_machine_registry_pin(monkeypatch):
    """这台机器的注册表里**真钉着**一个指针（人读面的家）。

    不把它按掉，这一档的断言就随「谁在跑测试」变——同一个 env 断言在钉过的机器上
    读注册表那份、在没钉过的机器上读 env 那份，测试就成了机器的快照。
    要测注册表那一支的档，自己再 monkeypatch 一次（见下面那两档）。
    """
    monkeypatch.setattr(paths, "_pinned_human_root", lambda: None)


# --- the resolver itself -----------------------------------------------------


def test_unpinned_pointer_falls_back_to_the_data_home(mind, tmp_path, monkeypatch):
    """没有指针 ⇒ 人读面是**数据家本体**（用户 11-08 16:4x 拍的口径）。

    以前这里回 ``None`` ⇒ 落回老布局 ``<mind>/Surface``——那个目录盘上**不存在**
    （人读面 11-03 起就没有 ``vault/`` 那一层了）⇒ 笔记页与日记页一起报「读不到」，
    迁移脚本也扫不到东西。环境变量是**曾经的门牌**，不是运行的前提。
    """
    monkeypatch.delenv("HUMAN_ROOT", raising=False)
    monkeypatch.delenv("HUMAN_ROOT", raising=False)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PLOBI_HOME", str(home))
    assert real_target(HUMAN_LOGICAL, mind) == home / "notes"


def test_pinned_pointer_moves_the_human_surface_out(mind, sibling_vault, monkeypatch):
    monkeypatch.setenv("HUMAN_ROOT", str(sibling_vault))
    assert real_target(HUMAN_LOGICAL, mind) == sibling_vault / "notes"
    # the brain half is the flattened one: Mind/ is a logical name only
    assert real_target(MIND_LOGICAL, mind) == mind / "wiki" / "concepts"


def test_flattened_brain_drops_the_loom_level(mind, monkeypatch):
    """The flag is the whole switch: False = 旧 Mind 壳，True = 平铺（现在是 True)."""
    monkeypatch.delenv("HUMAN_ROOT", raising=False)
    monkeypatch.delenv("HUMAN_ROOT", raising=False)
    (mind / "Mind" / "wiki" / "concepts").mkdir(parents=True)
    monkeypatch.setattr(paths, "BRAIN_IS_FLATTENED", False)
    assert real_target(MIND_LOGICAL, mind) == mind / "Mind" / "wiki" / "concepts"
    monkeypatch.setattr(paths, "BRAIN_IS_FLATTENED", True)
    assert real_target(MIND_LOGICAL, mind) == mind / "wiki" / "concepts"


def test_an_empty_brain_is_not_mistaken_for_a_flattened_one(mind, monkeypatch):
    """Why the flatten is a declared flag and not read off the disk: inference
    answers "flattened" for every test fake surface and every fresh clone, which
    would relocate AI-authored writes to the brain root without anyone deciding it."""
    monkeypatch.delenv("HUMAN_ROOT", raising=False)
    monkeypatch.delenv("HUMAN_ROOT", raising=False)
    monkeypatch.setattr(paths, "BRAIN_IS_FLATTENED", False)
    assert not (mind / "Mind").exists()
    assert real_target(MIND_LOGICAL, mind) == mind / "Mind" / "wiki" / "concepts"


def test_resolve_roots_reads_the_env_once_and_never_guesses(mind, sibling_vault, monkeypatch):
    monkeypatch.setenv("HUMAN_ROOT", str(sibling_vault))
    roots = resolve_roots(mind)
    assert isinstance(roots, Roots) and roots.human == sibling_vault
    # a pinned pointer that does not exist yet is still the pointer
    monkeypatch.setenv("HUMAN_ROOT", str(sibling_vault / "nope"))
    assert resolve_roots(mind).human == sibling_vault / "nope"


# --- the stale inherited pointer (Windows: 进程在改钉之前就起来了) --------------


def test_a_stale_inherited_pointer_defers_to_the_registry_pin(mind, sibling_vault, monkeypatch):
    """env 里那个家已经不在了，而注册表那份钉的还在 ⇒ 认注册表那份。

    2026-11-03 实测：人读面从 ``<PLOBI_HOME>/surface`` 搬到 ``<PLOBI_HOME>`` 本体后，
    长驻的桌面 app 仍按继承来的旧值找 ``<PLOBI_HOME>/surface/notes`` ⇒ 日记页与笔记页
    同时报「读不到 surface」。跟 ``plobi_constants`` 为 ``PLOBI_HOME`` 做的那件事同形。
    """
    gone = sibling_vault / "surface"          # 搬走之前的那一处
    monkeypatch.setenv("HUMAN_ROOT", str(gone))
    monkeypatch.setattr(paths, "_pinned_human_root", lambda: str(sibling_vault))
    assert resolve_roots(mind).human == sibling_vault
    assert real_target(HUMAN_LOGICAL, mind) == sibling_vault / "notes"


def test_the_env_still_wins_when_its_target_is_there(mind, sibling_vault, monkeypatch):
    """env 指的目录**还在** ⇒ 用 env。用户可能故意临时指到别处，env 是当下的选择。"""
    monkeypatch.setenv("HUMAN_ROOT", str(sibling_vault))
    monkeypatch.setattr(paths, "_pinned_human_root", lambda: str(sibling_vault.parent))
    assert resolve_roots(mind).human == sibling_vault


def test_a_pointer_to_a_surface_not_built_yet_is_still_the_pointer(mind, sibling_vault, monkeypatch):
    """注册表也没钉（或钉的是同一个）⇒ 仍用 env。新装时表面还没建出来，指针照样算数。"""
    monkeypatch.setenv("HUMAN_ROOT", str(sibling_vault / "nope"))
    monkeypatch.setattr(paths, "_pinned_human_root", lambda: None)
    assert resolve_roots(mind).human == sibling_vault / "nope"
    monkeypatch.setattr(paths, "_pinned_human_root", lambda: str(sibling_vault / "nope"))
    assert resolve_roots(mind).human == sibling_vault / "nope"


def test_no_env_at_all_falls_back_to_the_registry_pin(mind, sibling_vault, monkeypatch):
    """长驻进程在改钉之前起来 ⇒ env 里压根没有这一项 ⇒ 读注册表那份钉的。"""
    monkeypatch.delenv("HUMAN_ROOT", raising=False)
    monkeypatch.delenv("HUMAN_ROOT", raising=False)
    monkeypatch.setattr(paths, "_pinned_human_root", lambda: str(sibling_vault))
    assert resolve_roots(mind).human == sibling_vault


def test_base_for_does_not_touch_the_disk(mind, sibling_vault):
    split = Roots(mind=mind, human=sibling_vault)
    assert split.base_for("Dossiers/projects/Plobi") == sibling_vault
    assert split.base_for("Mind/raw") == mind
    # 数据家算不出来 ⇒ 直接抛，绝不回落脑仓（脑里不放人读面）
    with pytest.raises(MindUnavailable):
        Roots(mind=mind).base_for(HUMAN_LOGICAL)


# --- the writer's guard ------------------------------------------------------


def test_writer_never_recreates_the_old_surface_inside_the_brain(mind, tmp_path, monkeypatch):
    """没有指针时写侧落在**数据家**，不在脑仓里造一个 ``<mind>/Surface``。

    那会把用户自己的笔记复制进 AI 仓：第二份、没有备份、还会在屏上读成「有两处」。
    """
    monkeypatch.delenv("HUMAN_ROOT", raising=False)
    monkeypatch.delenv("HUMAN_ROOT", raising=False)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PLOBI_HOME", str(home))
    target = safe_target(mind, "Notes/new.md", creating=True)
    assert target == home / "notes" / "new.md"
    assert not (mind / "Surface").exists()


def test_a_leftover_surface_inside_the_brain_is_never_written_to(
    mind, tmp_path, monkeypatch
):
    """老布局**残留** ⇒ 也不认它（11-13 全清：那只是待搬的旧壳，不是家）。

    以前这一条写的是「还在就照旧认，不静默换家」；现在换家的动作由搬家脚本一次做完，
    读侧不再替它留活口，写侧更不许往里添新文件。
    """
    monkeypatch.delenv("HUMAN_ROOT", raising=False)
    monkeypatch.delenv("HUMAN_ROOT", raising=False)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PLOBI_HOME", str(home))
    monkeypatch.setattr(paths, "_pinned_human_root", lambda: None)
    (mind / "Surface" / "notes").mkdir(parents=True)
    assert safe_target(mind, "Notes/new.md", creating=True) == home / "notes" / "new.md"
    assert not (mind / "Surface" / "notes" / "new.md").exists()


def test_writer_into_the_sibling_needs_no_existing_directory(mind, sibling_vault, monkeypatch):
    monkeypatch.setenv("HUMAN_ROOT", str(sibling_vault))
    assert safe_target(mind, "Notes/物理 Physics/new.md", creating=True) == (
        sibling_vault / "notes" / "物理 Physics" / "new.md"
    )


def test_traversal_still_escapes_nothing(mind, sibling_vault, monkeypatch):
    monkeypatch.setenv("HUMAN_ROOT", str(sibling_vault))
    assert not is_safe_relative("Notes/../../etc/passwd")
    assert not is_safe_relative("Surface/../../etc/passwd")
    with pytest.raises(UnsafeMindPath):
        safe_target(mind, "Notes/../../etc/passwd", creating=True)


# --- the inverse the provider needs ------------------------------------------


def test_logical_target_names_the_zone_a_file_belongs_to(mind, sibling_vault, monkeypatch):
    monkeypatch.setenv("HUMAN_ROOT", str(sibling_vault))
    note = sibling_vault / "notes" / "数学 Math" / "极限.md"
    note.parent.mkdir(parents=True)
    note.write_text("---\ntitle: 极限\n---\nbody\n", encoding="utf-8")
    # 回给屏上与模型的是**新**写法（旧写法只是仍能解析，不再广告）
    assert logical_target(note, mind) == "Notes/数学 Math/极限.md"
    assert logical_target(mind / "unrelated.md", mind) is None


def test_logical_target_relabels_a_flattened_brain_path(mind, monkeypatch):
    monkeypatch.delenv("HUMAN_ROOT", raising=False)
    monkeypatch.delenv("HUMAN_ROOT", raising=False)
    monkeypatch.setattr(paths, "BRAIN_IS_FLATTENED", True)
    concept = mind / "wiki" / "concepts" / "srtp.md"
    concept.parent.mkdir(parents=True)
    concept.write_text("# x", encoding="utf-8")
    assert logical_target(concept, mind) == "Mind/wiki/concepts/srtp.md"


def test_first_file_in_an_absent_zone_is_still_inside_it(mind, monkeypatch):
    """The zones are defined by the path, not by whether the directory exists yet.

    一个 ``is_dir()`` 守卫当年让 ``sync_turn`` 第一次写导出就吃闭门羹（那是旧原料区的形状，
    裁定 198 之后那条腿已经作废）。判据本身照旧要钉：区域由**路径**定义，不由目录在不在定义。
    换一格还活着的区（wiki 的概念层）来钉它。
    """
    monkeypatch.delenv("HUMAN_ROOT", raising=False)
    monkeypatch.delenv("HUMAN_ROOT", raising=False)
    assert not (mind / "wiki" / "concepts").exists()
    assert logical_target(mind / "wiki" / "concepts" / "chat.md", mind) == (
        "Mind/wiki/concepts/chat.md"
    )


# --- the consumer the user actually sees ---------------------------------------


def test_notes_view_reads_the_split_layout(mind, sibling_vault, monkeypatch):
    monkeypatch.setenv("HUMAN_ROOT", str(sibling_vault))
    note = sibling_vault / "notes" / "物理 Physics" / "ch01.md"
    note.parent.mkdir(parents=True)
    note.write_text(
        "---\ntitle: 质点运动学\nbelongs: [大学物理（甲）Ⅱ]\n---\n正文\n", encoding="utf-8"
    )
    scan = scan_notes(mind)
    assert scan.scanned == 1
    entry = scan.notes[0]
    assert entry.path == "Notes/物理 Physics/ch01.md"   # logical, stable for the desktop
    assert Path(entry.fs_path) == note                  # real file, in the other repo


def test_notes_view_reports_a_missing_surface_instead_of_zero_notes(mind, monkeypatch):
    monkeypatch.setenv("HUMAN_ROOT", str(mind.parent / "surface-that-is-not-there"))
    scan = scan_notes(mind)
    assert scan.scanned == 0
    assert any("目录不存在" in line for line in scan.skipped)


def test_agent_aliases_still_match_projects_through_the_split(mind, sibling_vault, monkeypatch):
    monkeypatch.setenv("HUMAN_ROOT", str(sibling_vault))
    (sibling_vault / "dossiers" / "projects" / "SRTP").mkdir(parents=True)
    entry = types.SimpleNamespace(
        name="srtp", mind_subtree="Dossiers/projects/SRTP", category="research", role="l2_course"
    )
    from plobi.mind.notes_view import agent_aliases

    assert "SRTP" in agent_aliases(entry)
    assert "SRTP" in known_names(mind, [entry])


def test_a_research_tree_is_a_known_owner_too(mind, sibling_vault, monkeypatch):
    """Counting only ``Dossiers/projects`` would report ``research/SRTP`` as a note
    tagged with an owner nobody registered — the sidebar's dangling-tag card."""
    monkeypatch.setenv("HUMAN_ROOT", str(sibling_vault))
    (sibling_vault / "dossiers" / "research" / "SRTP").mkdir(parents=True)
    assert "SRTP" in known_names(mind, [])


def test_a_tree_lands_in_its_category_folder_and_a_stale_pointer_adopts_it(
    mind, sibling_vault, monkeypatch
):
    """``category=research`` must not mint a second ``projects/SRTP`` beside the
    real tree. Live shape on 2026-10-16 08:35: the roster still said
    ``Dossiers/projects/SRTP`` after the move, so registering the 分身 wrote three
    boilerplate files into a brand-new folder on the human surface."""
    from plobi.mind.project_tree import ensure_project_tree

    monkeypatch.setenv("HUMAN_ROOT", str(sibling_vault))

    write = ensure_project_tree(raw_name="SRTP", category="research", mind_root=mind)
    assert write.relative == "Dossiers/research/SRTP"
    assert (sibling_vault / "dossiers" / "research" / "SRTP" / "dossier.json").is_file()
    assert not (sibling_vault / "dossiers" / "projects" / "SRTP").exists()

    adopted = ensure_project_tree(raw_name="SRTP", category="projects", mind_root=mind)
    assert (adopted.reused, adopted.relative) == (True, "Dossiers/research/SRTP")
    assert not (sibling_vault / "dossiers" / "projects" / "SRTP").exists()


def test_the_seeder_reads_trees_from_every_category_folder(
    tmp_path, mind, sibling_vault, monkeypatch
):
    """The read side has to see the same five folders the writer uses, or a tree
    that moved to ``research/`` stops being a candidate and the two halves drift."""
    from plobi.agents import registry as registry_mod

    monkeypatch.setenv("HUMAN_ROOT", str(sibling_vault))
    monkeypatch.delenv("PLOBI_PROJECTS_CONFIG", raising=False)
    monkeypatch.setenv("PLOBI_HOME", str(tmp_path / ".plobi"))
    tree = sibling_vault / "dossiers" / "research" / "Wealth-Lab"
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
    assert entry.mind_subtree == "Dossiers/research/Wealth-Lab"
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

    Before this the writer ran ``git add Surface/notes/x.md`` with ``cwd=MIND_ROOT``.
    After the split that path exists only in ``HUMAN_ROOT``, so git matched nothing,
    the leg reported ``commit skipped`` — and every AI write sat untracked forever,
    which in turn froze ``sync_mind.py`` on its "dirty ⇒ skip both legs" rule.
    """
    from plobi.mind.writer import MindWriter, WriteRequest

    monkeypatch.setattr(MindWriter, "_commit", _REAL_MIND_COMMIT)
    mind = _init_repo(tmp_path / "mind")
    surface = _init_repo(tmp_path / "surface")
    monkeypatch.setenv("MIND_ROOT", str(mind))
    monkeypatch.setenv("HUMAN_ROOT", str(surface))

    result = MindWriter(
        mind, commit=True, run_verifier=False, require_approval=False
    ).write(
        [
            WriteRequest("Notes/ch01.md", "# note\n"),
            WriteRequest("Mind/wiki/concepts/srtp.md", "---\ntype: concept\n---\n\n# concept\n"),
        ]
    )

    assert result.ok, result.detail
    assert "commit skipped" not in result.detail, result.detail

    surface_files = _git(surface, "show", "--name-only", "--pretty=format:")
    mind_files = _git(mind, "show", "--name-only", "--pretty=format:")
    assert "notes/ch01.md" in surface_files, (
        f"detail={result.detail} | surface={surface_files} | "
        f"surface_log={_git(surface, 'log', '--oneline')} | "
        f"surface_status={_git(surface, 'status', '--porcelain')} | "
        f"on_disk={(surface / 'notes' / 'ch01.md').is_file()}"
    )
    assert "Notes/ch01.md" not in surface_files, surface_files
    assert "wiki/concepts/srtp.md" in mind_files, mind_files
    assert _git(surface, "status", "--porcelain").strip() == ""
    assert _git(mind, "status", "--porcelain").strip() == ""


# --- 旧名与旧写法已停用（11-13「全清，不留过渡态」） --------------------------


def test_the_old_pointer_name_is_no_longer_read(mind, tmp_path, monkeypatch):
    """只钉旧的 ``SURFACE_ROOT`` ⇒ **不认**（11-13 全清，旧指针名停用）。"""
    monkeypatch.delenv("HUMAN_ROOT", raising=False)
    monkeypatch.setattr(paths, "_pinned_human_root", lambda: None)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PLOBI_HOME", str(home))
    monkeypatch.setenv("SURFACE_ROOT", str(tmp_path / "surface"))
    assert resolve_roots(mind).human == home
    assert real_target(HUMAN_LOGICAL, mind) == home / "notes"


def test_the_old_logical_prefix_is_no_longer_translated(mind, sibling_vault, monkeypatch):
    """旧写法 ``Surface/notes/…`` **不再**被翻译成 ``Notes/…``（11-13 全清）。

    存量路径得重新指认；它只会被当成脑内一格（``<mind>/Surface/notes``），既不在
    安全表里，也不会指到用户的笔记上。
    """
    monkeypatch.setenv("HUMAN_ROOT", str(sibling_vault))
    assert is_safe_relative(LEGACY_LOGICAL + "/x.md") is False
    assert real_target(LEGACY_LOGICAL, mind) != real_target(HUMAN_LOGICAL, mind)
    assert real_target(LEGACY_LOGICAL, mind) == mind / LEGACY_LOGICAL
    assert real_target("Surface/diary/2026-11-10-Tuesday.md", mind) != (
        sibling_vault / "diary" / "2026-11-10-Tuesday.md"
    )
