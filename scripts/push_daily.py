#!/usr/bin/env python3
"""Daily push of the working repos to their remotes — 裁定 127 (用户 10-11 原话).

    「Plobi 和 Plobi-Harmony-App 每天开发完就推送一次就行了。」

The one thing this must never do is *decide a divergence*.  A diverged branch needs
a human (or the owning line) to reconcile it: force-push and rebase are hard-banned
in this workspace, and an unattended `git merge` would silently invent history on
somebody's main branch.  So: fetch, and push **only when it is a fast-forward**;
otherwise log a red line with the counts and the exact command to run by hand.

Docs is deliberately absent — 裁定 127 keeps it local-only.  The Mind repo has its
own line and its own push/pull arrangement; we report its state and never touch it.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
import sys
from pathlib import Path

WORKSPACE = Path(r"D:\Projects\Plobi")
REPOS = {
    "Code": WORKSPACE / "Plobi-Desktop" / "Code",
    "Harmony": WORKSPACE / "Plobi-Harmony-App",
}
# Reported, never acted on: another line owns these.
READ_ONLY = {
    "Docs": WORKSPACE / "Plobi-Desktop" / "Docs",
    "Mind": Path(os.environ.get("MIND_ROOT", r"D:\Data\AppData\plobi\mind")),
}
LOG = WORKSPACE / "bundles" / "keep" / "push-run.log"


def git(repo: Path, *args: str, timeout: int = 120) -> tuple[int, str]:
    try:
        r = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            timeout=timeout,
            env=_git_env(),
        )
    except FileNotFoundError:
        return 127, "git not found"
    except subprocess.TimeoutExpired:
        return 124, f"git {' '.join(args)} timed out after {timeout}s"
    out = (r.stdout + r.stderr).decode("utf-8", "replace").strip()
    return r.returncode, out


_PUSH_ENV: dict[str, str] | None = None


def _git_env() -> dict[str, str]:
    """git's own environment, routed through Plobi's declared exit when there is one.

    Measured on this box 2026-10-14: with iKuuu's service stopped, `git fetch` to
    github.com fails with "Recv failure: Connection was reset" — the campus link does
    not carry github directly — while the same URL through our engine returns 200.
    So the daily push must use the exit the table declares, or it silently depends on
    somebody else's VPN.  One reading, same parser as everything else (裁定 94).
    """
    global _PUSH_ENV
    if _PUSH_ENV is None:
        env = dict(os.environ)
        try:
            sys.path.insert(0, str(WORKSPACE / "Plobi-Desktop" / "Code"))
            from agent.egress.env_projection import apply_egress_env
            from agent.egress.rules import load_table

            apply_egress_env(env, load_table())
        except Exception as exc:  # no table / broken config: push the plain way, and say so
            env["_PLOBI_PUSH_NO_EGRESS"] = f"{type(exc).__name__}"
        _PUSH_ENV = env
    return _PUSH_ENV


def branch(repo: Path) -> str:
    return git(repo, "rev-parse", "--abbrev-ref", "HEAD")[1].strip() or "HEAD"


def remote_for(repo: Path, name: str) -> str:
    return git(repo, "remote", "get-url", name)[1].strip()


def sync(repo: Path, label: str, lines: list[str]) -> bool:
    """Push one repo if and only if the push is a fast-forward. Returns ok."""
    if not (repo / ".git").is_dir():
        lines.append(f"  {label:8s} SKIP  不是 git 仓（{repo}）")
        return True

    name = branch(repo)
    remote = "origin" if (repo / ".git").is_dir() and git(repo, "remote")[1] else ""
    if not remote:
        lines.append(f"  {label:8s} SKIP  没有远端")
        return True

    rc, out = git(repo, "fetch", remote, name)
    if rc != 0:
        lines.append(f"  {label:8s} RED   fetch 失败（多半是非交互拿不到凭证）：{out.splitlines()[-1] if out else rc}")
        return False

    tracking = f"{remote}/{name}"
    ahead = git(repo, "rev-list", "--count", f"{tracking}..{name}")[1].strip()
    behind = git(repo, "rev-list", "--count", f"{name}..{tracking}")[1].strip()

    if not behind.isdigit() or not ahead.isdigit():
        lines.append(f"  {label:8s} RED   数不出 ahead/behind：ahead={ahead} behind={behind}")
        return False

    if int(behind) > 0:
        # The whole point: never reconcile a divergence unattended.
        lines.append(
            f"  {label:8s} RED   分叉：本地多 {ahead} 笔、远端多 {behind} 笔 ⇒ 不推。"
            f"要人工合并：git -C {repo} merge {tracking}（force 与 rebase 是本仓硬禁）"
        )
        return False

    if int(ahead) == 0:
        lines.append(f"  {label:8s} ok    已平（{name}，无待推笔）")
        return True

    rc, out = git(repo, "push", remote, name, timeout=300)
    if rc != 0:
        lines.append(f"  {label:8s} RED   push 失败：{out.splitlines()[-1] if out else rc}")
        return False
    lines.append(f"  {label:8s} ok    推上 {ahead} 笔 → {remote_for(repo, remote).split('@')[-1]}")
    return True


def report_only(repo: Path, label: str, lines: list[str]) -> None:
    if not (repo / ".git").is_dir():
        lines.append(f"  {label:8s} —     不在盘上（{repo}）")
        return
    name = branch(repo)
    if not git(repo, "remote")[1].strip():
        lines.append(f"  {label:8s} —     无远端（按裁定 127，Docs 只留本地）")
        return
    git(repo, "fetch", "origin", name)
    ahead = git(repo, "rev-list", "--count", f"origin/{name}..{name}")[1].strip()
    behind = git(repo, "rev-list", "--count", f"{name}..origin/{name}")[1].strip()
    lines.append(f"  {label:8s} 只读  本地多 {ahead} 笔、远端多 {behind} 笔（归它自己的线，本任务不动）")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="fetch and report, push nothing")
    args = parser.parse_args()

    stamp = dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S%z")
    lines = [f"{stamp}  push_daily {'(dry-run)' if args.dry_run else ''}"]
    ok = True
    for label, repo in REPOS.items():
        if args.dry_run:
            report_only(repo, label, lines)
            continue
        ok = sync(repo, label, lines) and ok
    for label, repo in READ_ONLY.items():
        report_only(repo, label, lines)

    lines.append(f"  结论：{'GREEN' if ok else 'RED'}")
    text = "\n".join(lines)
    print(text)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(text + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
