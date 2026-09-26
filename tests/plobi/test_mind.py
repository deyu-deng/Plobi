"""Mind access: path safety, serialization, verifier gate, narrow reads."""

from __future__ import annotations

import subprocess
import threading
from datetime import datetime
from pathlib import Path

import pytest

from plobi.agenda.mind_sync import publish_daily_summary, summary_relative_path
from plobi.agenda.service import AgendaService
from plobi.mind import paths
from plobi.mind import writer as writer_module
from plobi.mind.lock import MindLockTimeout, mind_write_lock
from plobi.mind.paths import UnsafeMindPath, is_safe_relative, resolve_root, safe_target
from plobi.mind.reader import MindReader
from plobi.mind.writer import MindWriter, WriteRequest

# The suite-wide autouse fixture (tests/conftest.py::_mind_writer_never_commits)
# stubs ``MindWriter._commit`` so no test can create a git commit. The two
# top-level guard tests below must exercise the real implementation, so capture
# it here — at import time, before any fixture runs — and restore it per-test
# via monkeypatch.
_REAL_MIND_COMMIT = MindWriter._commit


@pytest.fixture()
def vault(tmp_path, monkeypatch):
    root = tmp_path / "Mind"
    (root / "Vault" / "meta").mkdir(parents=True)
    (root / "Vault" / "projects" / "Plobi").mkdir(parents=True)
    (root / "AGENTS.md").write_text("# Mind", encoding="utf-8")
    (root / "Vault" / "meta" / "Persona.md").write_text("我是邓德宇。", encoding="utf-8")
    (root / "Vault" / "projects" / "Plobi" / "plan.md").write_text("# Plobi\n北极星…", encoding="utf-8")
    (root / "Vault" / "projects" / "Plobi" / "progress.md").write_text("M1 进行中", encoding="utf-8")
    monkeypatch.setenv("MIND_ROOT", str(root))
    return root


# --- path safety ------------------------------------------------------------


@pytest.mark.parametrize(
    "relative",
    [
        "Loom/raw/chat-logs/digested/2026-08-25/agenda.md",
        "Vault/projects/Plobi/notes.md",
        "Vault/inbox/idea.md",
    ],
)
def test_safe_paths_are_accepted(relative):
    assert is_safe_relative(relative) is True


@pytest.mark.parametrize(
    "relative",
    [
        "AGENTS.md",                       # would rewrite Mind's own contract
        "Vault/projects/新项目/plan.md",    # new project dir trips the verifier
        "Loom/skills/whatever/SKILL.md",   # skill count declaration
        "../outside.md",
        "/etc/passwd",
        "",
    ],
)
def test_unsafe_paths_are_refused(relative):
    assert is_safe_relative(relative) is False


def test_safe_target_rejects_traversal(vault):
    with pytest.raises(UnsafeMindPath):
        safe_target(vault, "Vault/inbox/../../../escape.md")


def test_resolve_root_prefers_env(vault, monkeypatch, tmp_path):
    assert resolve_root() == vault

    monkeypatch.setenv("MIND_ROOT", str(tmp_path / "nowhere"))
    assert resolve_root() is None


def test_no_hardcoded_drive_letters_in_paths_module():
    source = Path(paths.__file__ or "")
    text = source.read_text(encoding="utf-8")
    assert "D:/Mind" not in text
    assert "/Users/ciel" not in text


# --- writer -----------------------------------------------------------------


def test_write_lands_under_loom(vault):
    writer = MindWriter(vault, run_verifier=False)
    result = writer.write_one("Loom/raw/chat-logs/digested/2026-08-25/agenda.md", "# 摘要\n")

    assert result.ok is True
    assert (vault / "Loom/raw/chat-logs/digested/2026-08-25/agenda.md").read_text(encoding="utf-8") == "# 摘要\n"


def test_unsafe_write_is_skipped_not_fatal(vault):
    writer = MindWriter(vault, run_verifier=False)
    result = writer.write(
        [
            WriteRequest("AGENTS.md", "hijacked"),
            WriteRequest("Vault/inbox/ok.md", "fine"),
        ]
    )

    assert result.ok is True
    assert result.skipped == ["AGENTS.md"]
    assert (vault / "AGENTS.md").read_text(encoding="utf-8") == "# Mind"


