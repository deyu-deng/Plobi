"""浅 clone 不能再让「落后」被读成「分叉」——不然每个托管安装都更新不了。

装机器给每一个托管安装都建 ``--depth 1`` 的浅 clone
（``scripts/install.ps1`` 的 ``git clone --depth 1 --branch $Branch``），
而浅 clone 的 graft 把祖先关系藏起来：

- ``rev-list origin/main..HEAD`` 会把 HEAD 自己数成「本地独有的提交」；
- ``merge-base --is-ancestor HEAD origin/main`` 也会失败。

于是「明明只是落后一天」被判成「本地有提交没在 origin/main 上」，
更新路径直接拒绝——2026-11-06 的装机验收实测就是这个形状。

这些用例用**真 git 仓**（本地 bare 远端，file:// 走浅 clone），不联网。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from plobi_cli.self_update import git_update_state

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_PS1 = REPO_ROOT / "scripts" / "install.ps1"


def _git(cwd, *args, check=True):
    r = subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=T",
         "-c", "commit.gpgsign=false", *args],
        cwd=str(cwd), capture_output=True, text=True, errors="replace")
    if check:
        assert r.returncode == 0, f"git {args} 失败：{r.stdout}{r.stderr}"
    return r


def _out(cwd, *args):
    return (_git(cwd, *args).stdout or "").strip()


def _make_remote(tmp_path):
    """一个 bare 远端 + 一个推了 seed 的克隆。"""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)],
                   capture_output=True, text=True, check=True)
    seed = tmp_path / "seed"
    _git(tmp_path, "clone", str(remote), "seed")
    (seed / "seed.txt").write_text("seed", encoding="utf-8")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-m", "seed")
    _git(seed, "push", "-u", "origin", "main")
    return remote


def _advance(remote, tmp_path, name):
    """在另一个克隆里加一个提交并推上去 —— 让被测的检出落后。"""
    other = tmp_path / f"other-{name}"
    _git(tmp_path, "clone", str(remote), other.name)
    (other / f"{name}.txt").write_text(name, encoding="utf-8")
    _git(other, "add", "-A")
    _git(other, "commit", "-m", name)
    _git(other, "push", "origin", "main")
    return other


def _shallow_clone(remote, tmp_path, name="shallow"):
    """用 file:// 克隆才能真的浅（本地路径克隆会忽略 --depth）。"""
    work = tmp_path / name
    _git(tmp_path, "clone", "--depth", "1", "--branch", "main",
         f"file://{remote.as_posix()}", name)
    assert _out(work, "rev-parse", "--is-shallow-repository") == "true"
    return work


class TestShallowCheckoutIsNotDiverged:
    def test_behind_shallow_checkout_applies_after_deepening(self, tmp_path):
        remote = _make_remote(tmp_path)
        work = _shallow_clone(remote, tmp_path)
        _advance(remote, tmp_path, "one")

        state = git_update_state(work, fetch=True)

        assert state["reason"] == "ok", state
        assert state["applies"] is True, state
        assert state["behind"] == 1, state
        # 浅 clone 的假「本地领先」不许再出现
        assert state["ahead"] == 0, state
        # 补深之后它就不再是浅的了
        assert state["shallow"] is False, state

    def test_offline_check_says_shallow_not_diverged(self, tmp_path):
        remote = _make_remote(tmp_path)
        work = _shallow_clone(remote, tmp_path)
        _advance(remote, tmp_path, "two")
        # 让本地知道远端往前走了（一次本地 fetch，不是联网）：离线路径读的就是
        # 这个「上次见到的远端」——它不动，所以落后格数算得出来，但「本地领先」
        # 判不出来（浅 clone 的 graft）。
        _git(work, "fetch", "origin", "main")

        state = git_update_state(work, fetch=False)

        # 不联网时判不出「本地领先」——如实说，别冤枉人
        assert state["reason"] == "ok", state
        assert state["shallow"] is True, state
        assert state["ahead"] is None, state
        assert state["behind"] == 1, state
        # apply 那一步会先补深再复核，所以这条路仍然可走
        assert state["applies"] is True, state

    def test_up_to_date_shallow_checkout_says_up_to_date(self, tmp_path):
        remote = _make_remote(tmp_path)
        work = _shallow_clone(remote, tmp_path)

        state = git_update_state(work, fetch=False)

        assert state["reason"] == "up_to_date", state
        assert state["behind"] == 0 and state["applies"] is False


class TestInstallerFetchesATagIntoALocalRef:
    """安装器的 -Branch 槽位可能装的是**发布 tag**（CI 的 stamp 里 branch 写的就是 tag）。

    ``git fetch origin <tag>`` 只写 FETCH_HEAD，**不建** ``refs/tags/<tag>``，
    紧跟着的 ``git checkout <tag>`` 于是报
    ``pathspec 'v0.3.1' did not match any file(s) known to git``，
    装机卡死在 repository 阶段（2026-11-06 装机实测）。
    """

    def test_branch_path_fetches_a_tag_with_an_explicit_refspec(self):
        text = INSTALL_PS1.read_text(encoding="utf-8")

        # 1) 先分清 $Branch 到底是分支还是 tag
        detect = 'rev-parse --verify --quiet "refs/remotes/origin/$Branch"'
        assert detect in text, "install.ps1 没分清 $Branch 是分支还是 tag"

        # 2) tag 那条：用显式 refspec 把 refs/tags/<tag> 建出来（否则 checkout 必报
        #    "pathspec 'v0.3.1' did not match any file(s) known to git"）
        fetch_tag = 'fetch origin "+refs/tags/${Branch}:refs/tags/${Branch}"'
        checkout_tag = 'checkout --detach "refs/tags/$Branch"'
        assert fetch_tag in text, "install.ps1 没有用显式 refspec 拉 tag"
        assert checkout_tag in text, "install.ps1 没有 checkout 本地 tag ref"

        # 3) 分支那条不许被挤掉
        assert "checkout $Branch" in text
        assert "pull --ff-only origin $Branch" in text

        # 4) 次序：先判种类，再拉，再 checkout
        assert text.index(detect) < text.index(fetch_tag) < text.index(checkout_tag)
