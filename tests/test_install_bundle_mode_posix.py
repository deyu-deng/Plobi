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

import os
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


# ── Node.js 与 ffmpeg：用户 11-07 拍「都进包」 ─────────────────────────────


def _shell_fixture(script: str, tmp_path: Path) -> subprocess.CompletedProcess:
    """Run a shell script built from install.sh's own functions."""
    path = tmp_path / "probe.sh"
    path.write_text(script, encoding="utf-8")
    return subprocess.run(
        [BASH, str(path)],
        capture_output=True,
        text=True,
        timeout=120,
    )


def _bash(script: str) -> None:
    """Run a shell snippet.

    Fixtures that must be EXECUTABLE are built here, not from Python: only a
    shell can set the exec bit the shell itself looks at (`[ -x ... ]`).
    """
    proc = subprocess.run([BASH, "-c", script], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_the_bundled_node_is_unpacked_instead_of_downloaded(tmp_path: Path) -> None:
    """包里那份 Node 解到 $PLOBI_HOME/node（真跑 bash）。

    用户 11-07：「开箱即用的体验多平台也要做」——mac/linux 那条腿也得
    装完就有 Node，不能首启再下一份。

    整场在 bash 里跑（真 POSIX 路径）：GNU tar 把 "C:/..." 当远端主机，
    而装机机上永远是 POSIX 路径。
    """
    script = (
        "set -uo pipefail\n"
        "is_termux() { return 1; }\n"
        "ROOT_FHS_LAYOUT=false\n"
        "DISTRO=linux\n"
        "OS=linux\n"
        "FIX=$(mktemp -d)\n"
        "mkdir -p \"$FIX/toolchain\" \"$FIX/stage/node-v22.23.4-linux-x64/bin\" \"$FIX/home\"\n"
        "printf '#!/bin/sh\\necho v22.23.4\\n' > \"$FIX/stage/node-v22.23.4-linux-x64/bin/node\"\n"
        "chmod +x \"$FIX/stage/node-v22.23.4-linux-x64/bin/node\"\n"
        "tar czf \"$FIX/toolchain/node-v22.23.4-linux-x64.tar.gz\" "
        "-C \"$FIX/stage\" node-v22.23.4-linux-x64\n"
        'export PLOBI_HOME="$FIX/home"\n'
        'export TOOLCHAIN_DIR="$FIX/toolchain"\n'
        + _shell_function("get_command_link_dir")
        + "\n"
        + _shell_function("configure_managed_node_npm_prefix")
        + "\n"
        + _shell_function("install_node_from_archive")
        + "\n"
        'install_node_from_archive "$TOOLCHAIN_DIR/node-v22.23.4-linux-x64.tar.gz"\n'
        'if [ -x "$PLOBI_HOME/node/bin/node" ]; then echo "NODE-EXECUTABLE"; else echo "NODE-MISSING"; fi\n'
        'echo "REACHED-END"\n'
    )
    proc = _shell_fixture(script, tmp_path)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "REACHED-END" in proc.stdout, proc.stdout
    assert "NODE-EXECUTABLE" in proc.stdout, (
        f"包里那份 Node 没落到 $PLOBI_HOME/node：{proc.stdout}{proc.stderr}"
    )


def test_the_bundled_ffmpeg_is_unpacked_instead_of_installed(tmp_path: Path) -> None:
    """包里那份 ffmpeg 解到 $PLOBI_HOME/bin/ffmpeg（真跑 bash），不去问包管理器。

    装机器上的样子和 uv 一样：$PLOBI_HOME/bin —— 后端的 PATH 里就有这一格。
    """
    import gzip

    toolchain = tmp_path / "toolchain"
    toolchain.mkdir()
    with gzip.open(toolchain / "ffmpeg-linux-x64.gz", "wb") as gz:
        gz.write(b"\0" * (1024 * 1024 + 512))

    # 一个假的 rg 放在 PATH 上 ⇒ need_ripgrep 也归零，于是这个函数在碰
    # apt/brew 之前就返回（不联网、不装任何东西）。
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _bash(
        "set -e\n"
        f"printf '#!/bin/sh\\necho FAKE-RG 1.2.3\\n' > '{fake_bin.as_posix()}/rg'\n"
        f"chmod +x '{fake_bin.as_posix()}/rg'\n"
    )

    home = tmp_path / "home"
    home.mkdir()
    # PATH 只留这两个目录：假的 rg 在前，gzip 在 /usr/bin。真 rg 与真 ffmpeg
    # 装在 Windows PATH 上（WinGet / D:\Portables）——这里它们不在场，于是
    # 「包里那份」那条分支才是被跑的那条。
    script = (
        "set -uo pipefail\n"
        "log_info() { echo \"INFO:$1\"; }\n"
        "log_success() { echo \"OK:$1\"; }\n"
        "log_warn() { echo \"WARN:$1\"; }\n"
        "is_termux() { return 1; }\n"
        "ROOT_FHS_LAYOUT=false\n"
        "DISTRO=linux\n"
        "OS=linux\n"
        f'export PATH="{fake_bin.as_posix()}:/usr/bin"\n'
        f'PLOBI_HOME="{home.as_posix()}"\n'
        f'TOOLCHAIN_DIR="{toolchain.as_posix()}"\n'
        + _shell_function("install_system_packages")
        + "\n"
        "install_system_packages\n"
        'echo "REACHED-END"\n'
    )
    proc = _shell_fixture(script, tmp_path)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "REACHED-END" in proc.stdout, proc.stdout
    assert "apt" not in proc.stdout and "brew" not in proc.stdout, f"它去问包管理器了：{proc.stdout}"
    assert "ffmpeg ready (from the installer, no download)" in proc.stdout, (
        f"它没有去用包里那份 ffmpeg：{proc.stdout}{proc.stderr}"
    )
    ffmpeg = home / "bin" / "ffmpeg"
    assert ffmpeg.is_file(), "包里那份 ffmpeg 没落到 $PLOBI_HOME/bin"
    assert os.access(ffmpeg, os.X_OK), "ffmpeg 装上了却不可执行"
