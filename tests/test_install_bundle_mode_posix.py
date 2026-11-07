"""WP-BACKEND-IN-BUNDLE: the POSIX installer's bundle mode (install.sh).

install.sh REFUSES to run on Windows ("Windows detected. Please use the
PowerShell installer"), so the full-stage run belongs to the Linux CI. What
can be verified here -- and what this file does -- is the shell logic that
actually copies the packaged tree: `copy_bundled_backend` is extracted from
install.sh and RUN under bash with a fixture bundle (口径 27: script behaviour
is run, not read as text).

Asserted:
  · the packaged source lands in the install dir, byte for byte;
  · a file the previous version left behind is GONE (it would shadow the new
    one);
  · the venv stays where it is (its bin/ scripts carry absolute paths);
  · nothing was cloned (no .git);
  · the stage names and flags are wired where they must be: `--bundle-dir` /
    `--wheelhouse` parsed, the bundle branch in `clone_repo`, the offline
    wheelhouse tier in `install_deps` BEFORE the PyPI tier, and the POSIX
    dependency stage named `python-deps` (not `dependencies`).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SH = REPO_ROOT / "scripts" / "install.sh"
SOURCE = INSTALL_SH.read_text(encoding="utf-8")
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="这台没有 bash，install.sh 的行为无从真跑（不是通过）")


def _shell_function(name: str) -> str:
    """Extract one shell function from install.sh (its own source, verbatim)."""
    start = SOURCE.index(f"{name}() {{")
    end = SOURCE.index("\n}", start)
    return SOURCE[start : end + 2]


def test_the_bundled_tree_is_copied_over_the_installed_one(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    (bundle / "plobi_cli").mkdir(parents=True)
    (bundle / "plobi_cli" / "__init__.py").write_text('__version__ = "0.3.0"\n', encoding="utf-8")
    (bundle / "skills" / "demo").mkdir(parents=True)
    (bundle / "skills" / "demo" / "SKILL.md").write_text("demo skill\n", encoding="utf-8")
    (bundle / "scripts").mkdir(parents=True)
    (bundle / "scripts" / "install.sh").write_text("# bundled installer\n", encoding="utf-8")
    (bundle / "pyproject.toml").write_text("[project]\nname = 'plobi-agent'\n", encoding="utf-8")

    dest = tmp_path / "plobi-agent"
    (dest / "plobi_cli").mkdir(parents=True)
    (dest / "plobi_cli" / "stale.py").write_text("old\n", encoding="utf-8")
    (dest / "venv").mkdir(parents=True)
    (dest / "venv" / "keep.txt").write_text("keep\n", encoding="utf-8")

    script = tmp_path / "copy.sh"
    script.write_text(
        "set -euo pipefail\n"
        + _shell_function("copy_bundled_backend")
        + '\ncopy_bundled_backend "$1" "$2"\n',
        encoding="utf-8",
    )

    proc = subprocess.run(
        [BASH, str(script), str(bundle), str(dest)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr

    # The packaged source landed, byte for byte.
    assert (dest / "plobi_cli" / "__init__.py").read_text(encoding="utf-8") == '__version__ = "0.3.0"\n'
    assert (dest / "skills" / "demo" / "SKILL.md").read_text(encoding="utf-8") == "demo skill\n"
    assert (dest / "pyproject.toml").exists()

    # The old version's file is GONE -- a leftover would shadow the new one.
    assert not (dest / "plobi_cli" / "stale.py").exists()

    # The venv stays where it is.
    assert (dest / "venv" / "keep.txt").exists()

    # Nothing was cloned.
    assert not (dest / ".git").exists()


def test_the_bundle_mode_is_wired_where_it_must_be() -> None:
    # the flags are parsed
    assert "--bundle-dir|-BundleDir)" in SOURCE
    assert "--wheelhouse|-Wheelhouse)" in SOURCE

    # the bundle branch sits in clone_repo, before the clone
    clone_fn = _shell_function("clone_repo")
    assert 'if [ -n "$BUNDLE_DIR" ] && [ -d "$BUNDLE_DIR" ]; then' in clone_fn
    assert clone_fn.index('if [ -n "$BUNDLE_DIR" ]') < clone_fn.index("git clone --depth 1")

    # the offline wheelhouse tier sits in install_deps, before the PyPI tier
    deps_fn = _shell_function("install_deps")
    assert 'if [ -n "$WHEELHOUSE" ] && [ -d "$WHEELHOUSE" ]; then' in deps_fn
    # 离线那一档走 **requirements 文件**（uv 官方那条配方），不走 lockfile：
    # `uv sync` 按 lockfile 记的 URL 去取包、还要把每个支重解一遍 ⇒ 离线必挂
    # （2026-11-07 实测：装机版因此 fallback 回 PyPI）。
    assert "pip install --no-index --find-links" in deps_fn
    assert "--require-hashes" in deps_fn
    assert "-r \"$REQ_FILE\"" in deps_fn
    assert "uv sync --extra all --locked --no-index" not in deps_fn
    assert deps_fn.index('if [ -n "$WHEELHOUSE" ]') < deps_fn.index('if [ -f "uv.lock" ]')

    # the POSIX dependency stage is named `python-deps` (install.ps1 calls it
    # `dependencies` -- the two scripts do NOT share the name)
    assert re.search(r"^\s+python-deps\)", SOURCE, re.M)
    assert re.search(r"^\s+dependencies\)", SOURCE, re.M) is None