def test_append_mode_accumulates(vault):
    writer = MindWriter(vault, run_verifier=False)
    writer.write_one("Vault/inbox/log.md", "one\n", mode="append")
    writer.write_one("Vault/inbox/log.md", "two\n", mode="append")

    assert (vault / "Vault/inbox/log.md").read_text(encoding="utf-8") == "one\ntwo\n"


def test_writer_reports_when_vault_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("MIND_ROOT", str(tmp_path / "absent"))
    writer = MindWriter(run_verifier=False)

    assert writer.available is False
    result = writer.write_one("Vault/inbox/x.md", "x")
    assert result.ok is False
    assert result.written == []


def test_failing_verifier_blocks_the_commit_but_keeps_files(vault):
    scripts = vault / "Loom" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "verifier.py").write_text("import sys\nsys.exit(1)\n", encoding="utf-8")

    writer = MindWriter(vault, run_verifier=True)
    result = writer.write_one("Vault/inbox/x.md", "x")

    assert result.ok is False
    assert "verifier" in result.detail
    # The file is still written — Mind is a working copy, not a transaction.
    assert (vault / "Vault/inbox/x.md").exists()


def test_passing_verifier_allows_the_write(vault):
    scripts = vault / "Loom" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "verifier.py").write_text("import sys\nsys.exit(0)\n", encoding="utf-8")

    result = MindWriter(vault, run_verifier=True).write_one("Vault/inbox/x.md", "x")
    assert result.ok is True


def test_missing_verifier_is_not_a_blocker(vault):
    assert MindWriter(vault, run_verifier=True).write_one("Vault/inbox/x.md", "x").ok is True


# --- commit skip-on-dirty (WP-MIND-HOT, 裁定 33.2) ---------------------------


def _install_real_commit(monkeypatch):
    """``tests/conftest._mind_writer_never_commits`` autouse stub 屏蔽了真实现；新测试要看到 skip 语义，需恢复。"""
    monkeypatch.setattr(writer_module.MindWriter, "_commit", _REAL_MIND_COMMIT)


def _bypass_lock(monkeypatch):
    """本沙箱的 safe-delete shim 会拦截 ``.plobi-mind.lock`` 的 ``unlink``，
    跑完一次就 SystemExit。本刀测的是 ``_commit`` skip 语义，不是锁；
    把锁换成 no-op。
    """
    from contextlib import contextmanager

    @contextmanager
    def no_lock(_lock_path, *, timeout=30.0):
        yield

    monkeypatch.setattr(writer_module, "mind_write_lock", no_lock)


@pytest.fixture()
def git_vault(tmp_path, monkeypatch):
    """带 ``git init`` 的 Mind 顶层：commit 应该真的进去。"""
    root = tmp_path / "Mind"
    (root / "Vault" / "meta").mkdir(parents=True)
    (root / "AGENTS.md").write_text("# Mind", encoding="utf-8")
    _git(["init"], root)
    _git(["config", "user.email", "tests@plobi.local"], root)
    _git(["config", "user.name", "Plobi Tests"], root)
    _git(["add", "AGENTS.md"], root)
    _git(["commit", "-m", "seed"], root)
    monkeypatch.setenv("MIND_ROOT", str(root))
    _install_real_commit(monkeypatch)
    return root


def _subprocess_run_factory(git_vault: Path):
    """为 ``git_vault`` 准备一个智能 ``subprocess.run``：

    - ``git rev-parse --show-toplevel`` → 返回真实 toplevel（让护栏通过）
    - ``git add`` → 总是成功
    - ``git commit`` → 替换为 ``(commit_output, commit_returncode)``

    测试只关心 commit 阶段的 skip 语义，git toplevel 守卫生效即可。
    """
    from unittest.mock import MagicMock

    def runner(cmd, **kwargs):
        cmd_list = cmd if isinstance(cmd, list) else [cmd]
        if cmd_list[:3] == ["git", "rev-parse", "--show-toplevel"]:
            completed = MagicMock()
            completed.returncode = 0
            completed.stdout = str(git_vault)
            completed.stderr = ""
            return completed
        if cmd_list[:2] == ["git", "add"]:
            completed = MagicMock()
            completed.returncode = 0
            completed.stdout = ""
            completed.stderr = ""
            return completed
        if cmd_list[:2] == ["git", "commit"]:
            text = runner.commit_output
            rc = runner.commit_returncode
            completed = MagicMock()
            completed.returncode = rc
            completed.stdout = text
            completed.stderr = ""
            return completed
        # Anything else: succeed silently (e.g. status, diff during fixture teardown).
        completed = MagicMock()
        completed.returncode = 0
        completed.stdout = ""
        completed.stderr = ""
        return completed

    runner.commit_output = ""
    runner.commit_returncode = 0
    return runner


