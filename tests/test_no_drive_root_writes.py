"""Guard: nothing may create a directory on a volume root (WP-D-ROOT-RESIDUE).

裁定 79 / 48.1 in one sentence: on Windows a POSIX literal like ``/tmp`` is *not*
an absolute path — Python resolves it against the **current drive**, so a process
whose cwd sits on ``D:\\`` creates ``D:\\tmp`` and writes user data there. Both the
test suite and the product did exactly that on 2026-10-08 (``D:\\tmp\\test_plobi``
with a whole home skeleton, ``D:\\proc\\definitely\\not\\writable\\usage.json`` with
410 B of real billing JSON, ``D:\\tmp\\plobi_test\\runtime`` from a live gateway).

These tests are written as invariants, not name lists: the assertion is "a volume
root gained no new child while this code ran", which stays true on a machine whose
policy designates ``D:\\Temp`` and false the moment anyone reintroduces the bug.
Nothing here hardcodes a drive letter; roots are derived from cwd, the temp area
and the account home.
"""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"


def _load_script(filename: str):
    """Import a script from scripts/ — that directory is not a package.

    The synthetic module name must be a valid identifier: `dataclasses` resolves
    string annotations by looking the defining module up in `sys.modules`, and a
    name containing dots or hyphens ("_mod_check-windows-footguns.py") makes
    @dataclass raise while inspecting `Footgun`'s own annotations.
    """
    safe = "_plobi_script_" + "".join(ch if ch.isalnum() else "_" for ch in filename)
    spec = importlib.util.spec_from_file_location(safe, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[safe] = module
    spec.loader.exec_module(module)
    return module


def _volume_roots() -> list[Path]:
    """Every volume root this process can plausibly write to, derived not named."""
    anchors = set()
    for probe in (Path.cwd(), Path(os.path.abspath(tempdir())), Path(os.path.expanduser("~"))):
        try:
            anchor = Path(os.path.abspath(probe)).anchor
        except OSError:
            continue
        if anchor:
            anchors.add(anchor)
    return [Path(a) for a in sorted(anchors)]


def tempdir() -> str:
    import tempfile

    return tempfile.gettempdir()


def _children(root: Path) -> set[str]:
    try:
        return {p.name for p in root.iterdir()}
    except OSError:
        return set()


def _run_python_with_env(code: str, env_overrides: dict[str, str]) -> str:
    env = dict(os.environ)
    for key in ("TMPDIR", "TEMP", "TMP"):
        env.pop(key, None)
    env.update(env_overrides)
    env["PYTHONPATH"] = str(REPO_ROOT)
    done = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
        cwd=str(REPO_ROOT),
    )
    assert done.returncode == 0, done.stdout + done.stderr
    return done.stdout.strip()


# ------------------------------------------------------------------ the resolver


def test_posix_literal_env_value_never_becomes_a_volume_root_child():
    """TMP=/tmp must not turn into D:\\tmp (or C:\\tmp) just because cwd is on that drive.

    This is the hole the previous fix left open: ``_is_usable_temp_root`` refused
    the literal, then ``tempfile.gettempdir()`` — which reads the same env vars —
    handed the same directory back already-absolutised.
    """
    code = (
        "import plobi_constants as c; import os; "
        "print(c.get_temp_root()); print(os.path.abspath('/tmp'))"
    )
    out = _run_python_with_env(code, {"TMP": "/tmp", "TEMP": "/tmp", "TMPDIR": "/tmp"}).splitlines()
    root = Path(out[0])
    refused_target = os.path.normcase(out[1])
    if os.name == "nt":
        assert os.path.normcase(str(root)) != refused_target, (
            f"get_temp_root() returned the mistranslated literal: {root}"
        )
        assert len(root.parts) > 2, f"temp root must not be a child of a volume root: {root}"
    else:
        assert str(root) == "/tmp", f"POSIX semantics changed: {root}"


