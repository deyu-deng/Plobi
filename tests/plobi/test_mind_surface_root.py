"""人读面住在**数据家本体**：一根环境变量都不需要（WP-DATA-DIR-ONE-ROOT）。

用户 11-08 16:4x 拍的口径：「数据目录不是就一个就行了吗？而且不需要环境变量也可以
运行呀。mind和日记还有笔记和档案都是用户的不过是用户指定数据目录下的不同文件夹而已。」

判据只认**解析函数自己的答案**，不许手拼字面路径（`WP-VAULT-MIND-TWOROOT` 的铁律：
手拼路径的假读数正是那一族的成因）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from plobi.mind import paths as mind_paths

POINTER_ENV = ("SURFACE_ROOT", "MIND_ROOT", "PLOBI_HOME")


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """一个干净的家：三根指针全清，脑在 ``<家>/mind``。"""
    for key in POINTER_ENV:
        monkeypatch.delenv(key, raising=False)
    data = tmp_path / "home"
    (data / "mind").mkdir(parents=True)
    monkeypatch.setenv("PLOBI_HOME", str(data))
    # 注册表那份钉在测试里一律当没有（这台机器上它本来也没有）。
    monkeypatch.setattr(mind_paths, "_pinned_surface_root", lambda: None)
    return data


def test_notes_diary_and_dossiers_land_in_the_data_home(home: Path):
    """`Surface/…` 落在数据家本体，不落在脑仓里，也不落在一个不存在的旧布局里。"""
    assert mind_paths.real_target("Surface/notes") == home / "notes"
    assert mind_paths.real_target("Surface/diary") == home / "diary"
    assert mind_paths.real_target("Surface/dossiers/projects/Aura") == (
        home / "dossiers" / "projects" / "Aura"
    )


def test_the_brain_stays_in_its_own_folder_of_the_same_home(home: Path):
    """脑还是 ``<家>/mind``——与人读面同属一个家，各占一格。"""
    assert mind_paths.resolve_root() == home / "mind"
    assert mind_paths.real_target("Mind/wiki") == home / "mind" / "wiki"


def test_a_pinned_surface_root_still_wins(home: Path, monkeypatch, tmp_path):
    """指针还是指针说了算：**不静默换家**（口径与这一只口原来那条一致）。"""
    pinned = tmp_path / "elsewhere"
    pinned.mkdir()
    monkeypatch.setenv("SURFACE_ROOT", str(pinned))
    assert mind_paths.real_target("Surface/notes") == pinned / "notes"


def test_the_legacy_layout_inside_the_brain_is_never_recreated(home: Path):
    """老布局（``<家>/mind/Surface``）不许被造出来——那是把用户自己的笔记复制进脑仓。"""
    target = mind_paths.safe_target(
        mind_paths.resolve_root(), "Surface/notes/数学/微积分.md", creating=True
    )
    assert target == home / "notes" / "数学" / "微积分.md"
    assert not (home / "mind" / "Surface").exists()


def test_a_legacy_layout_that_is_still_there_stays_the_home(home: Path):
    """老布局**还在** ⇒ 照旧认它，不静默换家（装机态那一份就是这么活的）。

    这一条与上一条是一对：没它时落到数据家本体，有它时不动它。
    """
    legacy = home / "mind" / "Surface"
    (legacy / "notes").mkdir(parents=True)
    assert mind_paths.real_target("Surface/notes") == legacy / "notes"


def test_first_run_builds_the_human_surface_in_the_data_home(home: Path):
    """首次启动把人读面那几格建在**数据家**里（笔记／日记／档案），不是脑仓里。"""
    mind_paths.ensure_root()
    for name in ("notes", "diary", "dossiers"):
        assert (home / name).is_dir(), f"{name} 没建在数据家里"
    assert (home / "mind" / "AGENTS.md").is_file()
