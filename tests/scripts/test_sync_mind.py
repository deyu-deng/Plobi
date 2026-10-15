"""WP-MIND-SYNC-SCHEDULED: the brain's own push/pull, tested against a local remote.

Every case here is fully offline — a bare repo in the tempdir stands in for
``origin`` — because the thing under test is the **decision**, not the network:
fast-forward when the other machine moved, refuse outright when the two sides
diverged, never touch a dirty worktree, and say so in a way a scheduled task's
exit code carries. The network leg on the real brain cannot be exercised from a
test, and pretending otherwise would be a green that means nothing.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "sync_mind.py"

RC_OK, RC_DIVERGED, RC_NOT_BRAIN, RC_NO_REMOTE, RC_DIRTY = 0, 20, 21, 22, 23
ENV = {
    k: v for k, v in os.environ.items() if k not in ("MIND_ROOT", "VAULT_ROOT")
}
ENV.update(
    GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
    GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid",
    GIT_TERMINAL_PROMPT="0",
)


def g(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", env=ENV)


def run(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), "--mind-root", str(root), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=ENV, timeout=180)


def commit_file(repo: Path, name: str, body: str) -> str:
    (repo / name).write_text(body, encoding="utf-8")
    assert g(repo, "add", "--", name).returncode == 0
    assert g(repo, "commit", "-q", "-m", name).returncode == 0
    return g(repo, "rev-parse", "HEAD").stdout.strip()


@pytest.fixture()
def brain(tmp_path):
    """A bare origin + a clone of it, with one shared commit already pushed."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True, env=ENV)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(origin), str(seed)], check=True, env=ENV)
    commit_file(seed, "diary.md", "第一天\n")
    assert g(seed, "push", "-q", "origin", "HEAD:refs/heads/main").returncode == 0
    mind = tmp_path / "mind"
    subprocess.run(["git", "clone", "-q", str(origin), str(mind)], check=True, env=ENV)
    # 空裸仓 clone 出来是 detached HEAD：显式落到 main 并认 upstream，与真脑一致
    g(mind, "checkout", "-q", "-B", "main", "origin/main")
    assert g(mind, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "main"
    assert "origin/main" in g(mind, "rev-parse", "--abbrev-ref", "main@{upstream}").stdout
    return mind, origin


def head_of(repo: Path) -> str:
    return g(repo, "rev-parse", "HEAD").stdout.strip()


# ---- the two halves that the ruling asks for --------------------------------


def test_push_leg_clears_ahead_and_the_other_machine_gets_it(brain):
    mind, origin = brain
    sha = commit_file(mind, "diary.md", "第一天\n第二天\n")
    assert g(mind, "rev-list", "--count", "@{u}..HEAD").stdout.strip() == "1"

    proc = run(mind)
    assert proc.returncode == RC_OK, proc.stdout + proc.stderr
    assert "push ok" in proc.stdout

    # 判据①的形状：归零是可证的，不是承诺
    assert g(mind, "rev-list", "--count", "@{u}..HEAD").stdout.strip() == "0"
    remote_refs = subprocess.run(["git", "ls-remote", str(origin), "refs/heads/main"],
                                 capture_output=True, text=True, env=ENV).stdout
    assert sha in remote_refs


def test_pull_leg_fast_forwards_when_the_other_machine_moved(brain, tmp_path):
    mind, origin = brain
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True, env=ENV)
    incoming = commit_file(other, "notes.md", "另一台写的笔记\n")
    assert g(other, "push", "-q", "origin", "HEAD:refs/heads/main").returncode == 0

    proc = run(mind)
    assert proc.returncode == RC_OK, proc.stdout + proc.stderr
    assert "pull ok" in proc.stdout
    assert head_of(mind) == incoming                       # 真的追上了
    assert (mind / "notes.md").read_text(encoding="utf-8").startswith("另一台")
    assert g(mind, "rev-list", "--count", "HEAD..@{u}").stdout.strip() == "0"


