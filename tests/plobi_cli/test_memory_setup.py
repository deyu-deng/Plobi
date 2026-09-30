from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import plobi_cli.memory_setup as memory_setup
from plobi_cli.memory_setup import _CANCELLED, _curses_select


def test_curses_select_cancel_defaults_to_selected(monkeypatch):
    captured = {}

    def fake_radiolist(title, items, selected=0, *, cancel_returns=None):
        captured.update({
            "title": title,
            "items": items,
            "selected": selected,
            "cancel_returns": cancel_returns,
        })
        return cancel_returns

    monkeypatch.setattr("plobi_cli.curses_ui.curses_radiolist", fake_radiolist)

    result = _curses_select("Pick one", [("first", "desc"), ("second", "")], default=1)

    assert result == 1
    assert captured == {
        "title": "Pick one",
        "items": ["first - desc", "second"],
        "selected": 1,
        "cancel_returns": 1,
    }


def test_curses_select_accepts_explicit_cancel_value(monkeypatch):
    captured = {}

    def fake_radiolist(title, items, selected=0, *, cancel_returns=None):
        captured["cancel_returns"] = cancel_returns
        return cancel_returns

    monkeypatch.setattr("plobi_cli.curses_ui.curses_radiolist", fake_radiolist)

    result = _curses_select("Pick one", [("first", "")], default=0, cancel_returns=_CANCELLED)

    assert result == _CANCELLED
    assert captured["cancel_returns"] == _CANCELLED


def test_curses_select_clears_after_picker_returns(monkeypatch):
    events = []

    def fake_radiolist(title, items, selected=0, *, cancel_returns=None):
        events.append("picker")
        return selected

    monkeypatch.setattr("plobi_cli.curses_ui.curses_radiolist", fake_radiolist)
    monkeypatch.setattr(memory_setup, "_clear_interactive_transition", lambda: events.append("clear"))

    result = _curses_select("Pick one", [("first", "")], default=0)

    assert result == 0
    assert events == ["picker", "clear"]


def test_cmd_setup_top_level_cancel_writes_nothing(monkeypatch):
    save_config = MagicMock()
    load_config = MagicMock(side_effect=AssertionError("cancel should not load config"))

    monkeypatch.setattr(memory_setup, "_get_available_providers", lambda: [("fake", "local", object())])
    monkeypatch.setattr(memory_setup, "_curses_select", lambda *args, **kwargs: kwargs["cancel_returns"])
    monkeypatch.setattr("plobi_cli.config.load_config", load_config)
    monkeypatch.setattr("plobi_cli.config.save_config", save_config)

    memory_setup.cmd_setup(SimpleNamespace())

    load_config.assert_not_called()
    save_config.assert_not_called()


def test_cmd_setup_builtin_selection_still_saves_builtin(monkeypatch):
    save_config = MagicMock()
    config = {"memory": {"provider": "openviking"}}
    providers = [("fake", "local", object())]

    monkeypatch.setattr(memory_setup, "_get_available_providers", lambda: providers)
    monkeypatch.setattr(memory_setup, "_curses_select", lambda *args, **kwargs: len(providers))
    monkeypatch.setattr("plobi_cli.config.load_config", lambda: config)
    monkeypatch.setattr("plobi_cli.config.save_config", save_config)

    memory_setup.cmd_setup(SimpleNamespace())

    assert config["memory"]["provider"] == ""
    save_config.assert_called_once_with(config)


def test_cmd_setup_clears_interactive_picker_before_provider_post_setup(monkeypatch):
    events = []

    class PostSetupProvider:
        def post_setup(self, plobi_home, config):
            events.append("post_setup")

    monkeypatch.setattr(memory_setup, "_get_available_providers", lambda: [("openviking", "local", PostSetupProvider())])
    monkeypatch.setattr(memory_setup, "_curses_select", lambda *args, **kwargs: events.append("select") or 0)
    monkeypatch.setattr(memory_setup, "_clear_interactive_transition", lambda: events.append("clear"), raising=False)
    monkeypatch.setattr(memory_setup, "_install_dependencies", lambda name: events.append("install"))
    monkeypatch.setattr(memory_setup, "get_plobi_home", lambda: "/tmp/plobi-test")
    monkeypatch.setattr("plobi_cli.config.load_config", lambda: {"memory": {}})

    memory_setup.cmd_setup(SimpleNamespace())

    assert events == ["select", "clear", "install", "post_setup"]


