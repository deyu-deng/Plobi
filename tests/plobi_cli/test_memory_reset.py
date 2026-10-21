"""Tests for `plobi memory reset` — driven through the real ``cmd_memory``.

The previous version of this file re-implemented the command's own logic inside a
helper (``_run_memory_reset``), so it stayed green while the real command was
clearing ``<home>/memories/`` — a location 裁定 42/178 retired and nothing reads
any more. Everything here calls ``cmd_memory`` and asserts against
``tools.memory_tool.memory_path``, the same resolver the agent writes through, so
the command cannot drift away from the ledgers it claims to clear.
"""

from types import SimpleNamespace

import pytest

from tools.memory_tool import memory_path


def _args(target="all", yes=True):
    return SimpleNamespace(memory_command="reset", target=target, yes=yes)


@pytest.fixture
def memory_env(tmp_path, monkeypatch):
    """A fake PLOBI_HOME with live entries in each ledger, plus planted decoys."""
    home = tmp_path / ".plobi"
    home.mkdir(parents=True)
    monkeypatch.setenv("PLOBI_HOME", str(home))
    monkeypatch.delenv("PLOBI_AGENT_ID", raising=False)

    own = memory_path("memory")
    own.parent.mkdir(parents=True, exist_ok=True)
    own.write_text(
        "§\nPlobi repo is at ~/.plobi/plobi-agent\n§\nUser prefers dark themes",
        encoding="utf-8",
    )
    user = memory_path("user")
    user.write_text("§\nOwns a piano\n§\nTimezone: China Standard", encoding="utf-8")
    # The shared ledger is NOT this command's business — it is written through the
    # approval layer, so clearing it stays a human call. Plant one to prove it.
    glob = memory_path("global")
    glob.parent.mkdir(parents=True, exist_ok=True)
    glob.write_text("Repo rules live in Docs/", encoding="utf-8")
    # Retired location, still on disk: must survive too. Clearing files nobody
    # reads was exactly the old bug.
    retired = home / "memories"
    retired.mkdir()
    (retired / "MEMORY.md").write_text("retired copy", encoding="utf-8")
    return {
        "home": home,
        "own": own,
        "user": user,
        "global": glob,
        "retired": retired / "MEMORY.md",
    }


class TestMemoryReset:
    def test_reset_all_clears_both_live_ledgers(self, memory_env, capsys):
        from plobi_cli.main import cmd_memory

        cmd_memory(_args(target="all", yes=True))
        out = capsys.readouterr().out
        assert not memory_env["own"].exists()
        assert not memory_env["user"].exists()
        assert "left alone" in out

    def test_global_ledger_survives_reset_all(self, memory_env):
        """全局那一格不归这条命令清（它走待确认层，清空要人来决定）。"""
        from plobi_cli.main import cmd_memory

        cmd_memory(_args(target="all", yes=True))
        assert memory_env["global"].exists()

    def test_retired_location_survives_reset_all(self, memory_env):
        from plobi_cli.main import cmd_memory

        cmd_memory(_args(target="all", yes=True))
        assert memory_env["retired"].read_text(encoding="utf-8") == "retired copy"

    def test_reset_memory_only_keeps_profile(self, memory_env):
        from plobi_cli.main import cmd_memory

        cmd_memory(_args(target="memory", yes=True))
        assert not memory_env["own"].exists()
        assert memory_env["user"].exists()

    def test_reset_user_only_keeps_agent_notes(self, memory_env):
        from plobi_cli.main import cmd_memory

        cmd_memory(_args(target="user", yes=True))
        assert memory_env["own"].exists()
        assert not memory_env["user"].exists()

    def test_confirmation_denied_keeps_everything(self, memory_env, monkeypatch):
        from plobi_cli.main import cmd_memory

        monkeypatch.setattr("builtins.input", lambda *a: "no")
        cmd_memory(_args(target="all", yes=False))
        assert memory_env["own"].exists()
        assert memory_env["user"].exists()

    def test_confirmation_accepted_deletes(self, memory_env, monkeypatch):
        from plobi_cli.main import cmd_memory

        monkeypatch.setattr("builtins.input", lambda *a: "yes")
        cmd_memory(_args(target="all", yes=False))
        assert not memory_env["own"].exists()
        assert not memory_env["user"].exists()

    def test_nothing_to_reset_reports_and_creates_nothing(self, tmp_path, monkeypatch, capsys):
        from plobi_cli.main import cmd_memory

        home = tmp_path / ".plobi"
        home.mkdir(parents=True)
        monkeypatch.setenv("PLOBI_HOME", str(home))
        monkeypatch.delenv("PLOBI_AGENT_ID", raising=False)

        cmd_memory(_args(target="all", yes=True))
        assert "Nothing to reset" in capsys.readouterr().out
        assert not memory_path("memory").exists()

    def test_partial_ledgers_still_reset(self, memory_env):
        """One ledger missing must not stop the other from being cleared."""
        from plobi_cli.main import cmd_memory

        memory_env["user"].unlink()
        cmd_memory(_args(target="all", yes=True))
        assert not memory_env["own"].exists()

    def test_profile_home_resets_the_shared_ledger_not_its_own_dir(self, tmp_path, monkeypatch):
        """裁 42/178: an agent's memory lives at the shared layer, not in its home."""
        from plobi_cli.main import cmd_memory
        from plobi_constants import get_plobi_home, get_shared_plobi_root

        profile_home = tmp_path / "profiles" / "myprofile"
        profile_home.mkdir(parents=True)
        monkeypatch.setenv("PLOBI_HOME", str(profile_home))
        monkeypatch.setenv("PLOBI_AGENT_ID", "myprofile")

        own = memory_path("memory")
        assert get_plobi_home() == profile_home
        assert own.is_relative_to(get_shared_plobi_root() / "memory"), own
        own.parent.mkdir(parents=True, exist_ok=True)
        own.write_text("profile memory", encoding="utf-8")
        legacy = profile_home / "memories" / "MEMORY.md"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text("retired profile copy", encoding="utf-8")

        cmd_memory(_args(target="memory", yes=True))
        assert not own.exists()
        assert legacy.read_text(encoding="utf-8") == "retired profile copy"
