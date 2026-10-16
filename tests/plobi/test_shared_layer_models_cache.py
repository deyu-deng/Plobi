"""裁定 42 第二手：models.dev 那份目录缓存是共享件，分身不许各带一份。

跑法：`bash scripts/run_tests.sh tests/plobi/test_shared_layer_models_cache.py -q`（唯一入口，不传 `--basetemp`）。
判据写成**路径同一性**与**落点对不对**，不写体积/文件数快照——这台现核过 16 份分身副本 ≈84 MB、
最旧一份停在 10-06 而 root 那份 10-16 08:36 才刷新，那种数只会过期，不会守住形状。
"""

from __future__ import annotations

import json

from agent import models_dev
from plobi_cli import profiles


def _profile_home(tmp_path):
    """<root>/profiles/<name> 两个家都建出来，分身那份先放一枚诱饵。"""
    root = tmp_path / "plobiroot"
    profile = root / "profiles" / "l2-course-x"
    profile.mkdir(parents=True)
    (root / "models_dev_cache.json").write_text('{"root":true}', encoding="utf-8")
    (profile / "models_dev_cache.json").write_text('{"decoy":true}', encoding="utf-8")

    return root, profile


def test_cache_path_resolves_to_shared_root_in_profile_mode(tmp_path, monkeypatch):
    """分身活跃时读的是 root 那一份，不是自己那份过期副本。"""
    root, profile = _profile_home(tmp_path)
    monkeypatch.setenv("PLOBI_HOME", str(profile))

    resolved = models_dev._get_cache_path()

    assert resolved == root / "models_dev_cache.json"
    assert resolved != profile / "models_dev_cache.json"
    assert models_dev._load_disk_cache() == {"root": True}, "读侧必须吃 root 那份，不是分身那份过期副本"


def test_saving_never_writes_a_profile_copy_and_clone_all_stops_producing_one(tmp_path, monkeypatch):
    """写侧同一条路：落点只能是被收回的那个 root；`clone-all` 那份生产者也得断掉。"""
    root, profile = _profile_home(tmp_path)
    monkeypatch.setenv("PLOBI_HOME", str(profile))

    models_dev._save_disk_cache({"from": "root-only"})

    assert json.loads((root / "models_dev_cache.json").read_text(encoding="utf-8")) == {"from": "root-only"}
    assert json.loads((profile / "models_dev_cache.json").read_text(encoding="utf-8")) == {"decoy": True}
    assert "models_dev_cache.json" in profiles._CLONE_ALL_STRIP
