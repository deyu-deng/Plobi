#!/usr/bin/env python3
"""发版前核：workflow 里引用的仓内脚本，必须**在要发的那个提交里**存在。

背景（2026-11-07 现场）：`WP-BACKEND-IN-BUNDLE` 那一笔把在途未提交的 chatlog 作业
带进了仓，而那个作业引用的 ``apps/desktop/scripts/build-chatlog.mjs`` 只在盘上、不在 git 里
⇒ 三个平台的构建全红（``Cannot find module …/build-chatlog.mjs``），**任何发版都会被拦**。
手上有两把尺能看见这类事故：门看**提交范围**，但门看不见「workflow 引用了一个不在仓里的文件」。

这个检查把那类事故变成机器能判的一条：读 ``.github/workflows/*.yml`` 里 ``run:`` 步骤
引用到的仓内脚本路径，逐个问 git「**这个提交**里有它吗」。缺一个就红，并说清缺哪个。

用法：
    python scripts/plobi/check_workflow_scripts.py            # 核 HEAD
    python scripts/plobi/check_workflow_scripts.py <commit-ish> # 核要发的那个提交

退出码：0＝绿；1＝红（有 workflow 引用了那个提交里不存在的脚本）。
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DIR = ".github/workflows"

# 仓内脚本路径：**锚定在仓的顶层目录上**取（`apps/`、`scripts/`…），
# 再按路径段逐级试后缀（`${{ github.workspace }}/apps/x.mjs` 的候选带前缀垃圾，
# 后缀法比一条正则准）。不锚定就会把 `Node.js`、`install.ps1` 这类裸文件名当路径。
_TOP_DIRS = (
    "apps/", "scripts/", "tools/", "tests/", "plobi/", "agent/", "cron/", "gateway/",
    "plugins/", "packages/", "web/", "website/", "skills/", "optional-mcps/",
    "optional-skills/", "locales/", "packaging/", "ci/", "nix/", "docker/", "ui-twig/",
)
_TOKEN = re.compile(
    r"(?P<p>" + "|".join(re.escape(d) for d in _TOP_DIRS) + r")"
    r"[\w.\-/]*?\.(?:mjs|cjs|js|ts|tsx|py|ps1|sh|cmd|bat)\b"
)
# 明显不是仓内路径的（依赖树、构建产物、vendor 里的第三方）
_SKIP_PARTS = ("node_modules", "dist", "build", "vendor", "site-packages")


def _git(*args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True
    )
    return proc.stdout


def _exists_in(commit: str, path: str) -> bool:
    return subprocess.run(
        ["git", "-C", str(ROOT), "cat-file", "-e", f"{commit}:{path}"],
        capture_output=True,
    ).returncode == 0


def _candidate_suffixes(token: str) -> list[str]:
    """一个候选 token 的所有「从路径段边界起」的后缀（长的在前）。"""
    parts = [p for p in token.split("/") if p and not p.startswith(".") and "}" not in p]
    out = []
    for i in range(len(parts)):
        candidate = "/".join(parts[i:])
        if candidate and "." in candidate:
            out.append(candidate)
    return out


def _missing_in_workflow(text: str, commit: str) -> list[str]:
    """这个 workflow 引用了哪些**在那个提交里不存在**的脚本。"""
    missing: list[str] = []
    for match in _TOKEN.finditer(text):
        token = match.group(0).strip("/")
        if not token or any(part in token.split("/") for part in _SKIP_PARTS):
            continue
        suffixes = _candidate_suffixes(token)
        if not suffixes:
            continue
        if any(_exists_in(commit, suffix) for suffix in suffixes):
            continue
        missing.append(token)
    return sorted(set(missing))


def missing_scripts(commit: str) -> list[tuple[str, str]]:
    """(workflow 文件, 它引用但那个提交里没有的脚本) —— 一条一条。"""
    listing = _git("ls-tree", "-r", "--name-only", commit, WORKFLOW_DIR)
    out: list[tuple[str, str]] = []
    for wf in [ln.strip() for ln in listing.splitlines() if ln.strip().endswith((".yml", ".yaml"))]:
        try:
            text = _git("show", f"{commit}:{wf}")
        except subprocess.CalledProcessError:
            continue
        for path in _missing_in_workflow(text, commit):
            out.append((wf, path))
    return out


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    commit = argv[0] if argv else "HEAD"

    missing = missing_scripts(commit)
    if not missing:
        print(f"workflow 脚本自检：{commit} 里每个被引用的仓内脚本都在 ✔")
        return 0

    print(f"workflow 脚本自检红：{commit} 里这些被引用的脚本**不在这个提交里** ——")
    for wf, path in missing:
        print(f"  · {wf} → {path}")
    print(
        "\n  这就是 11-07 那次三平台全红的形状：作业在仓里、脚本只在盘上。"
        "\n  要么把脚本连着作业一起落（**同一条提交**，缺一个就红），要么把作业撤回去。"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
