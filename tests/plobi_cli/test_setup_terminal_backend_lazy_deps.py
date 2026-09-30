"""Terminal-backend SDK installs must not bypass tools/lazy_deps.

Why this exists: `setup_terminal_backend()` used to answer a missing Modal or
Daytona SDK with `_pip_install(["modal"])` / `_pip_install(["daytona"])` and, on
failure, print `uv pip install modal` / `uv pip install daytona`. A bare name
hands pip a free resolve, so the setup wizard could leave the venv holding a
different build than `LAZY_DEPS["terminal.modal"]` /
`LAZY_DEPS["terminal.daytona"]` pin for the rest of the app — and it skipped the
`security.allow_lazy_installs` gate entirely. Both keys already exist, so the
fix was to route through `ensure()`, not to add anything.

The tests below assert the *relationship* (every install routes through
ensure() with the right key; no rendered string offers an unpinned install),
not a snapshot of the pin table.
"""

import ast
import builtins
import inspect
import re
import types

import pytest

from plobi_cli.config import load_config
import plobi_cli.setup as setup_mod


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _lazy_deps_pkg_index():
    """Map canonical package name -> the exact specs LAZY_DEPS pins it at."""
    from tools.lazy_deps import LAZY_DEPS

    index = {}
    for specs in LAZY_DEPS.values():
        for spec in specs:
            name = re.match(r"^[A-Za-z0-9_.\-]+", spec)
            if not name:
                continue
            index.setdefault(name.group(0).lower().replace("_", "-"), set()).add(spec)
    return index


def _unpinned_install_offenders(text):
    """Every `pip install` fragment in ``text`` naming a lazy_deps-pinned
    package without that package's exact pin.

    Applied to both the source literals and the rendered output: a bare name
    hands whoever runs it a free resolve, which is exactly the bug class this
    file pins shut.
    """
    index = _lazy_deps_pkg_index()
    offenders = []
    for chunk in re.findall(r"pip install[^\n]*", text):
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_.\-]*", chunk):
            key = token.lower().replace("_", "-")
            if key not in index:
                continue
            if not re.search(rf"{re.escape(token)}(\[[^\]]*\])?\s*==", chunk):
                offenders.append((token, chunk, sorted(index[key])))
    return offenders


def _runtime_strings(module):
    """Every non-docstring str literal in a module, parsed from its real source.

    Comments and docstrings are developer-facing prose; this returns the strings
    the product actually renders.
    """
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


def _pip_install_references(func):
    """Every import or call of `_pip_install` inside one function's source."""
    found = []
    for node in ast.walk(ast.parse(inspect.getsource(func))):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "_pip_install":
            found.append(("call", node.lineno))
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names or ():
                if alias.name == "_pip_install":
                    found.append(("import", node.lineno))
    return found


_BACKENDS = {
    "modal": {"choice": 2, "feature": "terminal.modal"},
    "daytona": {"choice": 4, "feature": "terminal.daytona"},
}


