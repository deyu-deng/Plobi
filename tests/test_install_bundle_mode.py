"""WP-BACKEND-IN-BUNDLE：安装器的 bundle 模式必须**拷源码、不 clone**。

桌面安装包现在带上后端（运行时源码树 ＋ 锁定的依赖轮子仓）⇒ 首启与更新都从包里装：
不再 clone 仓库、不再从 PyPI 解析 ⇒ 装完就是完整一版，前后端同号，断网也能起。

这条测试**真跑 PowerShell**（口径 27：脚本类不许只读文本），而且**不联网**：
夹具是本地目录，断言的是行为——

  · `-BundleDir` 给出时，repository 档把包里的源码树拷进安装目录，
    **不产生 .git**（今天这条路是 clone）；
  · 旧版本留下的文件被**替换**而不是合并（留着就会遮住新版本的那份）；
  · venv 留在原地（它的 Scripts\\*.exe 带绝对路径，搬走就坏）。

**临时目录必须落在数据盘**：安装器自己按 `D:\\_POLICY\\directory-policy.md` 挡住
系统盘上的运行数据（"非交互模式不当没看见"）——测试得守同一条规矩，所以不用
pytest 的 tmp_path（测试入口把它指到 C 盘的 scratch home）。

依赖那一档（`-Wheelhouse` ⇒ `uv sync --no-index --find-links` ⇒ 离线装锁定的轮子）
在本测试里只断言**调用形状**（快）；真装一次要几分钟，留在装机态验收里跑。
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_PS1 = REPO_ROOT / "scripts" / "install.ps1"
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")

# The data drive this machine keeps run data on (see D:\\_POLICY). The
# installer refuses a run-data dir on the system drive, so the fixture has to
# live here -- the same rule the product enforces.
DATA_TMP = Path("D:/Temp")

pytestmark = pytest.mark.skipif(
    POWERSHELL is None,
    reason="这台没有 PowerShell，install.ps1 的行为无从真跑（不是通过）",
)


@pytest.fixture
def sandbox():
    """A throwaway tree on the data drive (the installer forbids C: here)."""
    if not DATA_TMP.is_dir():
        pytest.skip("这台没有数据盘临时目录（D:/Temp），按盘规运行数据不许落系统盘")

    root = Path(tempfile.mkdtemp(prefix="plobi-bundle-", dir=str(DATA_TMP)))
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _child_env(install_dir: Path) -> dict[str, str]:
    """A child environment the installer can actually work in.

    Two things the test harness takes away:
    · **PATHEXT** -- `env -i` strips it, and PowerShell then cannot resolve a
      command that has no extension on the PATH (`git`).
    · the **git config --global** call inside the installer must not touch the
      USER's global config -> point it at a throwaway file.
    """
    import os

    env = dict(os.environ)
    dirs = []

    git_exe = shutil.which("git")
    if git_exe:
        dirs.append(str(Path(git_exe).parent))

    try:
        probe = subprocess.run(["git", "--exec-path"], capture_output=True, text=True, timeout=60)
        root = Path(probe.stdout.strip())
        for _ in range(3):
            root = root.parent
        for sub in ("cmd", "bin", "mingw64/bin"):
            dirs.append(str(root / sub))
    except Exception:  # noqa: BLE001
        pass

    if dirs:
        env["PATH"] = os.pathsep.join(dirs) + os.pathsep + env.get("PATH", "")

    env["PATHEXT"] = env.get("PATHEXT") or ".COM;.EXE;.BAT;.CMD;.VBS;.JS;.WSF;.MSC"
    env["GIT_CONFIG_GLOBAL"] = str(install_dir.parent / "probe-gitconfig")

    return env


def _run_stage(stage: str, *, install_dir: Path, plobi_home: Path, bundle_dir: Path | None = None,
               wheelhouse: Path | None = None) -> subprocess.CompletedProcess:
    cmd = [
        POWERSHELL,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(INSTALL_PS1),
        "-Stage",
        stage,
        "-NonInteractive",
        "-Json",
        "-PlobiHome",
        str(plobi_home),
        "-InstallDir",
        str(install_dir),
    ]

    if bundle_dir is not None:
        cmd += ["-BundleDir", str(bundle_dir)]

    if wheelhouse is not None:
        cmd += ["-Wheelhouse", str(wheelhouse)]

    return subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=_child_env(install_dir))


def _make_bundle(root: Path) -> Path:
    """A miniature stand-in for the packaged backend."""
    bundle = root / "bundle"

    (bundle / "plobi_cli").mkdir(parents=True)
    (bundle / "plobi_cli" / "__init__.py").write_text('__version__ = "0.3.0"\n', encoding="utf-8")

    (bundle / "skills" / "demo").mkdir(parents=True)
    (bundle / "skills" / "demo" / "SKILL.md").write_text("demo skill\n", encoding="utf-8")

    (bundle / "scripts").mkdir(parents=True)
    (bundle / "scripts" / "install.ps1").write_text("# bundled installer\n", encoding="utf-8")

    (bundle / "pyproject.toml").write_text("[project]\nname = 'plobi-agent'\n", encoding="utf-8")
    (bundle / "uv.lock").write_text("version = 1\n", encoding="utf-8")

    return bundle


def test_bundle_mode_copies_the_source_instead_of_cloning(sandbox: Path) -> None:
    bundle = _make_bundle(sandbox)
    plobi_home = sandbox / "home"
    install_dir = plobi_home / "plobi-agent"
    install_dir.mkdir(parents=True)

    # A previous version's leftovers, and the venv that must survive.
    (install_dir / "plobi_cli").mkdir()
    (install_dir / "plobi_cli" / "stale.py").write_text("old\n", encoding="utf-8")
    (install_dir / "venv").mkdir()
    (install_dir / "venv" / "keep.txt").write_text("keep\n", encoding="utf-8")

    proc = _run_stage("repository", install_dir=install_dir, plobi_home=plobi_home, bundle_dir=bundle)

    assert proc.returncode == 0, proc.stdout + proc.stderr

    # The packaged source landed, byte for byte.
    assert (install_dir / "plobi_cli" / "__init__.py").read_text(encoding="utf-8") == '__version__ = "0.3.0"\n'
    assert (install_dir / "skills" / "demo" / "SKILL.md").read_text(encoding="utf-8") == "demo skill\n"
    assert (install_dir / "pyproject.toml").exists()
    assert (install_dir / "uv.lock").exists()

    # The old version's file is GONE -- a leftover would shadow the new one.
    assert not (install_dir / "plobi_cli" / "stale.py").exists()

    # The venv stays where it is (its Scripts\*.exe carry absolute paths).
    assert (install_dir / "venv" / "keep.txt").exists()

    # And nothing was cloned: no git repo, no remote.
    assert not (install_dir / ".git").exists()


def test_without_a_bundle_the_stage_still_wants_a_checkout() -> None:
    """No -BundleDir => the old shape (a git checkout) is still what happens.

    Asserted on the SOURCE, not by running it: that path really clones over
    the network, and it is today's behaviour rather than this knife's.
    """
    source = INSTALL_PS1.read_text(encoding="utf-8")

    assert "git -c windows.appendAtomically=false clone --depth 1 --branch $Branch" in source, "克隆那条路不见了"
    assert "if ($BundleDir -and (Test-Path $BundleDir))" in source, "bundle 分支不见了"
    assert source.index("if ($BundleDir -and (Test-Path $BundleDir))") < source.index(
        "git -c windows.appendAtomically=false clone --depth 1 --branch $Branch"
    ), "bundle 分支必须排在克隆那条路之前"


def test_the_dependencies_stage_asks_uv_for_an_offline_install() -> None:
    """The dependency stage must be able to install from the shipped wheels.

    Asserted on the SOURCE (the call shape), not by running it: a real
    install of the whole wheelhouse takes minutes. The behaviour it buys is
    verified for real by the CI step that installs the shipped wheelhouse
    offline before the package is built.
    """
    source = INSTALL_PS1.read_text(encoding="utf-8")

    assert "--no-index" in source and "--find-links" in source, "依赖档没有离线安装的开关"
    assert "--offline" in source, "依赖档没有禁止联网"
    # The script DECLARES the parameters as $BundleDir/$Wheelhouse (the dashed
    # form is the command line's), so assert the declaration.
    assert "[string]$BundleDir" in source, "安装器没有 bundle 模式入口"
    assert "[string]$Wheelhouse" in source, "安装器没有轮子仓入口"

    # The wheelhouse branch must come BEFORE the PyPI fallback, or the
    # install would reach for the network first.
    bundle_at = source.index("if ($Wheelhouse -and (Test-Path $Wheelhouse))")
    pypi_at = source.index('Write-Info "Trying tier: hash-verified (uv.lock) ..."')
    assert bundle_at < pypi_at, "轮子仓那一档排在 PyPI 兜底之后了"


def test_the_offline_tier_uses_the_requirements_file_not_the_lockfile() -> None:
    """离线那一档必须走 **requirements 文件**，不能走 lockfile。

    `uv sync` 按 lockfile 里记的 URL 去取包（`--find-links` 只在解析新需求时用）⇒ 离线
    它去 files.pythonhosted.org 找；它还要把 lockfile 的**每一个支**重新解一遍才肯装 ⇒
    只带一支轮子必然失败。两条都是 2026-11-07 实测出来的（装机版因此 fallback 回 PyPI，
    「安装过程不再另行下载依赖」那句话当时并不成立）。
    """
    source = INSTALL_PS1.read_text(encoding="utf-8")

    assert "pip install --no-index --find-links $Wheelhouse --require-hashes" in source, (
        "离线那一档没有用 uv pip install 的官方配方"
    )
    assert "-r $reqFile" in source, "离线那一档没有读 requirements 文件"
    assert "sync --extra all --locked --no-index" not in source, (
        "离线那一档还在用 uv sync --locked —— 它按 lockfile 的 URL 去取包，离线必挂"
    )


def test_the_posix_leg_uses_the_same_offline_recipe() -> None:
    """install.sh 那条腿必须与 Windows 那条同形（mac/linux 同样要能离线装）。"""
    source = (REPO_ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")

    assert "pip install --no-index --find-links" in source, "install.sh 的离线那一档没跟上"
    assert "--require-hashes" in source, "install.sh 的离线那一档不验哈希"
    assert "sync --extra all --locked --no-index" not in source, (
        "install.sh 的离线那一档还在用 uv sync --locked"
    )


def test_the_build_downloads_the_wheels_of_the_python_the_installer_pins() -> None:
    """CI 必须下**装机版用的那个 Python**（3.11）的轮子。

    装机器脚本钉的是 3.11（install.ps1::$PythonVersion、install.sh::$PYTHON_VERSION），
    轮子仓里若只有 CI 自己那个 Python（3.12）的轮子 ⇒ 装机版离线装不上（2026-11-07
    实测：包里 19 个 cp312 轮子、1 个 cp311）。
    """
    workflow = (REPO_ROOT / ".github" / "workflows" / "release-desktop.yml").read_text(encoding="utf-8")

    assert "--python-version 3.11" in workflow, "CI 下的轮子不是装机版要的那一版"
    # 而且要在装包之前先验一遍离线装得上（这一道门是这次事故的直接对策）。
    assert "Verify the offline install" in workflow, "CI 没有离线安装自检"
    assert "--no-index --find-links apps/desktop/build/wheelhouse" in workflow, (
        "CI 的离线自检没有用本地轮子仓"
    )


def test_the_requirements_file_travels_with_the_backend() -> None:
    """requirements.txt 必须跟源码树一起进包——离线那一档读的就是它。"""
    script = (REPO_ROOT / "apps" / "desktop" / "scripts" / "collect-backend-bundle.mjs").read_text(
        encoding="utf-8"
    )

    assert "requirements.txt" in script, "收集脚本没有把 requirements.txt 带进包"

