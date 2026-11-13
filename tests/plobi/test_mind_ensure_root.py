"""新家第一次启动时，脑必须自己长出来（`WP-MIND-BOOTSTRAP`）。

2026-11-08 现场：装机版 0.3.0 的家是 ``D:\\Data\\AppData\\Plobi``，那个家下面从来没有 ``mind``；
``resolve_root`` 答 ``None``，笔记页与日记页一起报「MIND_ROOT 未设置或无 Mind surface」。
事实是**家认得、脑没有**，而没有任何一处代码负责建。这里钉住补上的那一段的行为契约：

  1. 家里没有脑、也没有 ``MIND_ROOT`` 指针 ⇒ :func:`ensure_root` 建出 ``<家>/mind``，
     并且建完之后 :func:`resolve_root` **认得出它**（自建与自认必须闭环，否则建了等于没建）；
  2. 幂等：第二次调用不改任何东西——不重建、不覆盖、不碰时间戳；
  3. 已存在的 ``AGENTS.md`` 是人写的契约文件，一个字节都不动；
  4. 已经是脑的库原样保留，多出来的文件不删。

``tests/conftest.py`` 已经把 ``PLOBI_HOME`` 关进 per-test tempdir，所以这些用例碰不到这台机器
上登记的任何一个真家。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from plobi.mind import paths as mind_paths


@pytest.fixture()
def no_pointer_home(tmp_path: Path, monkeypatch) -> Path:
    """一个空家，且没有任何 ``MIND_ROOT`` / ``PLOBI_DEV_HOME`` 指针指过来。"""
    home = tmp_path / "plobi_home_no_brain"
    home.mkdir()
    monkeypatch.setenv("PLOBI_HOME", str(home))
    monkeypatch.delenv("MIND_ROOT", raising=False)
    monkeypatch.delenv("PLOBI_DEV_HOME", raising=False)
    return home


def test_first_launch_builds_a_brain_and_resolves_it(no_pointer_home: Path) -> None:
    assert mind_paths.resolve_root() is None, "前提被破坏：空家不该解析出脑"

    root = mind_paths.ensure_root()

    assert root == no_pointer_home / "mind"
    assert root is not None and root.is_dir()
    assert (root / "AGENTS.md").is_file(), "没有 AGENTS.md 的目录不算脑（仓内两档就是这么判的）"
    for rel in mind_paths.brain_skeleton():
        assert (root / rel).is_dir(), f"骨架缺了 {rel}"

    # 自建与自认必须闭环：建完这一次之后，不设指针也要解析得到。
    assert mind_paths.resolve_root() == root
    assert mind_paths.require_root() == root
    assert mind_paths.resolve_roots() is not None, "脑有了，两个根的解析也该通"


def test_second_call_is_a_no_op(no_pointer_home: Path) -> None:
    root = mind_paths.ensure_root()
    assert root is not None

    before = {
        str(p.relative_to(root)): (p.stat().st_mtime_ns, p.read_bytes() if p.is_file() else None)
        for p in sorted(root.rglob("*"))
    }

    assert mind_paths.ensure_root() == root

    after = {
        str(p.relative_to(root)): (p.stat().st_mtime_ns, p.read_bytes() if p.is_file() else None)
        for p in sorted(root.rglob("*"))
    }
    assert after == before, "第二次调用动了已有内容"


def test_existing_agents_md_is_never_overwritten(no_pointer_home: Path) -> None:
    mind = no_pointer_home / "mind"
    mind.mkdir()
    mine = "# 我自己写的脑\n\n这里有我的规矩。\n"
    (mind / "AGENTS.md").write_text(mine, encoding="utf-8")

    root = mind_paths.ensure_root()

    assert root == mind
    assert (mind / "AGENTS.md").read_text(encoding="utf-8") == mine


def test_an_existing_brain_keeps_its_own_files(no_pointer_home: Path) -> None:
    mind = no_pointer_home / "mind"
    kept = mind / "wiki" / "daily" / "2026-11-08.md"
    kept.parent.mkdir(parents=True)
    kept.write_text("今天的流水\n", encoding="utf-8")

    mind_paths.ensure_root()

    assert kept.read_text(encoding="utf-8") == "今天的流水\n"


def test_surface_children_only_appear_when_the_library_exists(
    no_pointer_home: Path, tmp_path: Path, monkeypatch
) -> None:
    """指针指着一个还没建出来的 Obsidian 库时，不替用户造格子。"""
    missing = tmp_path / "not_created_yet"
    monkeypatch.setenv("HUMAN_ROOT", str(missing))

    mind_paths.ensure_root()

    assert not missing.exists(), "指针指向的库还不存在，却被凭空建出来了"


def test_surface_children_appear_when_the_library_exists(
    no_pointer_home: Path, tmp_path: Path, monkeypatch
) -> None:
    surface = tmp_path / "surface_lib"
    surface.mkdir()
    monkeypatch.setenv("HUMAN_ROOT", str(surface))

    mind_paths.ensure_root()

    for rel in mind_paths.human_skeleton():
        assert (surface / rel).is_dir(), f"人读面缺了 {rel}"


def test_bootstrap_keeps_the_data_root_and_the_pointer_apart(tmp_path: Path, monkeypatch) -> None:
    """``MIND_ROOT`` 在场时它说了算，家只是家——建脑不往家里塞第二份。"""
    pointer = tmp_path / "explicit_brain"
    monkeypatch.setenv("MIND_ROOT", str(pointer))

    root = mind_paths.ensure_root()

    assert root == pointer
    home = Path(os.environ["PLOBI_HOME"])
    assert not (home / "mind").exists(), "指针在场时不该再按家建一份"