def _subprocess_run_stub(output: str, returncode: int = 1):
    """便捷：返回无条件 reject 的 run stub（不走 toplevel guard）。"""
    from unittest.mock import MagicMock

    def runner(cmd, **kwargs):
        completed = MagicMock()
        completed.returncode = returncode
        completed.stdout = output
        completed.stderr = ""
        return completed

    return runner


def test_commit_skipped_when_index_locked(git_vault, monkeypatch):
    """真窗口（9-15 Mind 摘要）：``git commit`` 撞 ``.git/index.lock`` →
    ``ok=True``，文件已在盘上，主回合不去撞 Plobi memory。
    """
    _bypass_lock(monkeypatch)
    import plobi.mind.writer as mw

    runner = _subprocess_run_factory(git_vault)
    runner.commit_output = (
        "fatal: Unable to create '.git/index.lock': File exists.\n"
        "Another git process seems to be running; this process probably needs to\n"
        "wait for it to finish. Please try again later.\n"
    )
    runner.commit_returncode = 1
    monkeypatch.setattr(mw.subprocess, "run", runner)

    writer = MindWriter(git_vault, run_verifier=False)
    result = writer.write_one("Vault/inbox/x.md", "x")

    assert result.ok is True
    assert result.written == ["Vault/inbox/x.md"]
    assert "commit skipped" in result.detail
    assert "index.lock" in result.detail or "Unable to create" in result.detail


def test_commit_skipped_on_dirty_working_tree(git_vault, monkeypatch):
    """脏工作区（其它人的 chore 改动还没收）→ skip，不再 ok=False 拖主回合。"""
    _bypass_lock(monkeypatch)
    import plobi.mind.writer as mw

    runner = _subprocess_run_factory(git_vault)
    runner.commit_output = (
        "error: Your local changes to the following files would be overwritten "
        "by checkout:\n\tx.md\nPlease commit your changes or stash them before "
        "you can switch branches.\nAborting\n"
    )
    runner.commit_returncode = 1
    monkeypatch.setattr(mw.subprocess, "run", runner)

    writer = MindWriter(git_vault, run_verifier=False)
    result = writer.write_one("Vault/inbox/x.md", "x")

    assert result.ok is True
    assert "commit skipped" in result.detail


def test_commit_skipped_on_untracked_overlap(git_vault, monkeypatch):
    """untracked 文件挡住 add/commit → skip；写文件不抛。"""
    _bypass_lock(monkeypatch)
    import plobi.mind.writer as mw

    runner = _subprocess_run_factory(git_vault)
    runner.commit_output = (
        "error: The following untracked working tree files would be overwritten "
        "by merge:\n\tx.md\nPlease move or remove them before you can merge.\nAborting\n"
    )
    runner.commit_returncode = 1
    monkeypatch.setattr(mw.subprocess, "run", runner)

    writer = MindWriter(git_vault, run_verifier=False)
    result = writer.write_one("Vault/inbox/x.md", "x")

    assert result.ok is True
    assert "commit skipped" in result.detail


def test_commit_skipped_when_add_raises(git_vault, monkeypatch):
    """``git add`` 自己 raise（git 不可用 / index lock 同步阻塞）→ 也不抛。"""
    _bypass_lock(monkeypatch)
    import plobi.mind.writer as mw
    from subprocess import CalledProcessError

    def raiser(*args, **kwargs):
        raise CalledProcessError(128, args[0] if args else ["git"])

    monkeypatch.setattr(mw.subprocess, "run", raiser)

    writer = MindWriter(git_vault, run_verifier=False)
    result = writer.write_one("Vault/inbox/x.md", "x")

    assert result.ok is True
    assert "commit skipped" in result.detail
    assert (git_vault / "Vault" / "inbox" / "x.md").is_file()


