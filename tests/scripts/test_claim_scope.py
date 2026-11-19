"""`scripts/check_claim_scope.py` 的行为契约（裁定 77）。

全部跑在 `tmp_path` 里的临时登记册 + 临时 git 仓上：**不碰真工作区、不碰真 `Docs/`、
不碰真注册表**，也不 commit（只用 `git add` 造暂存态）。判据是不变量，不是快照。

五条必须盯住的：
  1. 两条在派、族不相交 → 以 A 的前缀暂存 B 的文件 → 非零，且 stderr 出现 B 的 WP 名；
  2. 以 A 的前缀暂存 A 的文件 → 零；
  3. 无 `WP_COMMIT` 且无新鲜消息 + 无硬冲突 → 零（**这条保证门不会常红**）；
  4. 登记册不在场 → 零退出并打印跳过行（独立 clone 的人不是越界者）；
  5. 族写法带 `Code/` 前缀时要剥掉再比 —— Docs 版第一版漏了这步成了假绿，这颗牙专盯它。

外加两条同样要命的：认不出前缀时只在「两条在派线认领同一文件」上判红；
以及这脚本任何路径都不许写工作区、不许动索引。

还有一条是 10-12 21:2x 真撞上的（与 Docs 版同一颗雷）：族列里「**不认领也不改** `X`」提到的那个 X
**不是认领**——整列扫反引号会把三条形互不相干的线判成同占一个独占文件，本仓一笔 Code 提交就是这么
被拦下的。截断只截到否定标记之前，标记**之前**的真认领照常算数（`test_negation_cut_keeps_earlier_claims`）。
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check_claim_scope.py"
HOOK_SRC = REPO_ROOT / "scripts" / "git-hooks" / "commit-msg"

HEADER = (
    "| 刀 / WP | 谁在干 | 在哪 | 认领的文件族 | 状态 | 最后活动 | commit 前缀 |\n"
    "|---|---|---|---|---|---|---|\n"
)


def _row(wp: str, families: str, status: str = "在派") -> str:
    return f"| **{wp}** 测试线 | 某会话 | 某仓 | {families} | **{status}** | 23:5x | `[{wp}]` |\n"


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, env=_child_env()
    )
    assert proc.returncode == 0, f"git {' '.join(args)} failed: {proc.stderr}"
    return proc.stdout


def _child_env(**extra: str) -> dict[str, str]:
    """干净环境：继承 PATH，但拔掉 WP_COMMIT 与 git 自己注入的 GIT_*（钩子里跑测试时会串台）。"""
    env = {k: v for k, v in os.environ.items() if k != "WP_COMMIT" and not k.startswith("GIT_")}
    env.update(extra)
    return env


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "Code"
    repo.mkdir(exist_ok=True)
    _git(repo, "init", "-q")
    for rel, body in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return repo


def _stage(repo: Path, *rels: str) -> None:
    _git(repo, "add", "--", *rels)


def _run(repo: Path, register: Path | None, **env: str) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(SCRIPT), "--repo", str(repo)]
    if register is not None:
        cmd += ["--register", str(register)]
    return subprocess.run(cmd, capture_output=True, text=True, env=_child_env(**env))


def _register(tmp_path: Path, text: str) -> Path:
    reg = tmp_path / "agent-register.md"
    reg.write_text(text, encoding="utf-8")
    return reg


# ---------------------------------------------------------------- 不变量 1 / 5


def test_foreign_family_with_declared_prefix_is_red(tmp_path: Path) -> None:
    """以 A 的前缀签 B 认领的文件 → 红，且说清属于谁（B 的 WP 名要在 stderr 里）。"""
    repo = _repo(tmp_path, {"plobi_cli/uninstall.py": "x\n", "agent/insights.py": "y\n"})
    reg = _register(
        tmp_path,
        HEADER
        + _row("WP-ALPHA", "`Code/plobi_cli/uninstall.py`、`Code/tests/plobi_cli/test_gui.py`")
        + _row("WP-BETA", "`Code/agent/insights.py`"),
    )

    _stage(repo, "agent/insights.py")
    proc = _run(repo, reg, WP_COMMIT="[WP-ALPHA] 顺手改了一下")

    assert proc.returncode != 0, f"越界必须红：{proc.stdout}{proc.stderr}"
    assert "WP-BETA" in proc.stderr, proc.stderr
    assert "agent/insights.py" in proc.stderr, proc.stderr


def test_code_prefix_is_stripped_before_comparing(tmp_path: Path) -> None:
    """防假绿那颗牙：族写法带 `Code/` 前缀，git 给的是仓内相对路径，不剥前缀就永远比不中。"""
    repo = _repo(tmp_path, {"agent/insights.py": "y\n"})
    reg = _register(
        tmp_path,
        HEADER
        + _row("WP-ALPHA", "`Code/agent/plan.py`")
        + _row("WP-BETA", "`Code/agent/insights.py`"),
    )

    _stage(repo, "agent/insights.py")
    proc = _run(repo, reg, WP_COMMIT="WP-ALPHA 别的前缀")

    assert proc.returncode != 0, (
        "带 Code/ 前缀的认领族必须剥掉前缀后仍然判得出越界——没剥就是假绿"
    )
    assert "WP-BETA" in proc.stderr, proc.stderr


def test_own_family_with_own_prefix_is_green(tmp_path: Path) -> None:
    """不变量 2：以 A 的前缀签 A 自己的文件 → 零退出。"""
    repo = _repo(tmp_path, {"scripts/build_repo_map.py": "x\n", "scripts/repo_map.md": "y\n"})
    reg = _register(
        tmp_path,
        HEADER
        + _row("WP-ALPHA", "`Code/scripts/build_repo_map.py`、`Code/scripts/repo_map.md`")
        + _row("WP-BETA", "`Code/agent/insights.py`"),
    )

    _stage(repo, "scripts/build_repo_map.py", "scripts/repo_map.md")
    proc = _run(repo, reg, WP_COMMIT="[WP-ALPHA] 我的落点")

    assert proc.returncode == 0, f"{proc.stdout}{proc.stderr}"
    assert "[WP-ALPHA]" in proc.stdout, proc.stdout


def test_directory_family_and_glob_families_are_covered(tmp_path: Path) -> None:
    """目录族 / `dir/**` 族同样算认领范围（与 Docs 版 _covered 同一口径）。"""
    repo = _repo(tmp_path, {"plugins/plobi-core/channel.py": "x\n",
                           "docs/arch/notes.md": "y\n"})
    reg = _register(
        tmp_path,
        HEADER
        + _row("WP-ALPHA", "`Code/scripts/check_claim_scope.py`")
        + _row("WP-DIR", "`Code/plugins/plobi-core/`")
        + _row("WP-GLOB", "`Code/docs/arch/**`"),
    )

    _stage(repo, "plugins/plobi-core/channel.py")
    dir_case = _run(repo, reg, WP_COMMIT="[WP-ALPHA] 越界")
    assert dir_case.returncode != 0 and "WP-DIR" in dir_case.stderr, dir_case.stderr

    _stage(repo, "docs/arch/notes.md")
    glob_case = _run(repo, reg, WP_COMMIT="[WP-ALPHA] 又越界")
    assert glob_case.returncode != 0 and "WP-GLOB" in glob_case.stderr, glob_case.stderr


# ---------------------------------------------------------------- 不变量 3（防常红）


def test_no_prefix_and_no_hard_conflict_is_green(tmp_path: Path) -> None:
    """无 WP_COMMIT、族不相交 → 必须零退出。这保证门不会常红。"""
    repo = _repo(tmp_path, {"agent/insights.py": "y\n", "scripts/other.py": "z\n"})
    reg = _register(
        tmp_path,
        HEADER
        + _row("WP-ALPHA", "`Code/scripts/other.py`")
        + _row("WP-BETA", "`Code/agent/insights.py`"),
    )

    _stage(repo, "agent/insights.py", "scripts/other.py")
    proc = _run(repo, reg)

    assert proc.returncode == 0, f"判不出归属只给黄灯：{proc.stdout}{proc.stderr}"
    assert "认不出前缀，只判硬冲突" in proc.stdout, proc.stdout


def test_commit_editmsg_alone_is_not_a_source(tmp_path: Path) -> None:
    """`.git/COMMIT_EDITMSG` 不再参与归属判定：哪怕它刚写、写得明明白白，也不许拿来判越界。

    这是「归属只由 commit-msg 注入」的回归牙——谁把那行读回去，这条就红。
    """
    repo = _repo(tmp_path, {"agent/insights.py": "y\n"})
    reg = _register(
        tmp_path,
        HEADER
        + _row("WP-ALPHA", "`Code/scripts/other.py`")
        + _row("WP-BETA", "`Code/agent/insights.py`"),
    )
    (repo / ".git" / "COMMIT_EDITMSG").write_text("[WP-ALPHA] 刚写的新鲜消息\n", encoding="utf-8")

    _stage(repo, "agent/insights.py")
    proc = _run(repo, reg)

    assert proc.returncode == 0, f"消息文件不该被当成本笔前缀：{proc.stdout}{proc.stderr}"
    assert "认不出前缀，只判硬冲突" in proc.stdout, proc.stdout


def _hook_repo(tmp_path: Path) -> Path:
    """临时仓 + 真 check_claim_scope.py + 真登记册 + 真 commit-msg 钩子（不碰真工作区）。"""
    repo = _repo(tmp_path, {"agent/insights.py": "y\n"})
    (repo / "scripts").mkdir(exist_ok=True)
    shutil.copy2(SCRIPT, repo / "scripts" / "check_claim_scope.py")
    reg = repo / "Docs" / "reference" / "agent-register.md"
    reg.parent.mkdir(parents=True, exist_ok=True)
    reg.write_text(
        HEADER
        + _row("WP-ALPHA", "`Code/scripts/other.py`")
        + _row("WP-BETA", "`Code/agent/insights.py`"),
        encoding="utf-8",
    )
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    dest = hooks / "commit-msg"
    shutil.copy2(HOOK_SRC, dest)
    dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    _stage(repo, "agent/insights.py")
    return repo


def _commit(repo: Path, message: str) -> subprocess.CompletedProcess:
    """真提交，只为把钩子链跑起来。身份用 -c 传，绝不写用户全局配置。"""
    return subprocess.run(
        [
            "git",
            "-c", "user.name=claim-scope-test",
            "-c", "user.email=claim-scope-test@example.invalid",
            "-c", "commit.gpgsign=false",
            "commit", "-m", message,
        ],
        cwd=str(repo),
        capture_output=True,
        text=True,
        env=_child_env(),
    )


def test_commit_msg_hook_blocks_a_foreign_prefix(tmp_path: Path) -> None:
    """真钩子链：`git commit -m "[WP-ALPHA] ..."` 签别人的文件 → 提交当场被挡，报错点名 WP-BETA。"""
    repo = _hook_repo(tmp_path)

    proc = _commit(repo, "[WP-ALPHA] 替别人签字")

    assert proc.returncode != 0, f"钩子没拦住越界提交：{proc.stdout}{proc.stderr}"
    assert "WP-BETA" in proc.stderr, proc.stderr


def test_commit_msg_hook_lets_own_prefix_through(tmp_path: Path) -> None:
    """同一套钩子链，换成自己那格的前缀必须放行 —— 否则这扇门就是常红。"""
    repo = _hook_repo(tmp_path)

    proc = _commit(repo, "[WP-BETA] 我自己的文件")

    assert proc.returncode == 0, f"自己签自己的文件被挡了：{proc.stdout}{proc.stderr}"


def test_commit_msg_hook_stays_quiet_without_prefix(tmp_path: Path) -> None:
    """没前缀的提交（治理动作、文档笔误）不许被挡：认不出就退 0，交回 pre-commit 兜底。"""
    repo = _hook_repo(tmp_path)

    proc = _commit(repo, "无前缀的一笔")

    assert proc.returncode == 0, f"无前缀被挡 = 常红：{proc.stdout}{proc.stderr}"


def test_wp_commit_env_var_wins_over_editmsg(tmp_path: Path) -> None:
    """归属判定口径：`WP_COMMIT` 优先于 COMMIT_EDITMSG（两个来源给不同前缀时才验得出优先级）。"""
    repo = _repo(tmp_path, {"agent/insights.py": "y\n"})
    reg = _register(
        tmp_path,
        HEADER
        + _row("WP-ALPHA", "`Code/scripts/other.py`")
        + _row("WP-BETA", "`Code/agent/insights.py`"),
    )
    # 消息文件里是登记册上根本没有的第三条线：谁胜出，输出会直接说出来。
    (repo / ".git" / "COMMIT_EDITMSG").write_text("[WP-GAMMA] 新鲜但第二来源\n", encoding="utf-8")
    _stage(repo, "agent/insights.py")

    # 声明是 BETA 自己签 → 只有 env 压过消息文件才是绿（按消息判会红在 GAMMA 上）。
    own = _run(repo, reg, WP_COMMIT="[WP-BETA] 我自己签")
    assert own.returncode == 0, f"WP_COMMIT 必须优先：{own.stdout}{own.stderr}"

    # 换成 ALPHA 声明就按 ALPHA 判越界，且报错里写的是 ALPHA 而不是 GAMMA。
    over = _run(repo, reg, WP_COMMIT="[WP-ALPHA] 我签别人的文件")
    assert over.returncode != 0, f"{over.stdout}{over.stderr}"
    assert "本笔前缀是 [WP-ALPHA]" in over.stderr, over.stderr
    assert "GAMMA" not in over.stderr, over.stderr


# ---------------------------------------------------------------- 硬冲突档


def test_two_live_lines_claiming_same_file_is_a_hard_conflict(tmp_path: Path) -> None:
    """认不出前缀时唯一能判的红：同一文件被两条在派线同时认领。"""
    repo = _repo(tmp_path, {"plobi_cli/uninstall.py": "x\n"})
    reg = _register(
        tmp_path,
        HEADER
        + _row("WP-ALPHA", "`Code/plobi_cli/uninstall.py`")
        + _row("WP-BETA", "`Code/plobi_cli/uninstall.py`"),
    )

    _stage(repo, "plobi_cli/uninstall.py")
    proc = _run(repo, reg)

    assert proc.returncode != 0, proc.stdout
    assert "两条在派线同时认领" in proc.stderr, proc.stderr
    assert "WP-ALPHA" in proc.stderr and "WP-BETA" in proc.stderr, proc.stderr


def test_negation_clause_is_not_a_claim(tmp_path: Path) -> None:
    """10-12 21:2x 真被拦下的那笔 Code 提交：族列里「不认领也不改 `X`」的 X 不算认领。

    ALPHA 真认领 `Code/bezel/config.yaml`，BETA / GAMMA 只在豁免说明里提到同一个独占文件。
    修之前这三家都被判成占它 → 认不出前缀那一档报「两条在派线同时认领」，一笔本来干净的
    提交被拦住（当事人既不绕 SKIP_ARCH_GATES 也不用 --no-verify，只能来找我翻格）。
    """
    repo = _repo(tmp_path, {"bezel/config.yaml": "x\n"})
    reg = _register(
        tmp_path,
        HEADER
        + _row("WP-ALPHA", "`Code/bezel/config.yaml`")
        + _row("WP-BETA", "`Code/scripts/b_only.py`；**不认领也不改** `Code/bezel/config.yaml`")
        + _row("WP-GAMMA", "`Code/agent/insights.py`；**只读不改** `Code/bezel/config.yaml`"),
    )

    _stage(repo, "bezel/config.yaml")
    proc = _run(repo, reg)

    assert proc.returncode == 0, f"豁免说明里的路径不许算认领：{proc.stdout}{proc.stderr}"
    assert proc.stderr == "", proc.stderr
    assert "在派认领方 3 家" in proc.stdout, proc.stdout


def test_negation_cut_keeps_earlier_claims(tmp_path: Path) -> None:
    """对照组（防修过头）：截断只截到否定标记，标记**之前**的真认领照常算数。"""
    repo = _repo(tmp_path, {"scripts/b_only.py": "x\n"})
    reg = _register(
        tmp_path,
        HEADER
        + _row("WP-ALPHA", "`Code/scripts/a_only.py`")
        + _row("WP-BETA", "`Code/scripts/b_only.py`；**不认领也不改** `Code/scripts/a_only.py`"),
    )

    _stage(repo, "scripts/b_only.py")
    proc = _run(repo, reg, WP_COMMIT="[WP-ALPHA] 越界签 BETA 的族")

    assert proc.returncode != 0, f"{proc.stdout}{proc.stderr}"
    assert "属于在派的 WP-BETA" in proc.stderr, proc.stderr


def test_negation_clause_does_not_unclaim_the_real_owner(tmp_path: Path) -> None:
    """另一个方向的对照组：别人写豁免，不许把真认领方那格也一起清空。"""
    repo = _repo(tmp_path, {"bezel/config.yaml": "x\n"})
    reg = _register(
        tmp_path,
        HEADER
        + _row("WP-ALPHA", "`Code/bezel/config.yaml`")
        + _row("WP-GAMMA", "`Code/agent/insights.py`；**不认领也不改** `Code/bezel/config.yaml`"),
    )

    _stage(repo, "bezel/config.yaml")
    proc = _run(repo, reg, WP_COMMIT="[WP-GAMMA] 嘴上说不改还是签了")

    assert proc.returncode != 0, f"{proc.stdout}{proc.stderr}"
    assert "属于在派的 WP-ALPHA" in proc.stderr, proc.stderr
    assert "本笔前缀是 [WP-GAMMA]" in proc.stderr, proc.stderr


def test_only_live_rows_are_read(tmp_path: Path) -> None:
    """状态口径：待派 / 待签 / 已闭 / 未登记的行不参与归属判定（它们没有"正在碰"的范围）。"""
    repo = _repo(tmp_path, {"agent/usage_pricing.py": "x\n"})
    reg = _register(
        tmp_path,
        HEADER
        + _row("WP-CLOSED", "`Code/agent/usage_pricing.py`", "已闭")
        + _row("WP-PENDING", "`Code/agent/usage_pricing.py`", "待派")
        + _row("WP-UNSIGNED", "`Code/agent/usage_pricing.py`", "待签")
        + _row("WP-ALPHA", "`Code/scripts/other.py`"),
    )

    _stage(repo, "agent/usage_pricing.py")
    proc = _run(repo, reg, WP_COMMIT="[WP-ALPHA] 签一个没人认领的文件")

    assert proc.returncode == 0, f"{proc.stdout}{proc.stderr}"
    assert "在派认领方 1 家" in proc.stdout, proc.stdout


def test_prose_and_docs_families_are_ignored(tmp_path: Path) -> None:
    """族列里混的中文说明与 `Docs/…` 不许被当成 Code 仓的文件族。"""
    repo = _repo(tmp_path, {"README.md": "x\n"})
    reg = _register(
        tmp_path,
        HEADER
        + _row("WP-MESSY", "`Code/pyproject.toml` 的 dev 那一行、`Docs/DECISIONS.md`、（材料已交）")
        + _row("WP-ALPHA", "`Code/scripts/other.py`"),
    )

    _stage(repo, "README.md")
    proc = _run(repo, reg, WP_COMMIT="[WP-ALPHA] 与谁都不相干")

    assert proc.returncode == 0, f"{proc.stdout}{proc.stderr}"


# ---------------------------------------------------------------- 不变量 4（独立 clone）


def test_missing_register_skips_with_a_note(tmp_path: Path) -> None:
    """`--register` 指向不存在的文件 → 零退出 + 打印跳过行。独立 clone 的人不是越界者。"""
    repo = _repo(tmp_path, {"agent/insights.py": "y\n"})
    _stage(repo, "agent/insights.py")
    proc = _run(repo, tmp_path / "nope" / "agent-register.md", WP_COMMIT="[WP-INTRUDER] 越界")

    assert proc.returncode == 0, f"{proc.stdout}{proc.stderr}"
    assert "登记册不在场，认领核对跳过" in proc.stdout, proc.stdout
    assert proc.stderr == "", proc.stderr


def test_register_is_found_upwards_from_the_repo(tmp_path: Path) -> None:
    """不带 `--register` 时要从仓根往上找到 `../Docs/reference/agent-register.md`。"""
    outer = tmp_path / "workspace"
    reg_dir = outer / "Docs" / "reference"
    reg_dir.mkdir(parents=True)
    (reg_dir / "agent-register.md").write_text(
        HEADER
        + _row("WP-ALPHA", "`Code/scripts/other.py`")
        + _row("WP-BETA", "`Code/agent/insights.py`"),
        encoding="utf-8",
    )
    repo = _repo(outer, {"agent/insights.py": "y\n"})
    _stage(repo, "agent/insights.py")

    proc = _run(repo, None, WP_COMMIT="[WP-ALPHA] 往上找到的登记册")

    assert proc.returncode != 0, proc.stdout
    assert "WP-BETA" in proc.stderr, proc.stderr


def test_docs_dir_present_but_page_gone_is_a_skip(tmp_path: Path) -> None:
    """`Docs/reference/` 在但那一页没了 → 仍算登记册不在场，跳过而不是判红。"""
    outer = tmp_path / "workspace"
    (outer / "Docs" / "reference").mkdir(parents=True)
    repo = _repo(outer, {"agent/insights.py": "y\n"})
    _stage(repo, "agent/insights.py")

    proc = _run(repo, None, WP_COMMIT="[WP-ALPHA] 没有登记册可用")

    assert proc.returncode == 0, f"{proc.stdout}{proc.stderr}"
    assert "登记册不在场" in proc.stdout, proc.stdout


# ---------------------------------------------------------------- 只读契约


def test_script_never_touches_worktree_or_index(tmp_path: Path) -> None:
    """任何路径（红/绿都算）都不许写工作区、不许动索引。"""
    repo = _repo(tmp_path, {"agent/insights.py": "y\n", "scripts/other.py": "z\n",
                           "untracked.py": "u\n"})
    reg = _register(
        tmp_path,
        HEADER
        + _row("WP-ALPHA", "`Code/scripts/other.py`")
        + _row("WP-BETA", "`Code/agent/insights.py`"),
    )
    _stage(repo, "scripts/other.py")

    before_staged = _git(repo, "diff", "--cached", "--name-only")
    before_status = _git(repo, "status", "--porcelain=v1")
    before_blob = _git(repo, "rev-parse", ":scripts/other.py")

    green_case = _run(repo, reg, WP_COMMIT="[WP-ALPHA] 签的是自己认领的族")
    red_case = _run(repo, reg, WP_COMMIT="[WP-BETA] 别人的族")

    assert red_case.returncode != 0   # 确保这条真的跑过「红」那一路
    assert green_case.returncode == 0
    assert _git(repo, "diff", "--cached", "--name-only") == before_staged
    assert _git(repo, "status", "--porcelain=v1") == before_status
    assert _git(repo, "rev-parse", ":scripts/other.py") == before_blob
