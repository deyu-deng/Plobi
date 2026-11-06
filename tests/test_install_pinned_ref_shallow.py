"""WP-INSTALL-PINNED-REF-SHALLOW：装机把一份**已存在的浅 clone** 固定到发布 tag。

2026-11-06 装机态验收死在这一步：`git checkout v0.3.1 failed (exit 1)`，底下是
`pathspec 'v0.3.1' did not match any file(s) known to git` —— 因为
`git fetch origin <tag>` **只写 FETCH_HEAD、不建 refs/tags/<tag>**；而 CI 的 stamp 把
`branch` 写成 tag（`v0.3.2`），于是 `$Branch` 槽位里装的是一枚 tag。装机的浅 clone
（`--depth 1`）里更没有那枚 tag ⇒ 整台机器卡死在 repository 阶段。

修法在 install.ps1：分清分支与 tag，tag 用**显式 refspec**（`+refs/tags/X:refs/tags/X`）
把本地 ref 真建出来，再 detach checkout。

这条测试**真跑 PowerShell**（口径 27：脚本类不许只读文本），远端用**本地裸仓**
（不联网、不碰 GitHub）：
  · 裸仓：commit A 打 tag，再在 main 上落 commit B；
  · 浅 clone（`--depth 1 --branch main`）＝每个托管安装的形状（A 那枚 commit 不在本地）；
  · 跑 Install-Repository，两条槽位各来一次：`-Tag`（helper 那条）与 `-Branch`（CI stamp 那条）。
断言：rc=0、HEAD == tag 那一笔、本地 `refs/tags/<tag>` 真的建出来了。

**这条测试确实钉住了那个回归**（现读，2026-11-06）：把**修复前**的 install.ps1
（`32078e15^`）抠出来跑同一副夹具，`-Branch` 槽位装 tag 那条当场死：

    error: pathspec 'v9.9.9' did not match any file(s) known to git
    git checkout v9.9.9 failed (exit 1)

HEAD 停在错的提交上、`refs/tags/v9.9.9` 根本没建出来——与 2026-11-06 装机态验收
记录的那句 `pathspec 'v0.3.1' did not match any file(s) known to git` 同形。
（`-Tag` 槽位在修复前就有显式 refspec，所以那条本来就不在回归面内。）
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_PS1 = REPO_ROOT / "scripts" / "install.ps1"
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")
SOURCE = INSTALL_PS1.read_text(encoding="utf-8")

pytestmark = pytest.mark.skipif(
    POWERSHELL is None,
    reason="这台没有 PowerShell，install.ps1 的行为无从真跑（不是通过）",
)

TAG = "v9.9.9"


def _function_text(name: str) -> str:
    """把 install.ps1 里某个函数的源码原样抠出来（与 test_install_ps1_data_dir.py 同一手法）。"""
    start = SOURCE.index(f"function {name}")
    brace = SOURCE.index("{", start)
    depth = 0
    for i in range(brace, len(SOURCE)):
        if SOURCE[i] == "{":
            depth += 1
        elif SOURCE[i] == "}":
            depth -= 1
            if depth == 0:
                return SOURCE[start : i + 1]
    raise AssertionError(f"unterminated function {name}")


def _git_env() -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "GIT_AUTHOR_NAME": "install-probe",
            "GIT_AUTHOR_EMAIL": "install-probe@example.invalid",
            "GIT_COMMITTER_NAME": "install-probe",
            "GIT_COMMITTER_EMAIL": "install-probe@example.invalid",
            "GIT_CONFIG_NOSYSTEM": "1",
        }
    )
    return env


def _git(args: list[str], cwd: Path) -> str:
    r = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=_git_env(),
        timeout=180,
    )
    assert r.returncode == 0, f"git {' '.join(args)} failed:\n{r.stdout}\n{r.stderr}"
    return r.stdout.strip()


@pytest.fixture(scope="module")
def _remote_with_tag(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str]:
    """一个本地裸仓（带 tag）+ 浅 clone 的 main，返回（clone 目录, tag 那笔 commit）。"""
    root = tmp_path_factory.mktemp("plobi-install-ref")
    work = root / "work"
    work.mkdir()

    _git(["init", "-b", "main"], work)
    (work / "a.txt").write_text("a\n", encoding="utf-8")
    _git(["add", "-A"], work)
    _git(["commit", "-m", "A"], work)
    _git(["tag", TAG], work)
    tagged = _git(["rev-parse", "HEAD"], work)

    # main 再往前落一笔：浅 clone（--depth 1）只带它 ⇒ tag 那笔不在本地
    (work / "b.txt").write_text("b\n", encoding="utf-8")
    _git(["add", "-A"], work)
    _git(["commit", "-m", "B"], work)

    bare = root / "remote.git"
    _git(["clone", "--bare", str(work), str(bare)], root)

    clone_dir = root / "plobi-agent"
    url = "file:///" + bare.as_posix()
    subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", "main", url, str(clone_dir)],
        capture_output=True,
        text=True,
        env=_git_env(),
        timeout=300,
        check=True,
    )

    shallow_file = clone_dir / ".git" / "shallow"
    assert shallow_file.exists(), "夹具不是浅 clone，后面的断言就白搭了"
    assert tagged not in _git(["rev-list", "HEAD"], clone_dir).split("\n")

    return clone_dir, tagged


def _run_install_repository(install_dir: Path, *, tag: str = "", branch: str = "main") -> subprocess.CompletedProcess:
    """真跑 install.ps1 的 repository stage（只抠出它要用的那几个函数）。"""
    stubs = (
        "$ErrorActionPreference = 'Stop'\n"
        "function Write-Info { param([string]$Message) Write-Output \"INFO:$Message\" }\n"
        "function Write-Success { param([string]$Message) Write-Output \"OK:$Message\" }\n"
        "function Write-Warn { param([string]$Message) Write-Output \"WARN:$Message\" }\n"
        "function Write-Err { param([string]$Message) Write-Output \"ERR:$Message\" }\n"
    )
    prelude = stubs + _function_text("Install-Repository") + "\n" + _function_text("Discard-LockfileChurn") + "\n"
    body = (
        f"$InstallDir = '{install_dir}'\n"
        f"$Branch = '{branch}'\n"
        "$Commit = ''\n"
        f"$Tag = '{tag}'\n"
        "$NonInteractive = $true\n"
        "Install-Repository\n"
    )

    probe = install_dir.parent / "install-repository-probe.ps1"
    probe.write_text(prelude + body, encoding="utf-8")
    try:
        return subprocess.run(
            [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(probe)],
            capture_output=True,
            text=True,
            timeout=300,
            env=_child_env(install_dir),
        )
    finally:
        probe.unlink(missing_ok=True)


def _git_bin_dirs() -> list[str]:
    """git 可执行文件所在的目录（给 PowerShell 子进程补 PATH 用）。

    测试入口用 `env -i` 起进程，PATH 是 MSYS 的 **POSIX 形状**（`/d/Software/Git/cmd:…`）
    ⇒ 传给 Windows 原生程序时那串路径没人认，PowerShell 里 `git` 就是「不是可识别的命令」。
    `shutil.which` 在那种 PATH 上也可能落空 ⇒ 退一步问 git 自己：它报 `--exec-path`，
    从那儿反推 Git 根目录，再把 `cmd`／`bin` 都加上。
    """
    dirs: list[str] = []

    exe = shutil.which("git")
    if exe:
        dirs.append(str(Path(exe).parent))

    try:
        probe = subprocess.run(
            ["git", "--exec-path"], capture_output=True, text=True, timeout=60
        )
        root = Path(probe.stdout.strip())
        for _ in range(3):
            root = root.parent
        for sub in ("cmd", "bin", "mingw64/bin"):
            dirs.append(str(root / sub))
    except Exception:  # noqa: BLE001 — 问不到就只靠 which 那条
        pass

    return [d for d in dirs if d]


def _child_env(install_dir: Path) -> dict[str, str]:
    """给 PowerShell 子进程一份能用的环境。

    两件事：
    · **git 要在 PATH 上**——测试入口把 PATH 收窄过（还是 POSIX 形状），子进程里
      `git` 会「不是可识别的命令」，于是脚本以为那不是一份检出，转身去走克隆那条路
      （既慢又不测我们这条）。
    · **GIT_CONFIG_GLOBAL 指到临时文件**——脚本里有一句 `git config --global
      windows.appendAtomically false`，它必须改这份配置，但**不许改用户的全局配置**。
    """
    env = dict(os.environ)
    extra = os.pathsep.join(_git_bin_dirs())
    env["PATH"] = f"{extra}{os.pathsep}{env.get('PATH', '')}" if extra else env.get("PATH", "")
    # PATHEXT 也要补：测试入口用 `env -i` 起进程，它只传 PATH，**PATHEXT 被剥了** ⇒
    # PowerShell 认不出 PATH 上那些**没有扩展名**的命令（`git`）⇒
    # 「The term 'git' is not recognized」。Python 的 subprocess 走 CreateProcess，
    # 它自己会补 .exe，所以夹具那几步没受影响——只有 PowerShell 子进程会中招。
    env["PATHEXT"] = os.environ.get("PATHEXT") or ".COM;.EXE;.BAT;.CMD;.VBS;.JS;.WSF;.MSC"
    env["GIT_CONFIG_GLOBAL"] = str(install_dir.parent / "probe-gitconfig")
    return env


def test_tag_slot_moves_an_existing_shallow_checkout_onto_the_tag(_remote_with_tag) -> None:
    """`-Tag` 那条槽位（helper 用的就是它）：浅 clone 落到 tag 那一笔。"""
    clone_dir, tagged = _remote_with_tag

    proc = _run_install_repository(clone_dir, tag=TAG)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _git(["rev-parse", "HEAD"], clone_dir) == tagged
    assert _git(["rev-parse", "--verify", f"refs/tags/{TAG}"], clone_dir) == tagged


def test_branch_slot_carrying_a_tag_also_lands_on_the_tag(_remote_with_tag) -> None:
    """`-Branch` 槽位里装的是一枚 tag（CI stamp 的形状）：同样要落到 tag 那一笔。"""
    clone_dir, tagged = _remote_with_tag

    proc = _run_install_repository(clone_dir, tag="", branch=TAG)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _git(["rev-parse", "HEAD"], clone_dir) == tagged
    assert _git(["rev-parse", "--verify", f"refs/tags/{TAG}"], clone_dir) == tagged


def test_a_tag_that_does_not_exist_fails_loudly(_remote_with_tag) -> None:
    """远端没有的那枚 tag：必须报错，不许静默当成「已是最新」。"""
    clone_dir, _tagged = _remote_with_tag

    proc = _run_install_repository(clone_dir, tag="v0.0.0-does-not-exist")

    assert proc.returncode != 0, "不存在的 tag 竟然被当成成功"
    assert "v0.0.0-does-not-exist" in (proc.stdout + proc.stderr)


def test_the_helpers_next_step_leaves_the_checkout_on_a_branch(_remote_with_tag) -> None:
    """helper 的第 1b 步：tag checkout 之后要把 `main` 重新指到那一版并切回去。

    不做这一步，检出就停在 **detached HEAD** 上 ⇒ 下一次更新没有分支可推
    （`plobi update` 判 detached 而拒绝）⇒ 这台机器第二次就更新不了了。
    那是 2026-11-06 那笔 helper 修复要治的病，这里把它钉住。
    """
    clone_dir, tagged = _remote_with_tag

    # 先走安装器那一步：落到 tag 上（detached）。
    proc = _run_install_repository(clone_dir, tag=TAG)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _git(["rev-parse", "HEAD"], clone_dir) == tagged

    # 再走 helper 的第 1b 步。
    # 注意：`git checkout main` 会**在 stdout 上打一句提示**（main 与 origin/main 分叉了——
    # 因为 main 现在指的是那一版 tag，而 origin/main 还指着浅 clone 的尖端），
    # 那不是错误（rc=0），所以这里看返回码，不看 stdout 是不是空的。
    assert _git(["branch", "-f", "main", f"refs/tags/{TAG}"], clone_dir) is not None
    assert _git(["checkout", "main"], clone_dir) is not None

    assert _git(["symbolic-ref", "--short", "HEAD"], clone_dir) == "main"
    assert _git(["rev-parse", "main"], clone_dir) == tagged
    assert _git(["rev-parse", "HEAD"], clone_dir) == tagged
