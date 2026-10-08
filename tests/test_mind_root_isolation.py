"""`tests/conftest.py` 的 `_hermetic_environment` 必须把脑也隔离掉（待决 #27，`WP-TEST-HERMETIC-MIND`）。

钉的是**可达性**，不是某个具体字符串：一个没自己设过 `MIND_ROOT` 的用例，解析到的脑**必须是
它自己 `tmp_path` 下那个假库**，而不是这台机器登记的那个真脑。理由在裁定 72——脑是唯一不能从代码
重新生成的那一份数据，测试往里写一个目录就是拿用户的数据做实验（同 `PLOBI_HOME` 那一族的形状，
见裁定 75 / `WP-UNINSTALL-USERENV`）。

三条：
  1. 默认（不设）→ 解析到 tmp 下的假脑，且**不等于**机器上那个真脑路径；
  2. 用例显式设了自己的脑 → 它说了算（隔离不是抢方向盘，autouse 先跑、用例后设）；
  3. 假脑**故意不是 git 仓** → `MindWriter._git_toplevel` 守卫据此拒绝提交，测试不会往任何人
     的仓里落提交（这条是"隔离"之外多要的一层保险，别把它改成 git 仓）。

模块级那份 `_MACHINE_MIND` 是在 conftest 的 fixture 生效**之前**、也就是 collection 阶段读的——
这正是"脑路径靠继承而不是靠登记"会在 collection 期漏进来的那道口子。把它显式记在这里，
是给下一位改这条的人看的：collection 期的读取不归本 fixture 管，要治得去治那个读取点。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from plobi.mind.paths import resolve_root

_MACHINE_MIND = os.environ.get("MIND_ROOT", "").strip()


def test_default_brain_is_the_per_test_vault(tmp_path: Path) -> None:
    root = resolve_root()

    assert root is not None, "脑没配置 ⇒ 这条已经不再是隔离问题，而是 fixture 没跑起来"
    assert Path(str(root)).is_relative_to(tmp_path), f"解析到了 tmp 之外：{root}"
    if _MACHINE_MIND:
        assert str(root) != _MACHINE_MIND, "测试直连盘上那个真脑仓"


def test_explicit_per_test_brain_overrides_the_default(tmp_path: Path, monkeypatch) -> None:
    mine = tmp_path / "my_own_vault"
    (mine / "Vault" / "projects").mkdir(parents=True)
    monkeypatch.setenv("MIND_ROOT", str(mine))

    assert str(resolve_root()) == str(mine)


def test_fake_brain_is_not_a_git_repo_so_writers_cannot_commit_into_it(tmp_path: Path) -> None:
    root = resolve_root()

    assert root is not None
    assert not (root / ".git").exists(), (
        "假库被当成了真仓的替代品：MindWriter 的 _git_toplevel 守卫会放行提交，"
        "测试产物就会落进某个仓的历史里"
    )
    assert (root / "AGENTS.md").is_file()


def test_no_test_writes_into_the_machines_real_brain(tmp_path: Path) -> None:
    """这台机器上真脑的存在必须被当作事实核对，而不是靠 fixture 的自我声明。"""
    if not _MACHINE_MIND:
        pytest.skip("这台没登记 MIND_ROOT，没有可比对的真脑")

    real = Path(_MACHINE_MIND)
    before = {p.name for p in (real / "Vault" / "projects").glob("*")} if (real / "Vault" / "projects").is_dir() else set()

    resolve_root()  # 每条测试都会走这条路；这里只验"走一次不会碰真脑"

    after = {p.name for p in (real / "Vault" / "projects").glob("*")} if (real / "Vault" / "projects").is_dir() else set()
    assert after == before, f"真脑被写了：{after ^ before}"
