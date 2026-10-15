"""名册改了，跑着的后端要认得（`WP-REGISTRY-RELOAD-FRESH`）。

判据两条，都写成**关系**不写成快照，负例在先：

1. 文件没动 ⇒ 两次 `get_registry()` 必须是同一个对象。这条防的是"把缓存整个删掉"那种假修法——
   那样每个轮询都重读整份 `projects.yaml`，正是这条刀明确禁止的成本形状。
2. 进程内直接往那份文件 `upsert` + `save` 一条新条目，**不重启、不调 API** ⇒ 下一次
   `get_registry()` 必须看得见它（今天 17:59 给五门课落身份走的就是这条路，后端读的还是启动那一刻的账）。

跑法：`bash scripts/run_tests.sh tests/plobi/test_registry_reload_fresh.py -q`（唯一入口，不传 `--basetemp`）。
"""

from __future__ import annotations

import pytest

from plobi.agents.registry import AgentEntry, AgentRegistry
from plobi.console import router as console_router


@pytest.fixture(autouse=True)
def tmp_registry(tmp_path, monkeypatch):
    """一份 tmp 名册 + 一个假脑库：任何一步都不许碰真家与真 Mind。"""
    monkeypatch.delenv("PLOBI_PROJECTS_CONFIG", raising=False)
    (tmp_path / "mind").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MIND_ROOT", str(tmp_path / "mind"))
    path = tmp_path / "projects.yaml"
    monkeypatch.setattr("plobi.agents.registry.default_path", lambda: path)
    console_router.set_registry(AgentRegistry(path=path))
    yield path
    console_router.set_registry(None)


def test_an_unchanged_registry_file_is_not_read_again(tmp_registry):
    """文件没动 ⇒ 同一个对象。删掉缓存冒充"每次都新鲜"在这里当场红。"""
    first = console_router.get_registry()

    assert console_router.get_registry() is first


def test_a_row_written_to_disk_by_someone_else_shows_up_without_a_restart(tmp_registry):
    """手改 / CLI 写名册这条路：不重启、不调 API，读的人也要看得见新行。"""
    before = {entry.name for entry in console_router.get_registry().entries()}
    assert "later" not in before

    on_disk = AgentRegistry.load(tmp_registry)
    on_disk.upsert(AgentEntry.from_dict("later", {"role": "l2_project", "profile": "later"}))
    on_disk.save()

    after = {entry.name for entry in console_router.get_registry().entries()}

    assert "later" in after
