"""The canonical runner must not let tests touch the developer's real $HOME.

R-041: ``scripts/run_tests.sh`` used to pass ``HOME="$HOME"`` into its
``env -i`` block. ``tests/conftest.py`` redirects ``PLOBI_HOME`` per test, but
that only covers code going through ``get_plobi_home()`` — import-time module
constants, the logging setup and anything reading ``Path.home()`` wrote into the
real ``~/.plobi`` (config.yaml, logs, cron/jobs.json, state.db and an auth.json
holding a live ``gh`` token). Besides polluting the machine, that made
"must fail closed when no credentials exist" tests depend on what a previous run
left behind.

These are behaviour contracts, not snapshots: the child must be given a scratch
HOME, the scratch must not be the caller's HOME, and it must be cleaned up.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RUNNER = REPO_ROOT / "scripts" / "run_tests.sh"


def _probe_dir(tmp_path: Path) -> Path:
    """A test dir whose single test reports the HOME it actually saw."""
    d = tmp_path / "homeprobe"
    d.mkdir()
    (d / "test_homeprobe.py").write_text(
        "import os\n"
        "from pathlib import Path\n"
        "\n"
        "def test_write_something(tmp_path):\n"
        "    # Mimic the leak: a module-level-ish write under the real home.\n"
        "    (Path.home() / '.plobi').mkdir(parents=True, exist_ok=True)\n"
        "    (Path.home() / '.plobi' / 'leak.txt').write_text(os.getcwd())\n"
        "    assert (Path.home() / '.plobi').is_dir()\n",
        encoding="utf-8",
    )
    return d


def _run(d: Path, fake_home: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(RUNNER), str(d), "-j", "1", "--file-timeout", "60", "-q"],
        cwd=REPO_ROOT,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(fake_home),
             "TMPDIR": str(fake_home / "tmp"), "LANG": "C.UTF-8"},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=300,
    )


def test_runner_replaces_home_with_a_scratch_dir(tmp_path: Path) -> None:
    fake_home = tmp_path / "realhome"
    fake_home.mkdir()
    (fake_home / "tmp").mkdir()

    proc = _run(_probe_dir(tmp_path), fake_home)

    assert proc.returncode == 0, proc.stdout
    # The child wrote its leak marker, but never into the caller's HOME.
    assert not (fake_home / ".plobi").exists(), (
        f"tests wrote into the caller's $HOME:\n{proc.stdout}"
    )
    assert "HOME=" in proc.stdout, "runner should say which HOME it used"


def test_scratch_home_is_cleaned_up(tmp_path: Path) -> None:
    fake_home = tmp_path / "realhome"
    fake_home.mkdir()
    scratch_parent = fake_home / "tmp"
    scratch_parent.mkdir()

    proc = _run(_probe_dir(tmp_path), fake_home)

    assert proc.returncode == 0, proc.stdout
    # mktemp honours $TMPDIR, so the scratch dir was created under
    # scratch_parent; the runner removes it on exit (trap + explicit cleanup).
    assert not list(scratch_parent.glob("plobi-tests-home.*")), (
        f"scratch HOME left behind:\n{proc.stdout}"
    )
