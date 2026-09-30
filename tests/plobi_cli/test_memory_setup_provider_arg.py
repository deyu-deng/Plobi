"""Tests for `plobi memory setup [provider]` routing.

The `memory setup` subcommand accepts an optional positional ``provider`` so a
fresh install can configure a specific provider directly (e.g.
``plobi memory setup honcho``) without the interactive picker — which matters
because the per-provider ``plobi <provider>`` subcommand is only registered
once that provider is active.
"""

from types import SimpleNamespace
from unittest.mock import patch

from plobi_cli import memory_setup


class TestMemorySetupProviderRouting:
    def test_setup_with_provider_arg_skips_picker(self):
        """`memory setup honcho` routes straight to cmd_setup_provider."""
        args = SimpleNamespace(memory_command="setup", provider="honcho")
        with patch.object(memory_setup, "cmd_setup_provider") as direct, \
             patch.object(memory_setup, "cmd_setup") as picker:
            memory_setup.memory_command(args)
        direct.assert_called_once_with("honcho")
        picker.assert_not_called()

    def test_setup_without_provider_runs_picker(self):
        """`memory setup` (no provider) runs the interactive picker."""
        args = SimpleNamespace(memory_command="setup", provider=None)
        with patch.object(memory_setup, "cmd_setup_provider") as direct, \
             patch.object(memory_setup, "cmd_setup") as picker:
            memory_setup.memory_command(args)
        picker.assert_called_once_with(args)
        direct.assert_not_called()

    def test_setup_with_missing_provider_attr_runs_picker(self):
        """A SimpleNamespace lacking `provider` must not crash — fall back to picker."""
        args = SimpleNamespace(memory_command="setup")
        with patch.object(memory_setup, "cmd_setup_provider") as direct, \
             patch.object(memory_setup, "cmd_setup") as picker:
            memory_setup.memory_command(args)
        picker.assert_called_once_with(args)
        direct.assert_not_called()

    def test_unknown_provider_reports_and_returns_early(self, capsys):
        """An unknown provider name surfaces a helpful message and returns
        before any config load/save (the not-found guard precedes those imports)."""
        memory_setup.cmd_setup_provider("notaprovider")
        out = capsys.readouterr().out
        assert "not found" in out
        assert "plobi memory setup" in out


class TestInstallDependenciesRunner:
    """`_install_dependencies` must route through ``tools/lazy_deps.ensure()``.

    It used to call ``plobi_cli.tools_config._pip_install`` with the names read
    straight out of plugin.yaml. Those names are bare or open-ranged
    ("honcho-ai", "mem0ai>=2.0.10,<3"), so the install resolved freely with no
    exact pin and no allowlist — the bypass AGENTS.md's Dependency Pinning
    Policy exists to stop.

    The uv → pip → ensurepip runner ladder this class used to assert is not
    lost: it lives in ``tools/lazy_deps._venv_pip_install`` (its own docstring
    says it mirrors ``_pip_install``), and ``ensure()`` is the only way to reach
    it from here now. Asserting the ladder through the mock would only test
    lazy_deps internals from the wrong side of the boundary.
    """

    def _run_with_missing_dep(self, tmp_path, monkeypatch, provider_name):
        (tmp_path / "plugin.yaml").write_text(
            "pip_dependencies:\n  - definitely-not-installed-xyz\n", encoding="utf-8"
        )
        import tools.lazy_deps as lazy_deps

        calls = []
        monkeypatch.setattr(
            lazy_deps, "ensure",
            lambda feature, *, prompt=True: calls.append((feature, prompt)),
        )
        monkeypatch.setattr("plugins.memory.find_provider_dir", lambda name: tmp_path)
        monkeypatch.setattr(
            "plobi_cli.tools_config._pip_install",
            lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("_install_dependencies must not hand-roll _pip_install")
            ),
        )
        memory_setup._install_dependencies(provider_name)
        return calls

    def test_pinned_provider_installs_through_ensure(self, tmp_path, monkeypatch):
        """honcho has a LAZY_DEPS entry, so ensure() gets its key — not a pip call."""
        assert self._run_with_missing_dep(tmp_path, monkeypatch, "honcho") == [
            ("memory.honcho", False)
        ]

    def test_provider_without_an_entry_installs_nothing(self, tmp_path, monkeypatch):
        """No allowlist entry → no known-safe pin → no install, no hand-rolled pip."""
        assert self._run_with_missing_dep(tmp_path, monkeypatch, "x") == []

    def test_ensure_runs_non_interactively(self, tmp_path, monkeypatch):
        """Setup owns the terminal; a blocking install prompt would deadlock it."""
        from tools.lazy_deps import LAZY_DEPS

        for provider in ("mem0", "supermemory", "hindsight"):
            assert f"memory.{provider}" in LAZY_DEPS, provider
            calls = self._run_with_missing_dep(tmp_path, monkeypatch, provider)
            assert calls == [(f"memory.{provider}", False)]
