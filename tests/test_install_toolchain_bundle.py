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
    start = _SOURCE.index(f"function {name}")
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
        _function_text(n) + "\n" for n in ("Get-WindowsArch", "Install-Git")
    )
    ps1 = Path(tempfile.mkdtemp(prefix="plobi-toolchain-ps-")) / "probe.ps1"
    ps1.write_text(prelude + body, encoding="utf-8")

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
