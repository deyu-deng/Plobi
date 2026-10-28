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

    import plobi_cli.mind_commands as mind_cli

    # 摘要在脑里（Loom → MIND_ROOT）：写错根就测成「缺席」而不是「未入库」
    mind, _vault = two_brain_repos
    day = datetime.now().date().isoformat()
    digest = mind / "wiki" / "daily" / f"{day}.md"
    digest.parent.mkdir(parents=True)
    digest.write_text("# 日程摘要\n", encoding="utf-8")

    lines = dict(mind_cli.status_lines())
    state = next(v for k, v in lines.items() if k.startswith("今日摘要"))
    assert "未入库" in state, state

    _git(mind, "add", f"wiki/daily/{day}.md")
    _git(mind, "commit", "-m", "chore(plobi): update 1 file(s)")
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


def test_persona_status_says_when_the_condensed_copy_lags(tmp_path, monkeypatch):
    """精简版是手工蒸的，没有自动派生路径 ⇒ `plobi mind status` 那行必须把"落后"说出来。

    反着也要成立：精简版比详版新（正常状态）时不许喊重蒸，否则这句提醒天天响、变成没人看的噪音。
    """
    import time

    from plobi.mind.paths import Roots
    from plobi_cli.mind_commands import _persona_state

    mind = tmp_path / "Mind"
    (mind / "persona").mkdir(parents=True)
    home = tmp_path / "home"
    (home / "memory").mkdir(parents=True)
    profile = mind / "persona" / "profile.md"
    user_md = home / "memory" / "USER.md"
    profile.write_text("# 用户画像\n\n- 姓名：小龟\n", encoding="utf-8")
    user_md.write_text("邓德彧（小龟）…\n", encoding="utf-8")
    monkeypatch.setenv("MIND_ROOT", str(mind))
    monkeypatch.setenv("PLOBI_HOME", str(home))
    roots = Roots(mind=mind, vault=None)

    # 精简版与详版同日 ⇒ 别喊
    now = time.time()
    os.utime(profile, (now - 86400 * 5, now - 86400 * 5))
    os.utime(user_md, (now, now))
    assert "该重蒸" not in _persona_state(roots), _persona_state(roots)

    # 详版被改过、精简版还是老的 ⇒ 说出来（谁改画像谁欠这一次蒸馏）
    os.utime(profile, (now, now))
    os.utime(user_md, (now - 3600, now - 3600))
    line = _persona_state(roots)
    assert "该重蒸" in line, line