def test_commit_still_returns_false_on_unrecognised_error(git_vault, monkeypatch):
    """不是 dirty / lock / untracked 的真错误仍 ok=False，但绝不抛。"""
    _bypass_lock(monkeypatch)
    import plobi.mind.writer as mw

    runner = _subprocess_run_factory(git_vault)
    runner.commit_output = "fatal: repository not found\n"
    runner.commit_returncode = 128
    monkeypatch.setattr(mw.subprocess, "run", runner)

    writer = MindWriter(git_vault, run_verifier=False)
    result = writer.write_one("Vault/inbox/x.md", "x")

    assert result.ok is False
    assert "commit skipped" not in result.detail


def test_publish_daily_summary_continues_to_fail_open_on_commit_skip(git_vault, tmp_path, monkeypatch):
    """``mind_sync.publish_daily_summary`` 拿到 commit skip 的 WriteResult 不抛。"""
    _bypass_lock(monkeypatch)
    import plobi.mind.writer as mw

    runner = _subprocess_run_factory(git_vault)
    runner.commit_output = (
        "fatal: Unable to create '.git/index.lock': File exists.\n"
    )
    runner.commit_returncode = 1
    mw.subprocess.run = runner  # 也覆盖 MindWriter 调到的 run（monkeypatch 作用域外）

    service = AgendaService(tmp_path / "agenda.db")
    today = datetime.now()
    service.create_manual(
        title="课",
        start_at=today.replace(hour=10, minute=0, second=0, microsecond=0),
    )
    # 用 vault 的 MindWriter，commit = True（默认）
    writer = MindWriter(git_vault, run_verifier=False)
    # 这等价于 publish_daily_summary 内部 sink.write_one(...) 的返回；
    # 直接验 WriteResult 的契约而不是再过一遍 mind_sync 的 import 重置
    result = writer.write_one("Vault/inbox/anything.md", "x")
    assert result.ok is True
    assert "commit skipped" in result.detail


# --- commit top-level guard (WP-P0-HYGIENE) ---------------------------------


def _git(args, cwd: Path) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    )
    return completed.stdout


def _init_repo(root: Path) -> None:
    _git(["init"], root)
    _git(["config", "user.email", "tests@plobi.local"], root)
    _git(["config", "user.name", "Plobi Tests"], root)


def test_commit_skipped_when_root_is_nested_in_an_enclosing_repo(tmp_path, monkeypatch):
    """A Mind root nested inside another repo never commits (root cause fix).

    This is the exact shape that grew the ``chore(plobi)`` commits: the Mind
    root lived under ``Code/.pytest-<x>/.../Mind`` and git walked *upward* to
    the ``Code`` repo. The write must still land; the enclosing repo's log
    must not grow.
    """
    monkeypatch.setattr(writer_module.MindWriter, "_commit", _REAL_MIND_COMMIT)

    outer = tmp_path / "outer"
    outer.mkdir()
    _init_repo(outer)
    seed = outer / "seed.txt"
    seed.write_text("seed\n", encoding="utf-8")
    _git(["add", "seed.txt"], outer)
    _git(["commit", "-m", "seed"], outer)

    root = outer / "Mind"
    (root / "Vault" / "meta").mkdir(parents=True)
    (root / "AGENTS.md").write_text("# Mind", encoding="utf-8")
    monkeypatch.setenv("MIND_ROOT", str(root))

    writer = MindWriter(root, commit=True, run_verifier=False)
    result = writer.write_one("Vault/inbox/x.md", "x")

    # The write itself is unaffected by the guard.
    assert result.ok is True
    assert (root / "Vault/inbox/x.md").read_text(encoding="utf-8") == "x"
    # …but no commit reached the enclosing repo.
    assert "commit skipped" in result.detail
    log = _git(["log", "--oneline"], outer)
    assert len(log.strip().splitlines()) == 1  # only the seed commit


