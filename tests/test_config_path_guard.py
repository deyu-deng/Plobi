"""家目录必须是真路径——Mock 一律当场打回（裁定 80 测试卫生条款）。

Regression 背景：2026-11-21 17:22 一次全量跑完，仓根多出一个 ``MagicMock/``
目录，里面躺着一份 ``model: test/model`` 的 ``config.yaml``。根因是
``unittest.mock.MagicMock`` 实现了 ``__fspath__``，``Path(MagicMock())``
不报错而是静默变成相对路径 ``MagicMock/mock/<id>``；某个用例把 Mock 当家目录
传给写盘函数，垃圾就落在 pytest 的 CWD 里了。

这里钉住两道闸：
1. :func:`plobi_constants.config_path_for_home` / :func:`shared_root_for`
   收到 Mock 时抛 ``TypeError``，而不是静默拼出一个相对路径；
2. 真 ``Path`` / ``str`` 行为完全不变（生产调用方零影响）。
"""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from plobi_constants import config_path_for_home, shared_root_for


@pytest.mark.parametrize(
    "bad_home",
    [MagicMock(), AsyncMock()],
    ids=["MagicMock", "AsyncMock"],
)
def test_config_path_for_home_rejects_mock_home(bad_home):
    """Mock 当家目录必须抛 TypeError，而不是拼出 MagicMock/mock/<id>。"""
    with pytest.raises(TypeError, match="Mock 当家目录"):
        config_path_for_home(bad_home)


def test_shared_root_for_rejects_mock_home():
    """shared_root_for 是同一条链的入口，同样要拦。"""
    with pytest.raises(TypeError, match="Mock 当家目录"):
        shared_root_for(MagicMock())


def test_mock_home_never_creates_directory(tmp_path, monkeypatch):
    """守卫要在造目录之前就炸，CWD 下不能出现 MagicMock/。"""
    monkeypatch.chdir(tmp_path)
    with pytest.raises(TypeError):
        config_path_for_home(MagicMock())
    assert not (tmp_path / "MagicMock").exists()


def test_real_paths_still_resolve(tmp_path):
    """正常家目录行为不变：非 profile 家 → 自己那份 config.yaml。"""
    assert config_path_for_home(tmp_path) == tmp_path / "config.yaml"
    assert config_path_for_home(str(tmp_path)) == tmp_path / "config.yaml"
    assert shared_root_for(tmp_path) == Path(tmp_path)


def test_profile_home_resolves_to_shared_root(tmp_path):
    """``<root>/profiles/<name>`` 仍按裁定 166 解析到共享根。"""
    profile_home = tmp_path / "profiles" / "course"
    profile_home.mkdir(parents=True)
    assert shared_root_for(profile_home) == tmp_path
    # 共享根那份还没落地 → 回落到分身自己那份
    assert config_path_for_home(profile_home) == profile_home / "config.yaml"