def test_both_legs_run_in_sequence_not_in_one_shot(brain, tmp_path):
    """判据②的正解：同一次运行里两侧各自前进 = 真分叉，ff 不可能，脚本必须拒绝。

    所以"推与拉都在排"要这样验：一趟快进追上，本机再写一笔，下一趟推上去。
    写成"一趟同时做两件事"是错的期望——那正是我不肯自动合脑的理由。
    """
    mind, origin = brain
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True, env=ENV)
    commit_file(other, "from-other.md", "别的机器\n")
    g(other, "push", "-q", "origin", "HEAD:refs/heads/main")

    first = run(mind)
    assert first.returncode == RC_OK, first.stdout + first.stderr
    assert "pull ok" in first.stdout and "push ok" not in first.stdout

    mine = commit_file(mind, "diary.md", "追上之后本机再写一笔\n")
    second = run(mind)
    assert second.returncode == RC_OK, second.stdout + second.stderr
    assert "push ok" in second.stdout
    assert g(mind, "rev-list", "--count", "@{u}..HEAD").stdout.strip() == "0"
    remote = subprocess.run(["git", "ls-remote", str(origin), "refs/heads/main"],
                            capture_output=True, text=True, env=ENV).stdout
    assert mine in remote


def test_divergence_refuses_instead_of_inventing_a_merge(brain, tmp_path):
    """两侧都前进 ⇒ 出声（RC_DIVERGED）：不产生合并提交、不动盘、不推。"""
    mind, origin = brain
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True, env=ENV)
    commit_file(other, "from-other.md", "别的机器\n")
    g(other, "push", "-q", "origin", "HEAD:refs/heads/main")
    commit_file(mind, "diary.md", "本机也写了\n")

    proc = run(mind)
    assert proc.returncode == RC_DIVERGED, proc.stdout
    assert "真分叉" in proc.stdout
    parents = [l for l in g(mind, "cat-file", "-p", "HEAD").stdout.splitlines()
               if l.startswith("parent ") or l.startswith("parent")]
    assert len(parents) == 1, parents                  # 还是普通一笔，没被合成两个父
    assert (mind / "from-other.md").exists() is False  # 没把别人的内容落到盘上
    assert g(mind, "rev-list", "--count", "@{u}..HEAD").stdout.strip() == "1"  # 也没推
def test_dirty_worktree_stops_both_legs_until_a_human_comes(brain, tmp_path):
    """脑的写入方（桌面/代理）可能正在写。这时两条腿都不动，一条话说清。

    落后时 pull 会改工作树 ⇒ 不 pull；而落后时 push 必被拒 ⇒ 也不去撞那一次网络。
    只脏、不落后时照推 —— 推不动工作树，所以不需要谁同意。
    """
    mind, origin = brain
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True, env=ENV)
    commit_file(other, "from-other.md", "别的机器\n")
    g(other, "push", "-q", "origin", "HEAD:refs/heads/main")
    (mind / "正在写.md").write_text("桌面此刻在写\n", encoding="utf-8")   # 未跟踪也算脏
    mine = commit_file(mind, "diary.md", "本机一笔\n")

    proc = run(mind)
    assert proc.returncode == RC_DIRTY, proc.stdout
    assert "不 pull" in proc.stdout and "也不 push" in proc.stdout
    assert (mind / "from-other.md").exists() is False   # 没在脏工作树上下别人的内容
    assert (mind / "正在写.md").exists()                # 没提交的那份还在原处
    remote = subprocess.run(["git", "ls-remote", str(origin), "refs/heads/main"],
                            capture_output=True, text=True, env=ENV).stdout
    assert mine not in remote                           # 也没推（推必被拒）

    # 只脏、不落后：推得动，且不碰工作树
    calm = tmp_path / "calm"
    subprocess.run(["git", "clone", "-q", str(origin), str(calm)], check=True, env=ENV)
    g(calm, "checkout", "-q", "-B", "main", "origin/main")
    (calm / "在写.md").write_text("桌面在写\n", encoding="utf-8")
    pushed = commit_file(calm, "diary.md", "calm 的一笔\n")
    second = run(calm)
    assert second.returncode == RC_OK, second.stdout + second.stderr
    assert "push ok" in second.stdout
    assert (calm / "在写.md").exists()
    remote = subprocess.run(["git", "ls-remote", str(origin), "refs/heads/main"],
                            capture_output=True, text=True, env=ENV).stdout
    assert pushed in remote
