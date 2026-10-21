"""Tests for ``scripts/plobi/migrate_legacy_memories.py`` — the "确认没用" ladder.

The script exists because 裁定 42/178 retired ``<home>/memories/`` on the read
side only: the ledger there was still on disk but nothing injected it any more.
These tests pin the three things the user's instruction depends on — plan without
writing, migrate then *verify before delete*, and refuse deletion when a chunk
cannot be found in its new home.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "plobi" / "migrate_legacy_memories.py"

from tools import memory_tool as mt  # noqa: E402


@pytest.fixture(scope="module")
def mig():
    spec = importlib.util.spec_from_file_location("migrate_legacy_memories", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def legacy_home(tmp_path, monkeypatch):
    """A temp install holding the retired layout: two notes + one profile chunk."""
    root = tmp_path / "plobihome"
    root.mkdir()
    monkeypatch.setenv("PLOBI_HOME", str(root))
    monkeypatch.delenv("PLOBI_AGENT_ID", raising=False)
    monkeypatch.setattr(mt, "get_shared_plobi_root", lambda: root)
    old = root / "memories"
    old.mkdir()
    (old / "MEMORY.md").write_text(
        "单 WP 单 commit 前缀 [WP-XXX]\n§\n测试入口 scripts/run_tests.sh，禁直调 pytest",
        encoding="utf-8",
    )
    (old / "USER.md").write_text("用户是这台机器的主人", encoding="utf-8")
    return root


def test_plan_reads_the_retired_files_without_writing(mig, legacy_home):
    items = mig.plan()
    assert len(items) == 3, "旧 MEMORY.md 两条 + USER.md 一条"
    assert {scope for scope, _, _ in items} == {"global", "user"}
    assert all(src for _, _, src in items), "每条都要带出处指针"
    assert not mt.get_memory_dir().exists(), "没给 --apply 就不该建任何文件"


def test_dry_run_adds_nothing(mig, legacy_home):
    res = mig.migrate(apply=False)
    assert sum(res["added"].values()) == 3
    assert not mt.get_memory_dir().exists()


def test_apply_writes_into_the_three_part_layout(mig, legacy_home):
    res = mig.migrate(apply=True)
    glob = mt.memory_path("global")
    prof = mt.memory_path("user")
    assert glob.is_file() and prof.is_file()
    body = glob.read_text(encoding="utf-8")
    assert "单 WP 单 commit 前缀 [WP-XXX]" in body
    assert "run_tests.sh" in body
    assert "〔出处 Docs/README.md" in body, "每条落新家都要带出处"
    assert "用户是这台机器的主人" in prof.read_text(encoding="utf-8")
    assert set(res["added"]) == {"global", "user"}


def test_apply_is_idempotent(mig, legacy_home):
    mig.migrate(apply=True)
    before = mt.memory_path("global").read_text(encoding="utf-8")
    res = mig.migrate(apply=True)
    assert res["added"] == {}, "同一条原文不许搬第二遍"
    assert mt.memory_path("global").read_text(encoding="utf-8") == before


def test_verify_refuses_delete_when_a_chunk_is_missing(mig, legacy_home):
    mig.migrate(apply=True)
    store = mt.load_on_disk_store()
    store._set_entries("global", [])
    store.save_to_disk("global")
    missing = mig.verify_migrated(mt.load_on_disk_store(), mig.plan())
    assert len(missing) == 2, "两条全局条目都没了就该点名"
    assert mig._legacy_dir().exists()


def test_cli_deletes_only_after_the_reconciliation_passes(mig, legacy_home, capsys):
    assert mig.main(["--apply", "--delete"]) == 0
    out = capsys.readouterr().out
    assert "对账通过" in out
    assert "已删旧目录内容" in out
    assert not mig._legacy_dir().exists()
    assert mt.memory_path("global").is_file()


def test_cli_without_delete_keeps_the_legacy_dir(mig, legacy_home):
    assert mig.main(["--apply"]) == 0
    assert mig._legacy_dir().is_dir()


def test_cli_red_when_verify_fails(mig, legacy_home, monkeypatch):
    """If the new home silently loses a chunk, the CLI must not remove the old one."""
    monkeypatch.setattr(mig, "verify_migrated", lambda store, items: ["global: 丢了一条"])
    assert mig.main(["--apply", "--delete"]) == 1
    assert mig._legacy_dir().is_dir()