def test_temp_root_is_still_an_existing_writable_directory():
    """Refusing bad candidates must not leave us with no answer at all."""
    code = (
        "import os, plobi_constants as c; r = c.get_temp_root(); "
        "print(r); print(os.path.isdir(r), os.access(r, os.W_OK))"
    )
    for overrides in ({"TMP": "/tmp"}, {"TMPDIR": "/nonexistent-place-for-tests"}, {}):
        out = _run_python_with_env(code, overrides).splitlines()
        assert out[1] == "True True", f"{overrides} -> {out}"


def test_real_home_last_resort_stays_off_the_volume_root():
    """With no account home resolvable and TMP=/tmp, runtime state must not land on a root."""
    env = {"TMP": "/tmp", "TEMP": "/tmp", "TMPDIR": "/tmp"}
    out = _run_python_with_env(
        "import os, plobi_constants as c\n"
        "for k in ('HOME','USERPROFILE','HOMEDRIVE','HOMEPATH','PLOBI_REAL_HOME'):\n"
        "    os.environ.pop(k, None)\n"
        "print(c.get_real_home({}))\n",
        env,
    )
    root = Path(out.splitlines()[-1])
    assert os.path.isdir(str(root)), out
    if os.name == "nt":
        assert len(root.parts) > 2, f"real-home fallback sits on a volume root: {root}"


# ---------------------------------------------------------------- the test runner


def test_split_path_list_keeps_absolute_roots_whole():
    """`--paths` must not cut a Windows absolute root at the drive-letter colon."""
    module = _load_script("run_tests_parallel.py")
    absolute = str(REPO_ROOT / "tests")
    assert module._split_path_list(absolute) == [absolute], absolute
    joined = os.pathsep.join([absolute, absolute])
    assert module._split_path_list(joined) == [absolute, absolute], joined


def test_split_path_list_still_accepts_a_relative_colon_list():
    """The CI payload shape (':'-joined repo-relative roots) keeps working everywhere."""
    module = _load_script("run_tests_parallel.py")
    assert module._split_path_list("tests:tests/unit") == ["tests", "tests/unit"]


def test_split_path_list_drops_empty_pieces():
    module = _load_script("run_tests_parallel.py")
    assert module._split_path_list(os.pathsep.join(["", "tests", ""])) == ["tests"]


# ----------------------------------------------------------------- the new rule


def test_footgun_rule_catches_the_historic_shape_and_skips_legit_uses():
    checker = _load_script("check-windows-footguns.py")
    rule = next(f for f in checker.FOOTGUNS if f.name == "POSIX-literal temp path as a fallback/default")
    for bad in ('    return "/tmp"', "cwd = os.environ.get('PLOBI_TMP', \"/tmp\")"):
        assert rule.pattern.search(bad), bad
    for fine in (
        '    with open("/proc/version", "r", encoding="utf-8") as f:',
        '        "/opt/homebrew/etc/openssl@1.1/cert.pem",',
        '    assert root != "/tmp"',
        '    return tempfile.gettempdir()',
    ):
        assert not rule.pattern.search(fine), fine


# ------------------------------------------------------- the invariant that bites


def test_exercising_the_fixed_sites_creates_no_new_volume_root_child(tmp_path):
    """Run the three operations that used to spray, and prove a volume root gained nothing.

    Snapshot before/after rather than asserting "D:\\tmp must not exist": historic
    residue is already on this disk, and a guard that is red from the day it ships
    gets ignored. What must be impossible is *growth*.
    """
    before = {str(root): _children(root) for root in _volume_roots()}

    from plobi_cli.oneshot import _write_usage_file

    blocker = tmp_path / "not-a-directory"
    blocker.write_text("", encoding="utf-8")
    _write_usage_file(str(blocker / "nested" / "usage.json"), {"total_tokens": 1})

    import plobi_constants as c

    old_tmp = os.environ.get("TMP")
    try:
        for var in ("TMPDIR", "TEMP", "TMP"):
            os.environ[var] = "/tmp"
        c.get_temp_root()
        c.get_real_home({})
    finally:
        for var, value in (("TMPDIR", None), ("TEMP", None), ("TMP", old_tmp)):
            if value is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = value

    for root, was in before.items():
        now = _children(Path(root))
        assert now == was, f"{root} gained {sorted(now - was)} while the fixed code ran"
