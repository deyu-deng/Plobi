"""首启零下载：包里的工具链（PortableGit + ripgrep）优先于下载。

2026-11-07 用户判词：「为什么我们的软件下载起来这么复杂，和别人的一点都不一样」
——别人的安装包把运行时一起带走，我们的让**首启**去下载。于是 PortableGit（git + bash）
与 ripgrep 进了包，装的时候先用包里那份。

这一档盯那条「先用包里那份」的分支：

* **真跑**（PowerShell）：把 PATH 掐掉（让 git 不在 PATH 上），给它一份假的
  PortableGit ⇒ 它应当去解**包里那份**并失败（假档案解不开），而**不是**去下载
  ——stdout 里没有 "Downloading" 就是证据。
* 源码形状：`-ToolchainDir` 参数在；包里那份 ripgrep 也有同一条分支；包里那份
  PortableGit 用完**不许删**（它属于安装包，不是临时下载物）。
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

_INSTALL_PS1 = Path(__file__).resolve().parents[1] / "scripts" / "install.ps1"
_POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")

_SOURCE = _INSTALL_PS1.read_text(encoding="utf-8")

pytestmark = pytest.mark.skipif(_POWERSHELL is None, reason="这台没有 PowerShell，装机器脚本的行为无从真跑（不是通过）")

_STUBS = (
    "$ErrorActionPreference = 'Stop'\n"
    'function Write-Info { param([string]$Message) Write-Output "INFO:$Message" }\n'
    'function Write-Success { param([string]$Message) Write-Output "OK:$Message" }\n'
    'function Write-Warn { param([string]$Message) Write-Output "WARN:$Message" }\n'
    'function Write-Err { param([string]$Message) Write-Output "ERR:$Message" }\n'
)


def _function_text(name: str) -> str:
    # The trailing space matters: "function Test-Node " must not match
    # "function Test-NodeVersionOk" -- both live in install.ps1.
    start = _SOURCE.index(f"function {name} ")
    brace = _SOURCE.index("{", start)
    depth = 0
    for i in range(brace, len(_SOURCE)):
        if _SOURCE[i] == "{":
            depth += 1
        elif _SOURCE[i] == "}":
            depth -= 1
            if depth == 0:
                return _SOURCE[start : i + 1]
    raise AssertionError(f"unterminated function {name}")


def _run_ps(body: str) -> subprocess.CompletedProcess:
    prelude = _STUBS + "".join(
        _function_text(n) + "\n"
        for n in (
            "Get-WindowsArch",
            "Install-Git",
            "Ensure-NodeExeOnPath",
            "Test-NodeVersionOk",
            "Install-ManagedNodeFromZip",
            "Test-Node",
            "Update-ProcessPathForPackages",
            "Install-SystemPackages",
        )
    )
    # The installer persists <data dir>\node to the USER PATH (that is how a
    # fresh shell finds node).  A test must not leave that behind -- snapshot
    # it, and put it back whatever happens.
    guard = (
        "$__savedUserPath = [Environment]::GetEnvironmentVariable('Path', 'User')\n"
        "try {\n"
        + body
        + "\n} finally {\n"
        "  [Environment]::SetEnvironmentVariable('Path', $__savedUserPath, 'User')\n"
        "}\n"
    )
    ps1 = Path(tempfile.mkdtemp(prefix="plobi-toolchain-ps-")) / "probe.ps1"
    ps1.write_text(prelude + guard, encoding="utf-8")

    try:
        return subprocess.run(
            [_POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1)],
            capture_output=True,
            text=True,
            timeout=300,
        )
    finally:
        shutil.rmtree(ps1.parent, ignore_errors=True)


def test_the_bundled_portable_git_is_used_instead_of_downloading(tmp_path: Path):
    """包里有 PortableGit ⇒ 不下载（真跑：掐掉 PATH，让 git 不在场）。

    给它一份**假的** PortableGit：它会去解包里那份并失败（解不开）——失败信息就是
    「它用的是包里那份」的证据；而 stdout 里没有 "Downloading" 说明它一次网都没碰。
    """
    toolchain = tmp_path / "toolchain"
    toolchain.mkdir()
    (toolchain / "PortableGit-9.9.9-64-bit.7z.exe").write_bytes(b"not a real archive")

    home = tmp_path / "home"
    q = chr(39)
    proc = _run_ps(
        # 掐掉 PATH 上的 git ⇒ 快路径不成立，只能走包里那份
        "$env:Path = 'C:\\Windows\\System32;C:\\Windows'\n"
        f"$PlobiHome = {q}{home}{q}\n"
        f"$ToolchainDir = {q}{toolchain}{q}\n"
        "Install-Git\n"
        'Write-Output "REACHED-END"\n'
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Downloading" not in proc.stdout, f"它去下载了，首启零下载就破了：{proc.stdout}"
    assert "Could not install portable Git" in proc.stdout, (
        f"它没有去用包里那份 PortableGit：{proc.stdout}{proc.stderr}"
    )
    assert "REACHED-END" in proc.stdout, proc.stdout


def test_the_toolchain_dir_is_a_parameter_of_the_installer():
    """`-ToolchainDir` 是安装脚本的一个参数——桌面壳把它连同包里的目录一起传下来。"""
    assert "[string]$ToolchainDir" in _SOURCE, "install.ps1 少了 -ToolchainDir 参数"


def test_the_bundled_ripgrep_has_a_branch_too():
    """ripgrep 同样有「包里那份」的分支——否则首启还是会去 winget 拉一次。"""
    body = _function_text("Install-SystemPackages")

    assert "ripgrep-*.zip" in body, "install.ps1 没有认包里那份 ripgrep"
    assert "$ToolchainDir" in body, "Install-SystemPackages 没看 -ToolchainDir"


def test_the_bundled_portable_git_is_never_deleted():
    """包里那份 PortableGit 用完**不许删**——它属于安装包，删了下次就装不成了。"""
    body = _function_text("Install-Git")

    assert "if ($tmpIsOurs)" in body, "临时文件只许删自己下载的那份"
    assert "Remove-Item -Force $tmpFile" in body


def test_uv_travels_with_the_package():
    """uv 也得进包：不然首启第一件事就是去 astral.sh 下载它。

    一台全新电脑上首启要下的东西越少，越接近「装完即用」。
    """
    body = _function_text("Install-Uv")
    collector = (_INSTALL_PS1.parents[1] / "apps" / "desktop" / "scripts" / "collect-toolchain-bundle.mjs").read_text(
        encoding="utf-8"
    )

    assert "uv-*-windows-*.zip" in body, "install.ps1 没有认包里那份 uv"
    assert "Using the uv that shipped with the installer" in body, "包里那份 uv 没用上"
    assert "uv-x86_64-pc-windows-msvc.zip" in collector, "收集脚本没有把 uv 收进包里"


def test_the_python_runtime_travels_with_the_package():
    """CPython 也得进包：`uv python install` 在全新电脑上就是一次下载。

    装机器脚本把包里那份解到 <数据目录>\\python，venv 直接建在它上面。
    """
    source = _SOURCE
    collector = (_INSTALL_PS1.parents[1] / "apps" / "desktop" / "scripts" / "collect-toolchain-bundle.mjs").read_text(
        encoding="utf-8"
    )

    assert "function Get-BundledPythonExe" in source, "没有取包里那份 CPython 的入口"
    assert "cpython-3.11*-install_only.tar.gz" in source, "install.ps1 没有认包里那份 CPython"
    assert "Python from the installer" in source, "python 档没有先试包里那份"
    assert "--python $pythonForVenv" in source, "venv 没有建在包里那份 CPython 上"
    assert "install_only" in collector and "python-build-standalone" in collector, (
        "收集脚本没有把 CPython 收进包里"
    )


def test_the_posix_leg_gets_the_same_treatment():
    """mac/linux 那条腿（install.sh）也要开箱即用：uv 与 CPython 一并进包。

    用户 11-07：「开箱即用的体验多平台也要做。」
    """
    posix = (_INSTALL_PS1.parents[1] / "scripts" / "install.sh").read_text(encoding="utf-8")
    runner = (
        _INSTALL_PS1.parents[1] / "apps" / "desktop" / "electron" / "bootstrap-runner.ts"
    ).read_text(encoding="utf-8")

    assert "--toolchain-dir|-ToolchainDir)" in posix, "install.sh 没有 --toolchain-dir 参数"
    assert "TOOLCHAIN_DIR=" in posix, "install.sh 没有 TOOLCHAIN_DIR 变量"
    assert "bundled_python_path()" in posix, "install.sh 没有取包里那份 CPython 的入口"
    assert "Using the uv that shipped with the installer" in posix, "install.sh 没有用包里那份 uv"
    assert "Python from the installer" in posix, "install.sh 没有用包里那份 CPython"
    assert '_python_for_venv="$_bundled_python"' in posix, "install.sh 的 venv 没有建在包里那份 CPython 上"
    assert "'--toolchain-dir', bundle.toolchain" in runner, "桌面壳没有把工具链目录传给 POSIX 那条腿"


def test_the_collector_covers_every_platform():
    """收集脚本按平台选资产：Windows 还要 PortableGit 与 ripgrep，mac/linux 自带 git。"""
    collector = (
        _INSTALL_PS1.parents[1] / "apps" / "desktop" / "scripts" / "collect-toolchain-bundle.mjs"
    ).read_text(encoding="utf-8")

    assert "process.platform !== 'win32'" not in collector, "收集脚本还只认 Windows"
    assert "apple-darwin" in collector, "macOS 的资产没进收集脚本"
    assert "unknown-linux-gnu" in collector, "Linux 的资产没进收集脚本"
    assert "needsGit" in collector and "needsRg" in collector, (
        "各平台要不要带 git/ripgrep 没有分开"
    )


# ── Node.js 与 ffmpeg：用户 11-07 拍「都进包」 ─────────────────────────────


def _write_node_zip(toolchain: Path, staging: Path) -> Path:
    """A Node.js zip shaped like the real one: one top folder holding node.exe.

    The stub is a Windows system binary that ignores its argument and exits 0
    -- all the installer asks of node.exe here is "answer --version".  (A copied
    python.exe does NOT work: a venv's launcher needs its pyvenv.cfg beside it,
    and a bare interpreter needs its DLL.)
    """
    import zipfile

    stub = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "whoami.exe"
    if not stub.is_file():  # pragma: no cover - a Windows machine always has it
        pytest.skip(f"这台没有 {stub}，做不出能跑的 node.exe 替身")

    inner = staging / "node-v99.0.0-win-x64"
    inner.mkdir(parents=True)
    shutil.copyfile(stub, inner / "node.exe")

    zip_path = toolchain / "node-v99.0.0-win-x64.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.write(inner / "node.exe", "node-v99.0.0-win-x64/node.exe")
    return zip_path


def test_the_bundled_node_is_used_instead_of_downloading(tmp_path: Path):
    """包里有 Node ⇒ 不下载（真跑：掐掉 PATH，让 node 不在场）。

    用户 11-07：「Node 与 ffmpeg 也进包」——浏览器工具与 TTS 语音要的就是它，
    让首启再下一份 Node，装完即用就成了空话。
    """
    toolchain = tmp_path / "toolchain"
    toolchain.mkdir()
    _write_node_zip(toolchain, tmp_path)

    home = tmp_path / "home"
    q = chr(39)
    proc = _run_ps(
        # 掐掉 PATH 上的 node ⇒ 快路径不成立，只能走包里那份
        "$env:Path = 'C:\\Windows\\System32;C:\\Windows'\n"
        f"$PlobiHome = {q}{home}{q}\n"
        f"$ToolchainDir = {q}{toolchain}{q}\n"
        "Test-Node\n"
        'Write-Output "REACHED-END"\n'
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Downloading" not in proc.stdout, f"它去下载 Node 了：{proc.stdout}"
    assert "ready (from the installer, no download)" in proc.stdout, (
        f"它没有去用包里那份 Node：{proc.stdout}{proc.stderr}"
    )
    assert (home / "node" / "node.exe").is_file(), "包里那份 Node 没落到 <数据目录>\\node"
    assert "REACHED-END" in proc.stdout, proc.stdout


def test_the_bundled_ffmpeg_is_used_instead_of_a_package_manager(tmp_path: Path):
    """包里有 ffmpeg ⇒ 不用 winget/choco/scoop（真跑）。

    装机器上的样子和 ripgrep 一样：<数据目录>\\bin\\ffmpeg.exe——后端的 PATH 里
    就有这一格，`shutil.which("ffmpeg")` 找得到。
    """
    import gzip
    import zipfile

    toolchain = tmp_path / "toolchain"
    toolchain.mkdir()

    # 一份能解开的 ripgrep：它让 needRipgrep 也归零，于是这个函数在碰
    # 包管理器之前就返回（不联网、不装任何东西）。
    rg_stage = tmp_path / "rg-stage" / "ripgrep-14.1.1"
    rg_stage.mkdir(parents=True)
    (rg_stage / "rg.exe").write_bytes(b"MZ stub")
    with zipfile.ZipFile(toolchain / "ripgrep-14.1.1-x86_64-pc-windows-msvc.zip", "w") as zf:
        zf.write(rg_stage / "rg.exe", "ripgrep-14.1.1/rg.exe")

    # 一份 ffmpeg：单个二进制压出来的 .gz（安装脚本按 >1MB 判它是真东西）。
    with gzip.open(toolchain / "ffmpeg-win32-x64.gz", "wb") as gz:
        gz.write(b"\0" * (1024 * 1024 + 512))

    home = tmp_path / "home"
    q = chr(39)
    proc = _run_ps(
        "$env:Path = 'C:\\Windows\\System32;C:\\Windows'\n"
        f"$PlobiHome = {q}{home}{q}\n"
        f"$ToolchainDir = {q}{toolchain}{q}\n"
        "Install-SystemPackages\n"
        'Write-Output "REACHED-END"\n'
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "winget" not in proc.stdout.lower(), f"它去问包管理器了：{proc.stdout}"
    assert "ffmpeg ready (from the installer, no download)" in proc.stdout, (
        f"它没有去用包里那份 ffmpeg：{proc.stdout}{proc.stderr}"
    )
    assert (home / "bin" / "ffmpeg.exe").is_file(), "包里那份 ffmpeg 没落到 <数据目录>\\bin"
    assert "REACHED-END" in proc.stdout, proc.stdout


def test_node_and_ffmpeg_travel_with_the_package():
    """Node 与 ffmpeg 得跟着包走（三个平台都要），且版本钉死。

    用户 11-07 拍板：「都进包。」
    """
    repo = _INSTALL_PS1.parents[1]
    collector = (repo / "apps" / "desktop" / "scripts" / "collect-toolchain-bundle.mjs").read_text(
        encoding="utf-8"
    )
    posix = (repo / "scripts" / "install.sh").read_text(encoding="utf-8")

    # 收集脚本：三个平台都收 node 与 ffmpeg，且版本是钉死的。
    assert "node-v${NODE.version}" in collector, "收集脚本没收 Node"
    assert "ffmpeg-static" in collector, "收集脚本没收 ffmpeg"
    assert "const NODE = {" in collector and "version:" in collector, "Node 版本没钉"
    assert "const FFMPEG = {" in collector and "tag:" in collector, "ffmpeg 版本没钉"
    assert "darwin-${macArch}" in collector, "macOS 的 Node 资产名不对"
    assert "linux-${linuxArch}" in collector, "Linux 的 Node 资产名不对"

    # Windows 那条腿：认包里那份 node 与 ffmpeg。
    assert "node-v*-win-*.zip" in _SOURCE, "install.ps1 没认包里那份 Node"
    assert "ffmpeg-*.gz" in _SOURCE, "install.ps1 没认包里那份 ffmpeg"
    assert "ready (from the installer, no download)" in _SOURCE, "装机器时没说是包里那份"

    # POSIX 那条腿：同一件事。
    assert "install_node_from_archive" in posix, "install.sh 没有取包里那份 Node 的入口"
    assert "node-v*-*.tar.gz" in posix, "install.sh 没认包里那份 Node"
    assert "ffmpeg-*.gz" in posix, "install.sh 没认包里那份 ffmpeg"
    assert 'gzip -dc "$_bundled_ffmpeg"' in posix, "install.sh 解包里那份 ffmpeg 的路子不对"
