"""家目录那份采集脚本与仓里那一版之间的对齐手（`WP-SOURCE-REFRESH` 欠的那一格）。

跑法：``bash scripts/run_tests.sh tests/plobi/test_cron_collect_sweep_drift.py -q``

为什么值得单独钉：`cron` 把相对名的作业脚本解析到 `$PLOBI_HOME/scripts/`，而那个名字部署时还换过
（`cron_collect_sweep.py` → `plobi_collect_sweep.py`）——**每一轮跑的都是复制品**。复制品没人维护，
10-23 就出现过"屏上频率改好了、跑的是旧版、三处读数都读不出这是旧脚本"。这里钉的三件事：
旧了要刷回来并说出来；一致时不许吭声；装成 wheel 那种旁边没有对照版时也不许吭声。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "plobi" / "cron_collect_sweep.py"


def _load():
    spec = importlib.util.spec_from_file_location("cron_collect_sweep_under_test", REPO_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakePlobiPackage:
    """假装 `import plobi` 解到的那枚包住在某个仓根下（不真去 import 产品那个）。"""

    def __init__(self, root: Path) -> None:
        self.__file__ = str(root / "plobi" / "__init__.py")


def _layout(tmp_path, *, repo_bytes: bytes, home_bytes: bytes):
    root = tmp_path / "repo"
    twin = root / "scripts" / "plobi" / "cron_collect_sweep.py"
    twin.parent.mkdir(parents=True, exist_ok=True)
    twin.write_bytes(repo_bytes)
    home = tmp_path / "home" / "plobi_collect_sweep.py"
    home.parent.mkdir(parents=True, exist_ok=True)
    home.write_bytes(home_bytes)
    return root, twin, home


def test_a_stale_home_copy_is_written_back_and_says_so(tmp_path, monkeypatch) -> None:
    module = _load()
    root, twin, home = _layout(tmp_path, repo_bytes=b"NEW round of collection code\n",
                               home_bytes=b"OLD copy from a previous install\n")
    monkeypatch.setattr(module, "__file__", str(home))
    monkeypatch.setitem(sys.modules, "plobi", _FakePlobiPackage(root))

    drift = module._align_with_repo_copy()

    assert drift is not None and drift["state"] == "refreshed"
    assert home.read_bytes() == twin.read_bytes(), "刷回来之后两份必须逐字一致"
    assert "这一轮仍按旧版算" in drift["note"], "旧代码算完的这一轮要照实说，不能装作新的一样"


def test_identical_copies_say_nothing(tmp_path, monkeypatch) -> None:
    module = _load()
    root, _twin, home = _layout(tmp_path, repo_bytes=b"same bytes\n", home_bytes=b"same bytes\n")
    monkeypatch.setattr(module, "__file__", str(home))
    monkeypatch.setitem(sys.modules, "plobi", _FakePlobiPackage(root))

    assert module._align_with_repo_copy() is None, "一致却吭声＝每天给用户发一次假事故"


def test_a_deployment_without_the_repo_twin_stays_quiet(tmp_path, monkeypatch) -> None:
    module = _load()
    root = tmp_path / "site-packages"
    (root / "plobi").mkdir(parents=True)
    home = tmp_path / "home" / "plobi_collect_sweep.py"
    home.parent.mkdir(parents=True, exist_ok=True)
    home.write_bytes(b"whatever\n")
    monkeypatch.setattr(module, "__file__", str(home))
    monkeypatch.setitem(sys.modules, "plobi", _FakePlobiPackage(root))

    assert module._align_with_repo_copy() is None, "装进 wheel 的那种部署旁边没有对照版，不该报警"
