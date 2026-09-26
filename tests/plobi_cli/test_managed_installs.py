from types import SimpleNamespace
from unittest.mock import patch

from plobi_cli.config import (
    format_managed_message,
    get_managed_system,
    recommended_update_command,
)
from plobi_cli.main import cmd_update
from tools.skills_hub import OptionalSkillSource


def test_get_managed_system_homebrew(monkeypatch):
    monkeypatch.setenv("PLOBI_MANAGED", "homebrew")

    assert get_managed_system() == "Homebrew"
    assert recommended_update_command() == "brew upgrade plobi-agent"


def test_format_managed_message_homebrew(monkeypatch):
    monkeypatch.setenv("PLOBI_MANAGED", "homebrew")

    message = format_managed_message("update Plobi Agent")

    assert "managed by Homebrew" in message
    assert "brew upgrade plobi-agent" in message


def test_recommended_update_command_defaults_to_plobi_update(monkeypatch):
    monkeypatch.delenv("PLOBI_MANAGED", raising=False)

    # Also short-circuit the .managed marker path — CI runners may have an
    # ambient ~/.plobi/.managed if a prior test left PLOBI_HOME pointing
    # somewhere with that marker, which would make get_managed_update_command()
    # return "Update your Nix flake input ..." instead of falling through to
    # detect_install_method().
    with patch("plobi_cli.config.get_managed_update_command", return_value=None), \
         patch("plobi_cli.config.detect_install_method", return_value="git"):
        assert recommended_update_command() == "plobi update"


def test_cmd_update_is_disabled_even_when_managed(monkeypatch, capsys):
    """``plobi update`` is a frozen no-op — the managed-mode check never runs."""
    from plobi_cli.main import _SELF_UPDATE_DISABLED_MSG

    monkeypatch.setenv("PLOBI_MANAGED", "homebrew")

    with patch("plobi_cli.main.subprocess.run") as mock_run:
        rc = cmd_update(SimpleNamespace())

    assert rc == 0
    assert _SELF_UPDATE_DISABLED_MSG in capsys.readouterr().out
    mock_run.assert_not_called()


def test_optional_skill_source_honors_env_override(monkeypatch, tmp_path):
    optional_dir = tmp_path / "optional-skills"
    optional_dir.mkdir()
    monkeypatch.setenv("PLOBI_OPTIONAL_SKILLS", str(optional_dir))

    source = OptionalSkillSource()

    assert source._optional_dir == optional_dir
