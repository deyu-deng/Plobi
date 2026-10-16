"""``plobi mind status`` must not lie about the day's digest (WP-MIND-LIVE).

Two invariants, both earned from a live failure on 2026-10-16:

* a digest that is on disk but untracked is **not** "已入库" — that gap is what
  made every brain write invisible to ``git`` and froze the sync legs;
* the CLI surface must resolve the official provider from the same rule the agent
  uses, or ``plobi mind`` vanishes for installs that never wrote ``memory.provider``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


def _git(repo: Path, *args: str) -> str:
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
    return root


@pytest.fixture()
def two_brain_repos(tmp_path, monkeypatch):
    mind = _init_repo(tmp_path / "mind")
    vault = _init_repo(tmp_path / "vault")
    (mind / "AGENTS.md").write_text("# Mind", encoding="utf-8")
    _git(mind, "add", "AGENTS.md")
    _git(mind, "commit", "-m", "seed")
    _git(vault, "commit", "--allow-empty", "-m", "seed")
    monkeypatch.setenv("MIND_ROOT", str(mind))
    monkeypatch.setenv("VAULT_ROOT", str(vault))
    monkeypatch.setenv("PLOBI_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    return mind, vault


def test_digest_on_disk_but_untracked_is_not_reported_as_committed(two_brain_repos, monkeypatch):
    from datetime import datetime

    import plugins.memory.mind.cli as mind_cli

    _, vault = two_brain_repos
    day = datetime.now().date().isoformat()
    digest = vault / "projects" / "Plobi" / "daily" / f"{day}.md"
    digest.parent.mkdir(parents=True)
    digest.write_text("# 日程摘要\n", encoding="utf-8")

    lines = dict(mind_cli.status_lines())
    state = next(v for k, v in lines.items() if k.startswith("今日摘要"))
    assert "未入库" in state, state

    _git(vault, "add", f"projects/Plobi/daily/{day}.md")
    _git(vault, "commit", "-m", "chore(plobi): update 1 file(s)")
    lines = dict(mind_cli.status_lines())
    state = next(v for k, v in lines.items() if k.startswith("今日摘要"))
    assert "已入库" in state and "未提交改动" not in state, state


def test_the_cli_surface_resolves_the_official_provider_without_a_config_key(
    two_brain_repos,
):
    """No ``memory.provider`` key = nothing decided = the official brain, on every surface.

    The empty-string case is the opposite decision and must stay distinguishable:
    someone opting out has to actually switch the CLI off.
    """
    from plugins.memory import _get_active_memory_provider

    assert _get_active_memory_provider() == "mind"

    config = Path(os.environ["PLOBI_HOME"]) / "config.yaml"
    config.write_text("memory:\n  provider: ''\n", encoding="utf-8")
    assert _get_active_memory_provider() is None
