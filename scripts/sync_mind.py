#!/usr/bin/env python3
"""Sync the brain repo with its own remote: pull only if it fast-forwards, then push.

Why this exists: the brain is the one store that cannot be regenerated from code
(ruling 72), and on this machine it is pushed by hand or not at all — so a lost
disk loses a person's diary, profile and notes. Ruling 127 put 定时推拉 on the
plan; this is the half that was never built.

Hard boundaries this script will not cross, and each one is a refusal with a
reason on stdout (criterion ③ is "failures must be audible", not "failures must
be retried quietly"):

- it runs against ``MIND_ROOT`` only, and refuses unless that directory is its
  own git top level (ruling 72: the code repo's git and gates must not reach it)
- it never rebases, never merges a commit, never force-pushes, never touches the
  worktree's content. ``pull`` is ``--ff-only``, full stop: if the two sides have
  diverged, a human decides, because silently merging the brain is irreversible
- if the worktree is dirty it skips the pull (the desktop/agent may be mid-write)
  and still pushes what is already committed
- credentials stay out of the repo: nothing here reads, writes or embeds a token.

Exit codes are stable so a scheduled task's LastTaskResult means something:
0 synced (or nothing to do) · 20 refused-diverged · 21 refused-not-the-brain-repo
· 22 refused-no-remote · 23 skipped-pull-dirty-but-pushed · 30 network/git failure.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

MIND_ENV_VAR = "MIND_ROOT"
UPSTREAM = "@{u}"

RC_OK = 0
RC_DIVERGED = 20
RC_NOT_BRAIN = 21
RC_NO_REMOTE = 22
RC_DIRTY_SKIPPED = 23
RC_GIT = 30


def git(root: Path, *args: str, timeout: int = 180) -> subprocess.CompletedProcess:
    """One git call, always non-interactive, always bounded by a timeout."""
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(["git", *args], 1, "", f"{type(exc).__name__}: {exc}")


def resolve_brain_root(explicit: str | None) -> Path | None:
    raw = (explicit or os.environ.get(MIND_ENV_VAR, "")).strip()
    return Path(raw) if raw else None


def assert_brain_repo(root: Path) -> str | None:
    """Refuse anything that is not the brain's own checkout."""
    if not root.is_dir():
        return f"{root} 不存在：{MIND_ENV_VAR} 指的目录不在盘上"
    top = git(root, "rev-parse", "--show-toplevel")
    if top.returncode:
        return f"{root} 不在任何 git 仓里（{MIND_ENV_VAR} 指错了？）"
    try:
        if Path(top.stdout.strip()).resolve() != root.resolve():
            return (f"{root} 不是自己的仓根（真正的外层仓是 {top.stdout.strip()}）——"
                    "裁定 72：不许用外层 checkout 的 git 动脑")
    except OSError as exc:
        return f"路径解析失败：{exc}"
    remotes = git(root, "remote")
    if remotes.returncode or not remotes.stdout.strip():
        return f"{root} 没有任何 remote：推拉都无处可去，先拍远端归谁（这不属于本脚本）"
    return None


def counts(root: Path) -> tuple[int, int]:
    """(ahead, behind) against the upstream of the current branch."""
    one = git(root, "rev-list", "--count", f"{UPSTREAM}..HEAD")
    two = git(root, "rev-list", "--count", f"HEAD..{UPSTREAM}")
    if one.returncode or two.returncode:
        return -1, -1
    return int(one.stdout.strip()), int(two.stdout.strip())


def branch(root: Path) -> str:
    return git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or "HEAD"


