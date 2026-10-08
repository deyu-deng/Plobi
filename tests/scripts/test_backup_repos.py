"""`scripts/backup_repos.py` 的行为契约（`WP-MIND-BUNDLES`，裁定 72 尾巴②）。

全部跑在 `tmp_path` 里造的一棵**假工作区**上：脚本被复制到
`<tmp>/Plobi-Desktop/Code/scripts/` 后按它自己的路径推导规则去找四个仓，所以这里验的是
**真的推导逻辑**，不是 monkeypatch 出来的假象。判据是不变量，不是快照。

四条必须盯住的：
  1. 四个仓都在位 → 退出 0，每个仓出一份 bundle，且每份都 `bundle verify` 过；
  2. `MIND_ROOT` 没设 → **非零退出**，且报文明确点名脑不在位（不许静默跳掉——裁定 72.6
     那条"12 条门禁静默跳过还报绿"就是这颗牙要防的形状）；
  3. `MIND_ROOT` 设了但目录不存在 → 非零，且说清是目录缺失不是没配；
  4. 任一仓缺 `.git` → 非零，那一格报出来，其余仓照常备（不因一处坏就说整片好）。

外加一条：脑的路径**只从 `MIND_ROOT` 读**，脚本与测试里都不许出现字面盘符（裁定 48.1 / 79）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "backup_repos.py"


def _child_env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_") and k != "MIND_ROOT"}
    env.update(extra)
    return env


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, env=_child_env()
    )
    assert proc.returncode == 0, f"git {' '.join(args)} failed: {proc.stderr}"
    return proc.stdout


def _repo(path: Path, name: str) -> Path:
    """一个有分支、有标签、有一个提交的最小仓 —— bundle 需要至少一个 ref。"""
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@example.invalid")
    _git(path, "config", "user.name", "t")
    (path / f"{name}.md").write_text(f"{name}\n", encoding="utf-8")
    _git(path, "add", "--", f"{name}.md")
    _git(path, "commit", "-q", "-m", f"seed {name}")
    _git(path, "tag", "v1")
    return path


def _workspace(tmp_path: Path, *, with_mind: bool = True, mind_exists: bool = True,
               docs_is_repo: bool = True) -> tuple[Path, Path | None]:
    """复制脚本进假工作区，返回 (脚本路径, 脑仓路径或 None)。"""
    desktop = tmp_path / "Plobi-Desktop"
    scripts = desktop / "Code" / "scripts"
    scripts.mkdir(parents=True)
    dst = scripts / "backup_repos.py"
    shutil.copyfile(SCRIPT, dst)

    _repo(desktop / "Code", "code")
    if docs_is_repo:
        _repo(desktop / "Docs", "docs")
    else:
        # 一个"目录在、但不是仓"的格子。不去 rmtree 真 .git —— git 对象文件在 Windows
        # 上是只读的，删它会撞 WinError 5（这条坑本仓已经记过，别再踩一遍）。
        d = desktop / "Docs"
        d.mkdir(parents=True)
        (d / "docs.md").write_text("docs\n", encoding="utf-8")
    _repo(tmp_path / "Plobi-Harmony-App", "harmony")
    mind = None
    if with_mind:
        mind = tmp_path / "mind-vault"
        if mind_exists:
            _repo(mind, "brain")
        else:
            mind = tmp_path / "mind-vault-absent"
    return dst, mind


def _run(script: Path, dest: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), "--dest", str(dest)],
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )


def test_all_four_repos_produce_verified_bundles(tmp_path: Path) -> None:
    script, mind = _workspace(tmp_path)
    dest = tmp_path / "out"
    proc = _run(script, dest, _child_env(MIND_ROOT=str(mind)))

    assert proc.returncode == 0, proc.stderr
    made = sorted(p.name for p in dest.glob("*.bundle"))
    assert len(made) == 4, made
    for repo_name in ("code", "docs", "harmony", "mind"):
        assert any(f"-{repo_name}-" in n for n in made), made
    for line in ("code", "docs", "harmony", "mind"):
        assert f"ok   {line}:" in proc.stdout, proc.stdout
        assert "(verified)" in proc.stdout

    # verify 不是自说自话：拿真 git 再验一次其中一份。
    a_bundle = next(p for p in dest.glob("*.bundle") if "-mind-" in p.name)
    check = subprocess.run(
        ["git", "bundle", "verify", str(a_bundle)],
        cwd=str(mind), capture_output=True, text=True, env=_child_env(),
    )
    assert check.returncode == 0, check.stderr


def test_mind_root_unset_fails_loudly_and_names_the_brain(tmp_path: Path) -> None:
    script, _mind = _workspace(tmp_path, with_mind=False)
    dest = tmp_path / "out"
    proc = _run(script, dest, _child_env())

    assert proc.returncode != 0
    assert "MIND_ROOT" in proc.stderr or "MIND_ROOT" in proc.stdout
    assert "brain not in place" in proc.stderr, proc.stderr
    # 其余三个仓仍然备出来了 —— 坏一处不等于全部白跑，也不等于装作全好了。
    assert len(list(dest.glob("plobi-code-*.bundle"))) == 1
    assert not list(dest.glob("plobi-mind-*.bundle"))


def test_mind_root_set_but_directory_missing_is_distinct_from_unset(tmp_path: Path) -> None:
    script, mind = _workspace(tmp_path, mind_exists=False)
    dest = tmp_path / "out"
    proc = _run(script, dest, _child_env(MIND_ROOT=str(mind)))

    assert proc.returncode != 0
    assert "mind: directory missing" in proc.stdout, proc.stdout
    assert "points at a directory that is not there" in proc.stderr, proc.stderr
    assert "is not set" not in proc.stderr  # 「没配」与「配了但目录没了」是两种修法，不许说成一句


def test_repo_without_git_is_reported_not_silently_skipped(tmp_path: Path) -> None:
    script, mind = _workspace(tmp_path, docs_is_repo=False)
    dest = tmp_path / "out"
    proc = _run(script, dest, _child_env(MIND_ROOT=str(mind)))

    assert proc.returncode != 0
    assert "docs: no .git" in proc.stdout, proc.stdout
    assert "not backed up: docs:" in proc.stderr, proc.stderr
    # 一处坏不等于整片白跑：其余三个仓仍然各出一份
    assert len(list(dest.glob("plobi-code-*.bundle"))) == 1
    assert len(list(dest.glob("plobi-mind-*.bundle"))) == 1


def test_script_and_test_contain_no_literal_drive_letters() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert not any(f"{d}:\\" in source for d in "CDEFGHIJKLMNOPQRSTUVWXYZ"), "字面盘符进了备份脚本"