def test_cmd_setup_provider_clears_before_provider_post_setup(monkeypatch):
    events = []

    class PostSetupProvider:
        def post_setup(self, plobi_home, config):
            events.append("post_setup")

    monkeypatch.setattr(memory_setup, "_get_available_providers", lambda: [("openviking", "local", PostSetupProvider())])
    monkeypatch.setattr(memory_setup, "_clear_interactive_transition", lambda: events.append("clear"), raising=False)
    monkeypatch.setattr(memory_setup, "_install_dependencies", lambda name: events.append("install"))
    monkeypatch.setattr(memory_setup, "get_plobi_home", lambda: "/tmp/plobi-test")
    monkeypatch.setattr("plobi_cli.config.load_config", lambda: {"memory": {}})

    memory_setup.cmd_setup_provider("openviking")

    assert events == ["clear", "install", "post_setup"]


def test_cmd_status_prefers_provider_status_config(monkeypatch, capsys):
    class StatusProvider:
        def get_status_config(self, provider_config):
            assert provider_config["endpoint"] == "http://stale.local"
            return {
                "use_ovcli_config": True,
                "ovcli_config_path": "/tmp/ovcli.conf.VPS_ROOT",
                "endpoint": "https://vps.example",
                "account": "acct",
                "user": "alice",
                "agent": "plobi",
            }

        def is_available(self):
            return True

    config = {
        "memory": {
            "provider": "openviking",
            "openviking": {
                "use_ovcli_config": True,
                "ovcli_config_path": "/tmp/ovcli.conf.VPS_ROOT",
                "endpoint": "http://stale.local",
            },
        }
    }
    monkeypatch.setattr("plobi_cli.config.load_config", lambda: config)
    monkeypatch.setattr(memory_setup, "_get_available_providers", lambda: [("openviking", "API key / local", StatusProvider())])

    memory_setup.cmd_status(SimpleNamespace())

    output = capsys.readouterr().out
    assert "endpoint: https://vps.example" in output
    assert "http://stale.local" not in output


def test_cmd_setup_generic_choice_cancel_writes_nothing(tmp_path, monkeypatch):
    class ChoiceProvider:
        def __init__(self):
            self.save_config = MagicMock()

        def get_config_schema(self):
            return [{
                "key": "mode",
                "description": "Mode",
                "default": "one",
                "choices": ["one", "two"],
            }]

    provider = ChoiceProvider()
    selections = iter([0, _CANCELLED])
    save_config = MagicMock()
    install_dependencies = MagicMock()

    monkeypatch.setattr(memory_setup, "_get_available_providers", lambda: [("fake", "local", provider)])
    monkeypatch.setattr(memory_setup, "_curses_select", lambda *args, **kwargs: next(selections))
    monkeypatch.setattr(memory_setup, "_install_dependencies", install_dependencies)
    monkeypatch.setattr(memory_setup, "get_plobi_home", lambda: tmp_path)
    monkeypatch.setattr("plobi_cli.config.load_config", lambda: {"memory": {}})
    monkeypatch.setattr("plobi_cli.config.save_config", save_config)

    memory_setup.cmd_setup(SimpleNamespace())

    install_dependencies.assert_called_once_with("fake")
    save_config.assert_not_called()
    provider.save_config.assert_not_called()
    assert not (tmp_path / ".env").exists()


# ---------------------------------------------------------------------------
# Dependency pinning: memory provider installs must not bypass tools/lazy_deps
#
# `_install_dependencies()` used to read the bare / open-range names straight
# out of plugin.yaml ("honcho-ai", "mem0ai>=2.0.10,<3") and hand them to
# `_pip_install(["--quiet"] + missing)`, then print
# `uv pip install <those same names>` as the manual fallback. That skips the
# LAZY_DEPS allowlist and its exact pins, and lets pip move a dependency the
# app pins elsewhere. The pins for these providers do exist
# (memory.honcho / memory.mem0 / memory.supermemory / memory.hindsight), so the
# fix is to route through ensure(), not to add anything.
# ---------------------------------------------------------------------------

_MISSING_DEP = "definitely-not-installed-xyz"


def _drive_install_dependencies(
    monkeypatch, capsys, tmp_path, provider_name, *, ensure_impl=None, dep=_MISSING_DEP
):
    """Run _install_dependencies() for a fake plugin dir, installer mocked.

    Returns (ensure_calls, printed output). Any surviving hand-rolled pip call
    raises instead of shelling out, so a regression fails loudly.
    """
    import tools.lazy_deps as lazy_deps

    (tmp_path / "plugin.yaml").write_text(
        f"pip_dependencies:\n  - {dep}\n", encoding="utf-8"
    )
    calls = []

    def fake_ensure(feature, *, prompt=True):
        calls.append((feature, prompt))
        if ensure_impl is not None:
            return ensure_impl(feature)

    monkeypatch.setattr(lazy_deps, "ensure", fake_ensure)
    monkeypatch.setattr("plugins.memory.find_provider_dir", lambda name: tmp_path)
    monkeypatch.setattr(
        "plobi_cli.tools_config._pip_install",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("memory setup must not hand-roll _pip_install")
        ),
    )

    memory_setup._install_dependencies(provider_name)
    return calls, capsys.readouterr().out