def sync(root: Path, *, do_fetch: bool = True, quiet: bool = False, sink=None) -> int:
    def say(msg: str) -> None:
        if not quiet:
            print(msg)
        if sink is not None:
            sink(msg)

    reason = assert_brain_repo(root)
    if reason:
        say(f"REFUSE {RC_NOT_BRAIN}: {reason}")
        return RC_NOT_BRAIN

    name = branch(root)
    upstream = git(root, "rev-parse", "--abbrev-ref", f"{name}@{{upstream}}").stdout.strip()
    if "@" in upstream or not upstream:
        say(f"REFUSE {RC_NO_REMOTE}: 分支 {name} 没有 upstream——先设一次 "
            f"`git -C <brain> branch --set-upstream-to={upstream or 'origin/' + name}`，"
            "本脚本不替人决定该跟哪条分支")
        return RC_NO_REMOTE

    dirty = git(root, "status", "--porcelain").stdout.strip()
    ahead = behind = -1

    if do_fetch:
        fetch = git(root, "fetch", "--prune", "origin")
        if fetch.returncode:
            say(f"FAIL {RC_GIT}: fetch 没成（{name}）：{fetch.stderr.strip().splitlines()[-1] if fetch.stderr.strip() else '无输出'}")
            return RC_GIT
    ahead, behind = counts(root)
    if ahead < 0:
        say(f"FAIL {RC_GIT}: 拿不到 ahead/behind（upstream {upstream} 不存在？先 fetch）")
        return RC_GIT

    code = RC_OK
    if behind:
        if dirty:
            if ahead:
                say(f"SKIP {RC_DIRTY_SKIPPED}: 落后 {behind} 笔且工作树有未提交改动 —— 不 pull"
                    "（脑的写入方可能正在写），也不 push（落后时推上去必被拒）："
                    "这一趟两件事都不做，等人")
                return RC_DIRTY_SKIPPED
            say(f"SKIP pull {RC_DIRTY_SKIPPED}: 工作树有未提交改动，且落后 {behind} 笔 —— "
                "不在这个状态上 pull（脑的写入方可能正在写）；本机无待推提交")
            code = RC_DIRTY_SKIPPED
        else:
            pull = git(root, "-c", "advice.detachedHead=false", "merge", "--ff-only",
                       f"refs/remotes/{upstream}")
            if pull.returncode:
                say(f"REFUSE {RC_DIVERGED}: 落后 {behind} 笔但 --ff-only 合不上 —— "
                    "这是真分叉，人来判；本脚本不 rebase、不 merge、不动盘")
                return RC_DIVERGED
            say(f"pull ok：快进 {behind} 笔（{upstream} → {name}）")

    if ahead:
        push = git(root, "push", "origin", f"HEAD:refs/heads/{name}")
        if push.returncode:
            say(f"FAIL {RC_GIT}: push 没成（ahead={ahead}）："
                f"{push.stderr.strip().splitlines()[-1] if push.stderr.strip() else '无输出'}")
            return RC_GIT
        say(f"push ok：{ahead} 笔上云（origin/{name}）")

    ahead, behind = counts(root)
    say(f"done: ahead={ahead} behind={behind} branch={name} dirty={'yes' if dirty else 'no'}")
    if not ahead and not behind:
        return RC_OK
    return code


def resolve_log(explicit: str | None) -> Path | None:
    """Where the audible trail goes. Derived, never a literal drive letter:
    ``--log`` → ``PLOBI_HOME/logs/mind-sync.log`` → no file (stdout only)."""
    raw = (explicit or "").strip()
    if not raw:
        home = os.environ.get("PLOBI_HOME", "").strip()
        raw = str(Path(home) / "logs" / "mind-sync.log") if home else ""
    return Path(raw) if raw else None


def append_log(path: Path, lines: list[str]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(path, "a", encoding="utf-8") as fh:
            for line in lines:
                fh.write(f"[{stamp}] {line}\n")
    except OSError as exc:
        # A log that cannot be written must not turn a real sync into a silent one.
        print(f"WARN: 写不进日志 {path}（{type(exc).__name__}: {exc}）—— 以上输出就是唯一留痕")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="pull (--ff-only) then push the brain repo")
    ap.add_argument("--mind-root", default=None, help=f"override {MIND_ENV_VAR} (tests)")
    ap.add_argument("--dry-run", action="store_true", help="只报状态，不动 remote")
    ap.add_argument("--no-fetch", action="store_true", help="跳过 fetch，用现有 remote-tracking 判断")
    ap.add_argument("--log", default=None, help="留痕文件（默认 PLOBI_HOME/logs/mind-sync.log）")
    args = ap.parse_args(argv)

    root = resolve_brain_root(args.mind_root)
    if root is None:
        print(f"REFUSE {RC_NOT_BRAIN}: {MIND_ENV_VAR} 没设 —— 脑的位置只认环境变量（裁定 48.1/79）")
        return RC_NOT_BRAIN
    if args.dry_run:
        reason = assert_brain_repo(root)
        if reason:
            print(f"REFUSE {RC_NOT_BRAIN}: {reason}")
            return RC_NOT_BRAIN
        ahead, behind = counts(root)
        print(f"dry-run: branch={branch(root)} ahead={ahead} behind={behind} "
              f"dirty={'yes' if git(root, 'status', '--porcelain').stdout.strip() else 'no'}")
        return RC_OK if ahead >= 0 else RC_GIT
    started = time.time()
    lines: list[str] = []
    # stdout 只有一个出口：sync() 里的 say() 不再自己印（quiet=True），
    # 否则同一行会在终端出现两遍——日志没被污染，但读起来像双写。
    rc = sync(root, do_fetch=not args.no_fetch, quiet=True, sink=lines.append)
    lines.append(f"exit={rc} elapsed={time.time() - started:.1f}s")
    for line in lines:
        print(line)
    log = resolve_log(args.log)
    if log is not None:
        append_log(log, lines)
    return rc


if __name__ == "__main__":
    sys.exit(main())