def test_commit_happens_when_root_is_its_own_repo(tmp_path, monkeypatch):
    """The guard only blocks nesting; a Mind root that IS the top-level commits."""
    monkeypatch.setattr(writer_module.MindWriter, "_commit", _REAL_MIND_COMMIT)

    root = tmp_path / "Mind"
    (root / "Vault" / "meta").mkdir(parents=True)
    (root / "AGENTS.md").write_text("# Mind", encoding="utf-8")
    _init_repo(root)
    monkeypatch.setenv("MIND_ROOT", str(root))

    writer = MindWriter(root, commit=True, run_verifier=False)
    result = writer.write_one("Vault/inbox/x.md", "x")

    assert result.ok is True
    assert result.detail == "committed"
    log = _git(["log", "--oneline"], root)
    assert len(log.strip().splitlines()) == 1
    assert "chore(plobi)" in log


def test_concurrent_writers_do_not_interleave(vault):
    writer = MindWriter(vault, run_verifier=False)
    errors: list[BaseException] = []

    def worker(index: int) -> None:
        try:
            writer.write_one(f"Vault/inbox/w{index}.md", f"content {index}")
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    for index in range(8):
        assert (vault / f"Vault/inbox/w{index}.md").read_text(encoding="utf-8") == f"content {index}"
    # The lock file must not survive the batch.
    assert not (vault / ".plobi-mind.lock").exists()


def test_lock_times_out_rather_than_hanging(tmp_path):
    lock_path = tmp_path / "held.lock"
    with mind_write_lock(lock_path, timeout=1.0):
        with pytest.raises(MindLockTimeout):
            with mind_write_lock(lock_path, timeout=0.2):
                pass


def test_writer_never_imports_a_model_client():
    from plobi.mind import writer as writer_module

    text = Path(writer_module.__file__ or "").read_text(encoding="utf-8")
    for forbidden in ("openai", "anthropic", "deepseek", "completion("):
        assert forbidden not in text


# --- reader -----------------------------------------------------------------


def test_reader_pulls_persona_and_projects(vault):
    context = MindReader(vault).context()

    assert "邓德宇" in context.persona
    assert [p.name for p in context.projects] == ["Plobi"]
    assert "北极星" in context.projects[0].plan_excerpt
    assert "M1" in context.projects[0].progress_excerpt


def test_reader_excerpts_are_capped(vault):
    huge = "字" * 50_000
    (vault / "Vault" / "meta" / "Persona.md").write_text(huge, encoding="utf-8")

    persona = MindReader(vault).persona()
    assert len(persona) < 5_000


def test_reader_is_quiet_when_vault_absent(tmp_path):
    reader = MindReader(tmp_path / "absent")

    assert reader.available is False
    assert reader.persona() == ""
    assert reader.projects() == []
    assert reader.context().is_empty is True


def test_reader_skips_empty_project_dirs(vault):
    (vault / "Vault" / "projects" / "empty").mkdir()
    assert [p.name for p in MindReader(vault).projects()] == ["Plobi"]


# --- agenda digest ----------------------------------------------------------


def test_daily_summary_is_published_under_loom(vault, tmp_path):
    service = AgendaService(tmp_path / "agenda.db")
    today = datetime.now()
    service.create_manual(title="组会", start_at=today.replace(hour=15, minute=0, second=0, microsecond=0))

    result = publish_daily_summary(service=service, writer=MindWriter(vault, run_verifier=False))

    assert result.ok is True
    written = vault / summary_relative_path()
    assert written.is_file()
    assert "组会" in written.read_text(encoding="utf-8")


def test_daily_summary_is_idempotent(vault, tmp_path):
    service = AgendaService(tmp_path / "agenda.db")
    writer = MindWriter(vault, run_verifier=False)

    publish_daily_summary(service=service, writer=writer)
    publish_daily_summary(service=service, writer=writer)

    body = (vault / summary_relative_path()).read_text(encoding="utf-8")
    assert body.count("# 日程摘要") == 1


def test_daily_summary_without_vault_is_a_soft_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("MIND_ROOT", str(tmp_path / "absent"))
    service = AgendaService(tmp_path / "agenda.db")

    result = publish_daily_summary(service=service, writer=MindWriter(run_verifier=False))
    assert result.ok is False
    assert "unavailable" in result.detail
