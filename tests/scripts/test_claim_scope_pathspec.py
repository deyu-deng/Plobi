"""claim 门在 pathspec 提交下的行为契约（WP-COMMIT-PATHSPEC）。

起因是 11-20 一次误诊：我当时用裸 `git commit -m` 把主索引里别人 38 档一起提交，
claim 门如实判越界红，我却以为门读了"全量"是 bug。仓外实验证明：**门没错**——
`git commit --only -- <paths>` 时 git 把 GIT_INDEX_FILE 指向临时索引，钩子天然只看到
本笔文件。这两条测试把那个事实钉进仓里，防止下次又误诊到门头上。

全部跑在 tmp_path 的临时仓；不用 mock，真起 git、真装钩子、真提交。

两条不变量：
  1. 主索引混着"别人的档"，pathspec 提交只提交自己的档 → 门绿（钩子看到临时索引）；
  2. 主索引同样的混法，裸 commit（无 pathspec）→ 门如实判红（提交确实越界）。

第 2 条是反证：证明门没被改松，它对"真的越界"仍然红。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check_claim_scope.py"

HEADER = (
    "| 刀 / WP | 谁在干 | 在哪 | 认领的文件族 | 状态 | 最后活动 | commit 前缀 |\n"
    "|---|---|---|---|---|---|---|\n"
)


def _row(wp: str, families: str) -> str:
    return f"| **{wp}** 测试线 | 会话 | 仓 | {families} | **在派** | 09:0x | `[{wp}]` |\n"


def _env(**extra: str) -> dict[str, str]:
    """干净环境：继承 PATH，拔掉 WP_COMMIT 与 git 自己注入的 GIT_*（钩子里会串台）。"""
    e = {k: v for k, v in os.environ.items() if k != "WP_COMMIT" and not k.startswith("GIT_")}
    e.update(extra)
    return e


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    p = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, env=_env())
    if check:
        assert p.returncode == 0, f"git {' '.join(args)} failed: {p.stderr}"
    return p


def _setup(tmp_path: Path) -> tuple[Path, Path]:
    """临时仓 + 临时登记册；主索引里故意混进"别人的档"。"""
    repo = tmp_path / "Code"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")

    (repo / "mine.py").write_text("mine-v1\n", encoding="utf-8")
    (repo / "others.py").write_text("others-v1\n", encoding="utf-8")
    _git(repo, "add", "--", "mine.py", "others.py")
    _git(repo, "commit", "-qm", "base")

    (repo / "mine.py").write_text("mine-v2\n", encoding="utf-8")
    (repo / "others.py").write_text("others-v2\n", encoding="utf-8")
    _git(repo, "add", "--", "mine.py", "others.py")  # 两档都进主索引

    register = tmp_path / "agent-register.md"
    register.write_text(
        HEADER
        + _row("WP-MINE", "`mine.py`")
        + _row("WP-OTHERS", "`others.py`"),
        encoding="utf-8",
    )

    # 装两个钩子，精确复刻真钩子的分工（裁定 80）：
    #   pre-commit —— 认不出前缀，只判硬冲突；
    #   commit-msg —— 从"正在写的消息文件"读出 [WP-XXX]、注入 WP_COMMIT、再判归属。
    # 归属判定在 commit-msg 这一步，所以一定要把它装上，否则测不出真链路。
    gate_cmd = (
        f'"{sys.executable}" "{SCRIPT}" --repo "{repo}" --register "{register}"'
    )
    pre = repo / ".git" / "hooks" / "pre-commit"
    pre.write_text("#!/bin/sh\n" + gate_cmd + "\n", encoding="utf-8")
    pre.chmod(0o755)
    cm = repo / ".git" / "hooks" / "commit-msg"
    cm.write_text(
        "#!/bin/sh\n"
        'WP=$(sed -n \'s/.*\\[\\(WP-[A-Z0-9-]*\\)\\].*/\\1/p\' "$1" | head -n 1)\n'
        f'WP_COMMIT="$WP" {gate_cmd}\n',
        encoding="utf-8",
    )
    cm.chmod(0o755)
    return repo, register


def test_pathspec_commit_sees_only_own_file_gate_green(tmp_path: Path) -> None:
    """主索引混着别人的档，但 pathspec 提交只提交自己的档 → 门绿。

    这是 11-20 误诊的反面：门没有读"全量"，它读的是 git 给的临时索引。
    """
    repo, _register = _setup(tmp_path)
    p = _git(
        repo, "commit", "--only", "-m", "[WP-MINE] pathspec", "--", "mine.py", check=False
    )
    assert p.returncode == 0, f"pathspec commit 应成功: {p.stdout}\n{p.stderr}"
    assert "RED" not in p.stdout + p.stderr
    # 别人的档没被这笔带走：它仍停在主索引的暂存态
    staged = _git(repo, "diff", "--cached", "--name-only").stdout.strip().splitlines()
    assert staged == ["others.py"], staged


def test_bare_commit_with_mixed_index_is_red(tmp_path: Path) -> None:
    """同样的混合主索引，裸 commit（无 pathspec）→ 门如实判红。

    反证：证明门没被改松。它拦的不是"索引里有别人档"，而是"你真的要把别人档提交进去"。
    """
    repo, _register = _setup(tmp_path)
    p = _git(repo, "commit", "-m", "[WP-MINE] bare", check=False)
    assert p.returncode != 0, "裸 commit 把别人的档一起提交，门必须拦"
    out = p.stdout + p.stderr
    assert "RED" in out
    assert "WP-OTHERS" in out, "应当指出越界到谁的认领族"
    # 门拦下了，HEAD 里不该有这笔
    head = _git(repo, "log", "-1", "--format=%s").stdout.strip()
    assert head == "base", head