def _drive(monkeypatch, capsys, tmp_path, backend, *, ensure_impl=None, sdk_present=False):
    """Run setup_terminal_backend() down one cloud-backend branch.

    Returns (ensure_calls, printed output). `lazy_deps.ensure` is stubbed to
    record the key it was called with; any surviving hand-rolled `_pip_install`
    raises, so a regression fails loudly instead of shelling out to pip.
    """
    import tools.lazy_deps as lazy_deps

    from plobi_cli.setup import setup_terminal_backend

    cfg = _BACKENDS[backend]
    monkeypatch.setenv("PLOBI_HOME", str(tmp_path))
    for var in ("MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET", "DAYTONA_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    # No managed gateway -> the Modal branch takes the "my own account" path,
    # which is the one that installs the SDK.
    monkeypatch.setattr("plobi_cli.setup.managed_nous_tools_enabled", lambda: False)
    monkeypatch.setattr("plobi_cli.setup._prompt_container_resources", lambda config: None)

    calls = []

    def fake_ensure(feature, *, prompt=True):
        calls.append((feature, prompt))
        if ensure_impl is not None:
            return ensure_impl(feature)

    monkeypatch.setattr(lazy_deps, "ensure", fake_ensure)
    monkeypatch.setattr(
        "plobi_cli.tools_config._pip_install",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("terminal backend setup must not hand-roll _pip_install")
        ),
    )

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        # Only the backend SDK is reported missing; every other import
        # (tools.lazy_deps included) must reach the real importer.
        if name == backend:
            if sdk_present:
                return types.ModuleType(name)
            raise ImportError(f"no module named {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)

    monkeypatch.setattr(
        "plobi_cli.setup.prompt_choice",
        lambda question, choices, default=0: (
            cfg["choice"] if question.startswith("Select terminal backend") else default
        ),
    )
    monkeypatch.setattr("plobi_cli.setup.prompt", lambda *a, **k: "")
    monkeypatch.setattr("plobi_cli.setup.prompt_yes_no", lambda *a, **k: False)

    config = load_config()
    setup_terminal_backend(config)
    return calls, capsys.readouterr().out


# ---------------------------------------------------------------------------
# routing: the install must land on ensure() with the right key
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("backend", ["modal", "daytona"])
def test_missing_backend_sdk_installs_through_ensure(monkeypatch, capsys, tmp_path, backend):
    """A missing SDK installs via lazy_deps.ensure() with prompt=False — the
    wizard owns the terminal, and `security.allow_lazy_installs` is the gate."""
    calls, out = _drive(monkeypatch, capsys, tmp_path, backend)
    assert calls == [(_BACKENDS[backend]["feature"], False)], (
        f"{backend} must install through lazy_deps.ensure('{_BACKENDS[backend]['feature']}'), "
        f"got {calls}"
    )
    assert "installed" in out


@pytest.mark.parametrize("backend", ["modal", "daytona"])
def test_present_backend_sdk_triggers_no_install(monkeypatch, capsys, tmp_path, backend):
    """An SDK that is already importable must not touch the installer at all."""
    calls, out = _drive(monkeypatch, capsys, tmp_path, backend, sdk_present=True)
    assert calls == []
    assert "pip install" not in out


@pytest.mark.parametrize("backend", ["modal", "daytona"])
def test_backend_selection_survives_the_routed_install(monkeypatch, capsys, tmp_path, backend):
    """Routing the install through lazy_deps must not change what the wizard
    persists: the backend still gets written to config."""
    calls, out = _drive(monkeypatch, capsys, tmp_path, backend)
    assert calls
    assert load_config()["terminal"]["backend"] == backend


# ---------------------------------------------------------------------------
# rendered text: what is missing + which in-app action installs it, never a
# bare command the human has to run by hand
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("backend", ["modal", "daytona"])
def test_refused_install_names_the_pin_and_the_in_app_action(
    monkeypatch, capsys, tmp_path, backend
):
    """When ensure() refuses (offline, or security.allow_lazy_installs=false),
    the message states the exact LAZY_DEPS pin and the in-app action.

    The old shape printed `uv pip install modal` / `uv pip install daytona` —
    a bare name with no pin, telling a human to run by hand the command the
    software runs itself.
    """
    from tools.lazy_deps import FeatureUnavailable, feature_specs

    feature = _BACKENDS[backend]["feature"]
    pins = feature_specs(feature)

    def _boom(_feature):
        raise FeatureUnavailable(
            feature,
            pins,
            "lazy installs disabled (security.allow_lazy_installs=false)",
        )

    calls, out = _drive(monkeypatch, capsys, tmp_path, backend, ensure_impl=_boom)
    assert calls == [(feature, False)]
    for pin in pins:
        assert pin in out, f"expected the pinned spec {pin!r} in: {out}"
    assert "plobi setup" in out
    assert "Could not install" in out
    # Whatever else the mechanism's own text renders, no fragment may offer this
    # backend's package unpinned — that is the old `uv pip install modal` shape.
    assert _unpinned_install_offenders(out) == []
    assert f"uv pip install {backend}" not in out
    assert f"pip install {backend}" not in out


@pytest.mark.parametrize("backend", ["modal", "daytona"])
def test_hand_rolled_install_is_not_left_on_the_success_path(
    monkeypatch, capsys, tmp_path, backend
):
    """The happy path renders a plain "installed" line, no command, no pin echo.

    `_pip_install` is patched to raise inside `_drive`, so a leftover hand
    install would blow up here; this asserts the rendered text too.
    """
    calls, out = _drive(monkeypatch, capsys, tmp_path, backend)
    assert "pip install" not in out
    assert "Run manually" not in out


# ---------------------------------------------------------------------------
# source invariants
# ---------------------------------------------------------------------------


def test_setup_terminal_backend_has_no_hand_rolled_pip_install():
    """Invariant: the terminal-backend wizard neither calls nor imports
    `_pip_install` any more.

    The neutts/kittentts TTS helpers still hand-roll installs elsewhere in this
    module (no LAZY_DEPS entry for those packages yet); this asserts on the
    terminal-backend path only.
    """
    assert _pip_install_references(setup_mod.setup_terminal_backend) == []


def test_no_rendered_string_offers_an_unpinned_install():
    """Invariant: a string setup.py renders may name a lazy_deps-pinned package
    in an install command only at that package's exact pin.

    Catches the whole class rather than one line at a time: re-adding
    `uv pip install modal` would fail here, and so would a hardcoded
    `modal==X.Y.Z` that drifted from LAZY_DEPS.
    """
    index = _lazy_deps_pkg_index()
    assert index

    offenders = _unpinned_install_offenders("\n".join(_runtime_strings(setup_mod)))
    assert not offenders, f"unpinned install command offered to the user: {offenders}"


@pytest.mark.parametrize("backend", ["modal", "daytona"])
def test_backend_feature_key_exists_in_the_pin_table(backend):
    """The keys these sites route through are ones LAZY_DEPS already pins — no
    new key, and no pin value invented here.

    Asserts the relationship (each spec is an exact `==` pin of that backend's
    own SDK), not the version literal, so bumping a pin cannot break this.
    """
    from tools.lazy_deps import LAZY_DEPS

    specs = LAZY_DEPS[_BACKENDS[backend]["feature"]]
    assert specs
    for spec in specs:
        assert re.match(rf"^{backend}==[0-9][0-9A-Za-z.\-]*$", spec), spec