@pytest.mark.parametrize("provider", ["honcho", "mem0", "supermemory", "hindsight"])
def test_missing_provider_deps_install_through_ensure(monkeypatch, capsys, tmp_path, provider):
    """Each provider whose packages LAZY_DEPS pins must install via ensure(),
    with prompt=False (setup already owns the terminal)."""
    calls, out = _drive_install_dependencies(monkeypatch, capsys, tmp_path, provider)
    assert calls == [(f"memory.{provider}", False)], (
        f"{provider} must install through lazy_deps.ensure('memory.{provider}'), got {calls}"
    )
    assert _MISSING_DEP not in out or "uv pip install" not in out


def test_provider_without_a_lazy_deps_entry_installs_nothing(monkeypatch, capsys, tmp_path):
    """No allowlist entry means no known-safe pin, so nothing gets installed.

    openviking/retaindb declare httpx and requests — core dependencies that
    ship pinned with the app. Installing them from plugin.yaml here would be an
    unpinned resolve of a package pyproject already fixes, so the correct
    behaviour is to say so plainly and install nothing.
    """
    from tools.lazy_deps import LAZY_DEPS

    assert "memory.openviking" not in LAZY_DEPS
    calls, out = _drive_install_dependencies(monkeypatch, capsys, tmp_path, "openviking")
    assert calls == []
    assert "uv pip install" not in out
    assert "pip install" not in out
    assert "installing nothing" in out


def test_refused_install_reports_the_pinned_spec(monkeypatch, capsys, tmp_path):
    """When ensure() refuses (offline, or security.allow_lazy_installs=false),
    the manual line must come from LAZY_DEPS — never a bare package name."""
    from tools.lazy_deps import LAZY_DEPS, FeatureUnavailable

    pin = LAZY_DEPS["memory.honcho"][0]

    def _boom(feature):
        raise FeatureUnavailable(
            feature, LAZY_DEPS[feature], "lazy installs disabled (security.allow_lazy_installs=false)"
        )

    calls, out = _drive_install_dependencies(
        monkeypatch, capsys, tmp_path, "honcho", ensure_impl=_boom
    )
    assert calls == [("memory.honcho", False)]
    assert pin in out, f"expected the pinned spec {pin!r} in: {out}"
    # The old shape was `uv pip install <name>` with no version at all.
    assert f"uv pip install {_MISSING_DEP}" not in out
    assert "Could not install" in out


def test_already_importable_deps_trigger_no_install(monkeypatch, capsys, tmp_path):
    """A provider whose packages are present must not touch the installer."""
    calls, out = _drive_install_dependencies(
        # `json` stands in for an already-satisfied dependency: the probe here
        # is `__import__(<pip name>)`, and stdlib names always resolve.
        monkeypatch, capsys, tmp_path, "honcho", dep="json"
    )
    assert calls == []
    assert "pip install" not in out


def _pip_install_references(module):
    """Every import or call of _pip_install in a module's real source."""
    import ast
    import inspect

    found = []
    for node in ast.walk(ast.parse(inspect.getsource(module))):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "_pip_install":
            found.append(("call", node.lineno))
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names or ():
                if alias.name == "_pip_install":
                    found.append(("import", node.lineno))
    return found


def _runtime_strings(module):
    """Non-docstring str literals — the text this module actually renders."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(module))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                docstrings.add(id(first.value))
    return [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings
    ]


def test_memory_setup_contains_no_hand_rolled_pip_install():
    """Invariant: this module neither calls nor imports _pip_install any more."""
    assert _pip_install_references(memory_setup) == []


def test_memory_setup_hardcodes_no_install_command_string():
    """Invariant: no literal in this module may spell out a pip install line.

    The remediation command has to come from lazy_deps.feature_install_command()
    at runtime, which reads LAZY_DEPS — so it cannot drift from the pins.
    """
    offenders = [s for s in _runtime_strings(memory_setup) if "pip install" in s]
    assert not offenders, f"hardcoded install command literal, will drift from LAZY_DEPS: {offenders}"
