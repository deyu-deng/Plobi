"""脑仓门禁不许把**外层代码仓**当成本仓审（裁定 72 尾巴① / REQUIREMENTS R-013 边角②）。

`plobi/mind/project_declarations.py` 会拿 `MIND_ROOT` 当 cwd 跑那个库自己的 `verifier.py`，
还要往 `AGENTS.md` 与 `Vault/projects/INDEX.md` 里写。`resolve_root()` 只保证"这个目录存在"，
不保证"这个目录就是它自己那个仓的顶层"。于是有一个真实形状能咬人：`MIND_ROOT` 指到一块
**自己不是 git 仓、又嵌在别的仓里**的目录时，`git rev-parse --show-toplevel` 给出的是那个外层仓
——门禁审错了对象，声明也写进了别人的仓。

三条钉的是这件事：
  1. 嵌在别的仓里 → 拒绝，且报文点名外层仓是谁（不许静默继续，也不许只说"失败了"）；
  2. 拒绝发生在**任何写动作之前**：外层仓的 `AGENTS.md` 一字节没动；
  3. 两种正当形态不误伤：脑仓自己带 `.git`（toplevel == 自己）、以及哪都不在仓里的裸目录
     （没有外层仓可误认，属假库/新用户库的正常形状）。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from plobi.mind.project_declarations import DeclarationSyncError, sync_project_declarations

NESTED_MESSAGE_MARKER = "嵌在"


def _git(repo: Path, *args: str) -> None:
    proc = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True)
    assert proc.returncode == 0, f"git {' '.join(args)}: {proc.stderr}"


def _make_vault(root: Path, *, with_gate: bool = False) -> Path:
    """一块看起来像脑仓的目录：Vault/projects/<p> + AGENTS.md。（verifier 不在场时用 with_gate=False）"""
    vault = root / "mind"
    (vault / "Vault" / "projects" / "Plobi").mkdir(parents=True)
    (vault / "AGENTS.md").write_text(
        "# Mind\n\n## 1. 项目（1）\n\n- Plobi — 本项目\n", encoding="utf-8"
    )
    if with_gate:
        script = vault / "Loom" / "scripts" / "verifier.py"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    return vault


def test_nested_non_repo_vault_is_refused_and_names_the_outer_repo(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    vault = _make_vault(tmp_path)
    outer_agents = tmp_path / "AGENTS.md"
    outer_agents.write_text("outer repo doc\n", encoding="utf-8")

    with pytest.raises(DeclarationSyncError) as caught:
        sync_project_declarations(dir_name="Plobi", mind_root=vault)

    text = str(caught.value)
    assert NESTED_MESSAGE_MARKER in text, text
    assert "AGENTS.md" in text or "INDEX" in text, f"报文没说清会改到谁：{text}"
    # 拒绝必须发生在写之前：外层仓那份文件一个字节没动
    assert outer_agents.read_text(encoding="utf-8") == "outer repo doc\n"


def test_real_vault_that_is_its_own_repo_is_not_refused(tmp_path: Path) -> None:
    vault = _make_vault(tmp_path, with_gate=True)
    _git(vault, "init", "-q")
    _git(vault, "config", "user.email", "t@example.invalid")
    _git(vault, "config", "user.name", "t")

    try:
        sync_project_declarations(dir_name="Plobi", mind_root=vault)
    except DeclarationSyncError as exc:
        assert NESTED_MESSAGE_MARKER not in str(exc), f"自己的仓被当成了嵌套：{exc}"


def test_directory_in_no_repo_at_all_is_not_refused_by_this_guard(tmp_path: Path) -> None:
    """裸目录没有外层仓可误认 —— 这条不许被守卫拦，否则假库与测试根全部陪葬。"""
    vault = _make_vault(tmp_path / "nowhere", with_gate=True)
    assert subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=str(vault),
                          capture_output=True, text=True).returncode != 0

    try:
        sync_project_declarations(dir_name="Plobi", mind_root=vault)
    except DeclarationSyncError as exc:
        assert NESTED_MESSAGE_MARKER not in str(exc), f"没有嵌套却被按嵌套拒绝：{exc}"
