"""Tests for ``plobi update`` / ``--check`` inside the Docker container.

Background: ``.dockerignore`` excludes ``.git``, so the existing git-pull
update path can never succeed inside the published image.  Before this
fix, ``plobi update`` would fall through to ``"✗ Not a git repository.
Please reinstall: curl ... install.sh"`` — that script installs a *new*
host-side Plobi, not an update to the running container, so the message
was actively misleading.

Update: ``cmd_update`` (the apply path) is now a frozen no-op in local dev
builds — it prints the self-update disable notice and returns 0 regardless
of install method, so the Docker guidance is no longer reachable through it.
The check path (``_cmd_update_check``) keeps its Docker-aware behaviour and
is covered directly below.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from plobi_cli.main import _SELF_UPDATE_DISABLED_MSG, _cmd_update_check, cmd_update


# ---------- cmd_update (apply path) ----------


@patch("plobi_cli.config.is_managed", return_value=False)
@patch("plobi_cli.config.detect_install_method", return_value="docker")
@patch("subprocess.run")
def test_cmd_update_is_disabled_even_in_docker(
    mock_run, _mock_method, _mock_managed, capsys
):
    """``plobi update`` is a frozen no-op — even in Docker: notice + exit 0, no git calls."""
    rc = cmd_update(SimpleNamespace(check=False))

    assert rc == 0
    out = capsys.readouterr().out
    assert _SELF_UPDATE_DISABLED_MSG in out
    # The Docker guidance is no longer reachable through the disabled stub.
    assert "doesn't apply inside the Docker container" not in out

    # No git invocations at all.
    git_calls = [c for c in mock_run.call_args_list if c.args and c.args[0] and "git" in str(c.args[0][0])]
    assert git_calls == [], f"expected no git calls, got: {git_calls}"


@patch("plobi_cli.config.is_managed", return_value=False)
@patch("plobi_cli.config.detect_install_method", return_value="docker")
@patch("subprocess.run")
def test_cmd_update_check_flag_is_disabled_even_in_docker(
    mock_run, _mock_method, _mock_managed, capsys
):
    """``plobi update --check`` is also a frozen no-op: notice + exit 0, no fetch."""
    rc = cmd_update(SimpleNamespace(check=True, branch=None))

    assert rc == 0
    out = capsys.readouterr().out
    assert _SELF_UPDATE_DISABLED_MSG in out
    assert "doesn't apply inside the Docker container" not in out

    git_calls = [c for c in mock_run.call_args_list if c.args and c.args[0] and "git" in str(c.args[0][0])]
    assert git_calls == [], f"expected no git calls, got: {git_calls}"


@patch("plobi_cli.config.is_managed", return_value=False)
@patch("plobi_cli.config.detect_install_method", return_value="docker")
@patch("subprocess.run")
def test_cmd_update_yes_and_force_are_ignored_by_disabled_stub(
    mock_run, _mock_method, _mock_managed, capsys
):
    """``--yes`` / ``--force`` can't re-enable self-update: notice + exit 0, no git."""
    rc = cmd_update(SimpleNamespace(check=False, yes=True, force=True))

    assert rc == 0
    assert _SELF_UPDATE_DISABLED_MSG in capsys.readouterr().out
    git_calls = [c for c in mock_run.call_args_list if c.args and c.args[0] and "git" in str(c.args[0][0])]
    assert git_calls == []


# ---------- _cmd_update_check (check path, direct entry) ----------


@patch("plobi_cli.config.detect_install_method", return_value="docker")
@patch("subprocess.run")
def test_cmd_update_check_direct_in_docker(mock_run, _mock_method, capsys):
    """Calling ``_cmd_update_check`` directly (no apply path) also bails."""
    with pytest.raises(SystemExit) as excinfo:
        _cmd_update_check()

    assert excinfo.value.code == 1
    assert "docker pull" in capsys.readouterr().out
    git_calls = [c for c in mock_run.call_args_list if c.args and c.args[0] and "git" in str(c.args[0][0])]
    assert git_calls == []


# ---------- Non-Docker installs unaffected ----------


@patch("plobi_cli.config.is_managed", return_value=False)
@patch("plobi_cli.config.detect_install_method", return_value="git")
@patch(
    "subprocess.run",
    return_value=SimpleNamespace(returncode=0, stdout="0\n", stderr=""),
)
def test_cmd_update_on_git_install_does_not_print_docker_message(
    _mock_run, _mock_method, _mock_managed, capsys
):
    """Source/git installs MUST NOT hit the Docker branch.

    Regression guard: an over-eager detection refactor could accidentally
    route git users through the docker-pull message.  We swallow
    SystemExit / unrelated errors from the rest of the update flow —
    those don't matter for this assertion; what matters is that the
    docker text is absent.

    ``subprocess.run`` is mocked because the git path will otherwise shell
    out to ``git fetch upstream`` / ``git fetch origin`` — on CI runners
    with no ``upstream`` remote configured this can hang past a timeout
    depending on git's network behaviour.  The stub
    returns a successful CompletedProcess-shaped object with ``"0\\n"``
    stdout, which both keeps the flow shell-free AND parses cleanly as
    the "0 commits behind" rev-list output the check path later parses
    via ``int(rev_result.stdout.strip())``.
    """
    try:
        cmd_update(SimpleNamespace(check=True, branch=None))
    except (SystemExit, Exception):
        # Update flow may exit for unrelated reasons in a stubbed env —
        # that's fine; we only care about the banner not appearing.
        pass

    assert "doesn't apply inside the Docker container" not in capsys.readouterr().out


@patch("plobi_cli.config.detect_install_method", return_value="pip")
@patch("plobi_cli.banner.check_via_pypi", return_value=0)
def test_cmd_update_check_on_pip_install_still_uses_pypi(
    _mock_pypi, _mock_method, capsys
):
    """PyPI installs route to PyPI check, not the Docker bail-out."""
    _cmd_update_check()

    out = capsys.readouterr().out
    assert "Already up to date" in out
    assert "doesn't apply inside the Docker container" not in out


# ---------- format_docker_update_message — content lock ----------


def test_format_docker_update_message_contents():
    """Lock in the high-value content of the Docker update message.

    These are the bits a user actually needs to act on; if any of them
    disappear in a copy edit, the message has lost its value.  Specific
    wording around them is free to evolve (we don't assert full text).
    """
    from plobi_cli.config import format_docker_update_message

    msg = format_docker_update_message()

    # Primary command — the entire reason this message exists.
    assert "docker pull nousresearch/hermes-agent:latest" in msg

    # The four key concepts the message must cover:
    assert "restart" in msg.lower(), "must explain that a restart is required"
    assert "--version" in msg, "must show how to verify the new version"
    assert ":latest" in msg, "must mention tag pinning caveat"
    assert "PLOBI_HOME" in msg or "/opt/data" in msg, (
        "must address config persistence across upgrades"
    )

    # Acknowledges that forks exist (build-your-own-image escape hatch).
    assert "fork" in msg.lower() or "Dockerfile" in msg
