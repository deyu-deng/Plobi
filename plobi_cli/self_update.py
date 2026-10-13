"""「这台能不能就地应用更新、为什么不能」的唯一判据。

仪表盘与桌面共用这里的答案，不许各写一份 `install_method in (...)` —— 那种抄本正是
R-046 的形状：按钮返回成功、什么都没发生。

git 检出以前是**整条冻结**（2026-09-06/07 两次摧毁工作树的事故之后）。冻结是对的，
冻错了东西：真正不许做的是 `reset --hard` / `rebase` / 切分支 / 动未提交的行，
而不是"把主线快进一格"。所以这里把"不许"拆成**逐个可报的前置条件**，全过才动，
且只走 `merge --ff-only` 这一条不会改写历史的路：

- 工作树必须干净（这台有并发会话在写，脏 = 别人有未提交的活，一行都不许碰）；
- 不许 detached HEAD、不许有本地未推的提交（那样快进不了，硬来就是重写历史）；
- 拉不到就说拉不到，**绝不退回上游开源项目**（裁定 R-043：本仓的更新源就是我们自己的远端）。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

SELF_UPDATE_FROZEN_MSG = (
    "这个安装方式没有被接进就地更新（只有自管虚拟环境的 pip 安装与干净的 git 检出可以）。"
    "请在带外更新，或把这台换成上面两种形态之一。"
)

#: 只有自管虚拟环境的那几条路能靠包管理器就地升级。
_APPLIABLE_METHODS = ("pip",)

#: 远端名可用环境变量指（默认 origin）。**不许**指向上游开源项目。
REMOTE_ENV = "PLOBI_UPDATE_REMOTE"

_GIT_TIMEOUT = 120


def local_self_update_applies(install_method: str, repo_root: Optional[Path] = None) -> bool:
    """True 仅当 `plobi update` 真能把更新落到这台机器上（不联网的快速前置判断）。"""
    if install_method in _APPLIABLE_METHODS:
        return True
    if install_method == "git":
        return git_update_state(repo_root, fetch=False)["applies"]
    return False


def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=_GIT_TIMEOUT,
    )


def _tail(text: str, limit: int = 240) -> str:
    clean = " ".join((text or "").split())
    return clean[-limit:]


def _pick_remote(repo_root: Path) -> Optional[str]:
    remotes = [r for r in _git(repo_root, "remote").stdout.splitlines() if r.strip()]
    if not remotes:
        return None
    want = (os.environ.get(REMOTE_ENV) or "").strip()
    if want:
        return want if want in remotes else None
    if "origin" in remotes:
        return "origin"
    return remotes[0] if len(remotes) == 1 else None


def git_update_state(repo_root: Optional[Path], *, fetch: bool) -> Dict[str, Any]:
    """这台 git 检出的就地更新前置条件。每个拒绝态单独可报，不静默、不猜。

    ``fetch=False`` 只做本地判断（给仪表盘 `can_apply` 用，不联网）；
    ``fetch=True`` 才拿得到真实的落后格数（给"真去应用"那一步用）。
    """
    root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[1]
    st: Dict[str, Any] = {
        "install_root": str(root), "is_git": False, "applies": False, "reason": "",
        "remote": None, "branch": None, "head": None, "remote_ref": None,
        "behind": None, "ahead": None, "dirty_files": 0, "detail": "",
    }
    if not (root / ".git").exists():
        st["reason"] = "not_a_git_checkout"
        return st
    st["is_git"] = True

    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if not branch or branch == "HEAD":
        st["reason"] = "detached_head"
        st["detail"] = "不在任何分支上，快进没有目标可言。"
        return st
    st["branch"] = branch

    remote = _pick_remote(root)
    if not remote:
        st["reason"] = "no_remote"
        st["detail"] = f"没有可用远端（或 {REMOTE_ENV} 指向了一个不存在的远端）。"
        return st
    st["remote"] = remote
    st["remote_ref"] = f"{remote}/{branch}"

    st["head"] = _git(root, "rev-parse", "--short=8", "HEAD").stdout.strip()
    dirty = [l for l in _git(root, "status", "--porcelain").stdout.splitlines() if l.strip()]
    st["dirty_files"] = len(dirty)
    if dirty:
        st["reason"] = "working_tree_dirty"
        st["detail"] = (f"{len(dirty)} 个未提交改动 —— 这台有多个会话并发在写，"
                        f"脏的行一律不碰。先提交或还原，再更新。")
        return st

    if fetch:
        got = _git(root, "fetch", "--quiet", remote, branch)
        if got.returncode != 0:
            st["reason"] = "fetch_failed"
            st["detail"] = _tail(got.stderr or got.stdout) or f"拉不到 {remote}/{branch}。"
            return st

    ref = st["remote_ref"]
    if _git(root, "rev-parse", "--verify", "--quiet", ref).returncode != 0:
        st["reason"] = "missing_remote_branch"
        st["detail"] = f"远端上没有 {branch} 这条分支。"
        return st

    st["behind"] = int(_git(root, "rev-list", "--count", f"HEAD..{ref}").stdout.strip() or 0)
    st["ahead"] = int(_git(root, "rev-list", "--count", f"{ref}..HEAD").stdout.strip() or 0)
    if st["ahead"]:
        st["reason"] = "diverged"
        st["detail"] = (f"本地有 {st['ahead']} 个提交没在 {ref} 上 —— 就地更新不许 rebase、"
                        f"不许 merge 提交，先把这些行推上去或处理掉。")
        return st
    if not st["behind"]:
        st["reason"] = "up_to_date"
        return st
    if _git(root, "merge-base", "--is-ancestor", "HEAD", ref).returncode != 0:
        st["reason"] = "not_fast_forwardable"
        st["detail"] = f"{ref} 不是 HEAD 的后代（远端被重写过？）—— 拒绝动工作树。"
        return st

    st["applies"] = True
    st["reason"] = "ok"
    return st


def apply_git_update(repo_root: Optional[Path] = None) -> Dict[str, Any]:
    """真去应用一次：拉我们自己那条远端，然后 `merge --ff-only`。

    成功返回 ``{ok: True, head, after, behind}``（仪表盘把它映射成 before/after/moved）；
    任何不满足的前置都返回
    ``{ok: False, reason, detail}`` —— 不起子进程空转、不写成功台账。
    """
    st = git_update_state(repo_root, fetch=True)
    root = Path(st["install_root"])
    if not st["applies"]:
        return {"ok": False, **st}
    merged = _git(root, "merge", "--ff-only", st["remote_ref"])
    if merged.returncode != 0:
        st["reason"] = "merge_failed"
        st["detail"] = _tail(merged.stderr or merged.stdout)
        st["applies"] = False
        return {"ok": False, **st}
    st["after"] = _git(root, "rev-parse", "--short=8", "HEAD").stdout.strip()
    return {"ok": True, **st}


def refusal(install_method: str, update_command: str, state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """仪表盘 `POST /api/plobi/update` 在按不动时该返回的形状。"""
    reason = (state or {}).get("reason") or "self_update_frozen"
    message = (state or {}).get("detail") or SELF_UPDATE_FROZEN_MSG
    return {
        "ok": False,
        "pid": None,
        "name": "plobi-update",
        "error": reason,
        "message": message,
        "update_command": update_command,
        "behind": (state or {}).get("behind"),
        "dirty_files": (state or {}).get("dirty_files"),
    }