def test_hard_boundaries_refuse(tmp_path, case, monkeypatch):
    monkeypatch.delenv("MIND_ROOT", raising=False)
    if case == "unset":
        proc = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True,
                              encoding="utf-8", env=ENV, timeout=60)
        assert proc.returncode == RC_NOT_BRAIN
        assert "MIND_ROOT" in proc.stdout
        return
    if case == "not-a-repo":
        target = tmp_path / "plain"
        target.mkdir()
        proc = run(target)
        assert proc.returncode == RC_NOT_BRAIN
        assert "不在任何 git 仓" in proc.stdout
        return
    if case == "enclosing-repo":
        # 裁定 72：外层 checkout 的 git 碰不到脑——指到子目录就必须拒
        outer = tmp_path / "outer"
        outer.mkdir()
        g(outer, "init", "-q")
        commit_file(outer, "a.md", "外层\n")
        inner = outer / "not-the-toplevel"
        inner.mkdir()
        proc = run(inner)
        assert proc.returncode == RC_NOT_BRAIN, proc.stdout
        assert "不是自己的仓根" in proc.stdout
        return
    if case == "no-remote":
        lone = tmp_path / "lone"
        lone.mkdir()
        g(lone, "init", "-q", "-b", "main")
        commit_file(lone, "a.md", "只有一份\n")
        proc = run(lone)
        assert proc.returncode == RC_NOT_BRAIN, proc.stdout   # 连 remote 都没有 → 不是脑仓
        return
    branchless = tmp_path / "branchless"
    subprocess.run(["git", "init", "-q", "-b", "main", str(branchless)], check=True, env=ENV)
    (branchless / "origin.git").mkdir()
    commit_file(branchless, "a.md", "本地而已\n")
    proc = run(branchless)
    assert proc.returncode == RC_NO_REMOTE or proc.returncode == RC_NOT_BRAIN, proc.stdout
    assert "upstream" in proc.stdout or "remote" in proc.stdout


# ---- 判据③：失败要出声 = 退出码 + 一行留痕 ----------------------------------


def test_every_run_leaves_a_line_in_the_log(brain, tmp_path):
    mind, _origin = brain
    log = tmp_path / "logs" / "mind-sync.log"
    commit_file(mind, "diary.md", "要推的一笔\n")
    proc = run(mind, "--log", str(log))
    assert proc.returncode == RC_OK, proc.stdout
    body = log.read_text(encoding="utf-8")
    assert "push ok" in body and f"exit={RC_OK}" in body     # 成功留痕
    assert "done: ahead=0 behind=0" in body


def test_a_failed_run_is_loud_in_both_channels(brain, tmp_path):
    mind, origin = brain
    log = tmp_path / "logs" / "mind-sync.log"
    # 制造一次注定失败的远端：把 origin 换成一个不存在的目录后 fetch 会报错
    g(mind, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    proc = run(mind, "--log", str(log))
    assert proc.returncode == 30, proc.stdout
    assert "FAIL" in proc.stdout
    body = log.read_text(encoding="utf-8")
    assert "FAIL" in body and "exit=30" in body              # 计划任务的日志里看得见


def test_dry_run_moves_nothing(brain):
    mind, _origin = brain
    before = head_of(mind)
    commit_file(mind, "diary.md", "没推的\n")
    proc = run(mind, "--dry-run")
    assert proc.returncode == RC_OK, proc.stdout
    assert "ahead=1" in proc.stdout
    assert head_of(mind) != before                           # 本地照旧
    assert g(mind, "rev-list", "--count", "@{u}..HEAD").stdout.strip() == "1"


def test_script_carries_no_credential_and_no_drive_letter():
    """口径：凭据不进有远端仓的历史；本机路径只认环境变量（裁定 48.1/79）。"""
    text = SCRIPT.read_text(encoding="utf-8")
    for needle in ("ghp_", "github_pat_", "password=", "D:\\", "C:\\", "--force"):
        assert needle not in text, f"脚本里出现了 {needle!r}"
    # 正向半边：不交互、不硬来、位置只认环境变量
    assert "GIT_TERMINAL_PROMPT" in text and "--ff-only" in text and "MIND_ROOT" in text
