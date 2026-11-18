"""采集腿的形状守卫（`WP-COLLECT-UNIFY`，2026-11-17）。

两条不变量，用这一档钉住，防止形状漂回去：

- **I1 落点只有一处**：全仓把 `Occurrence` 写进池的代码**只有**
  `plobi/collectors/base.py::land_occurrences` 一处。
  从前有**五条**路：微信／教务／浙大／课表各自 `pool.land`，公共层内联一份 `PoolItem`——
  而分岔点是 `getattr(collector, "collect", None)` **运行时探测**出来的隐藏协议
  （`collect()` 根本不在 `base.Collector` 契约里）⇒ 落池语义取决于你抄了谁。
- **I2 触发面只有一处**：采集侧的触发都汇到 `plobi/collectors/sweep.py`；
  微信那 4 条私有路径（webhook／手动扫／巡检／回扫）不再直接调 pipeline。

**为什么写成「读源码」的守卫而不是纯行为测试**：行为测试只能证明「这一条路通」，
证不了「**没有别的路**」。而这一刀要防的恰恰是「又长出一条」——所以直接扫那一棵树。
"""

from __future__ import annotations

from pathlib import Path

import pytest

import plobi.collectors
from plobi.collectors.base import Occurrence, land_occurrences, pool_items_of

COLLECTORS_DIR = Path(plobi.collectors.__file__).parent

#: 首启导入那一路（`file_import.py`）**不是采集腿**：它导入的是用户手动放进来的文件，
#: 形状是 `PoolItem` 而不是 `Occurrence`，而且它在 `WP-SOURCE-FIRST-RUN-IMPORT`（在派）
#: 名下——本刀不许顺手改它。这一格在这里写明，是为了让守卫不看着像在藏一个洞。
NOT_A_LEG = {"plobi/collectors/file_import.py"}

#: 「写池」那几种写法：`from plobi.pool import land ...`／`pool.land(...)`。
LANDING_PATTERNS = ("from plobi.pool import land", "pool.land(", "land as pool_land", "land as _land")


def _rel(path: Path) -> str:
    return path.relative_to(COLLECTORS_DIR.parents[1]).as_posix()


def _python_sources() -> list[Path]:
    return sorted(p for p in COLLECTORS_DIR.rglob("*.py") if p.is_file())


# --- I1 落点只有一处 --------------------------------------------------------


def test_only_one_file_in_the_collectors_tree_lands_into_the_pool() -> None:
    """采集腿那一棵树里，**只有 `base.py`** 碰得到 `plobi.pool.land`。"""
    landing_files = {
        _rel(path)
        for path in _python_sources()
        if any(pattern in path.read_text(encoding="utf-8") for pattern in LANDING_PATTERNS)
    }

    unexpected = landing_files - {"plobi/collectors/base.py"} - NOT_A_LEG
    assert not unexpected, (
        "落点又长出第二条路了："
        + "、".join(sorted(unexpected))
        + " —— 落池只有 `plobi/collectors/base.py::land_occurrences` 一处"
        "（用户 11-17 拍「落点收成一处」）"
    )


def test_the_single_landing_is_the_public_one() -> None:
    """那一处必须是**公共层**那一个（不是某一族自己藏一份）。"""
    text = (COLLECTORS_DIR / "base.py").read_text(encoding="utf-8")
    assert "def land_occurrences(" in text, "公共层那个落点函数不见了"
    assert "from plobi.pool import land" in text, "它不再经池的写入口落地"


def test_the_public_land_carries_the_window_columns_and_the_body() -> None:
    """会话两列与正文**由公共那一份映射带下去**——从前有一份副本漏了两列。"""
    occurrence = Occurrence(
        source="wechat",
        external_id="m1",
        title="标题",
        start_at="2026-08-25 10:00:00",
        body="正文全文",
        conversation="班级群",
        visibility="group",
        evidence={"talker": "班级群"},
    )

    items = pool_items_of([occurrence], source="wechat")

    assert len(items) == 1
    assert items[0].body == "正文全文"
    assert items[0].conversation == "班级群"
    assert items[0].visibility == "group"
    assert items[0].context == {"talker": "班级群"}


def test_landing_writes_through_the_public_function(tmp_path) -> None:
    """落池走公共那一处，落进去的那一行**带着那两列**。"""
    import plobi.pool as pool

    original = pool.store.path
    pool.store.path = lambda: tmp_path / "pool.db"
    try:
        result = land_occurrences(
            [
                Occurrence(
                    source="wechat",
                    external_id="m1",
                    title="标题",
                    start_at="2026-08-25 10:00:00",
                    body="正文全文",
                    conversation="班级群",
                    visibility="group",
                )
            ],
            source="wechat",
        )
        rows = pool.query()
    finally:
        pool.store.path = original

    assert result["created"] == 1
    assert len(rows) == 1
    assert rows[0]["conversation"] == "班级群"
    assert rows[0]["visibility"] == "group"
    assert rows[0]["body"] == "正文全文"


# --- I2 触发面只有一处 ------------------------------------------------------

#: 微信那几条从前各自触发的地方。
PRIVATE_TRIGGER_FILES = (
    "plobi/collectors/chatlog/webhook.py",
    "plobi/collectors/chatlog/watchdog.py",
    "plobi/collectors/chatlog/collect_api.py",
)


@pytest.mark.parametrize("rel", PRIVATE_TRIGGER_FILES)
def test_the_private_triggers_no_longer_call_the_pipeline_directly(rel: str) -> None:
    """那几条私有路径**不再自己调 pipeline**——触发面只有 `sweep` 那一处。"""
    text = (COLLECTORS_DIR.parents[1] / rel).read_text(encoding="utf-8")

    for banned in ("run_once(", "handle_message("):
        assert banned not in text, (
            f"{rel} 里还有 `{banned}`：那是**另一条触发路径**"
            "（用户 11-17 拍「私有路径全部收进公共触发面」）"
        )


@pytest.mark.parametrize("rel", PRIVATE_TRIGGER_FILES)
def test_the_private_triggers_go_through_the_common_one(rel: str) -> None:
    """它们改成走公共那一处（`run_source`／`trigger_round`）。"""
    text = (COLLECTORS_DIR.parents[1] / rel).read_text(encoding="utf-8")

    assert "run_source" in text or "trigger_round" in text, (
        f"{rel} 没有走公共触发面：触发面只有 `plobi/collectors/sweep.py` 那一处"
    )


def test_the_common_trigger_surface_exists_and_is_one() -> None:
    """公共触发面：`run_sweep`（整轮）与 `run_source`（单腿）——后者是前者的薄壳。"""
    from plobi.collectors import sweep

    assert callable(sweep.run_sweep)
    assert callable(sweep.run_source)

    # 单腿那一格是整轮的薄壳，不许自己再造一套调度。
    source = (COLLECTORS_DIR / "sweep.py").read_text(encoding="utf-8")
    assert "run_sweep(service=service, sources=(source_id,)" in source, (
        "`run_source` 不再是 `run_sweep` 的薄壳了 ⇒ 又要长出第二套触发"
    )


def test_run_source_returns_the_leg_own_report() -> None:
    """单腿触发要能拿回**腿自己那份读数**（逐条 `created`／`updated` 那些）。"""
    from plobi.collectors.sweep import run_source

    class _Leg:
        def collect(self, service=None, **params):
            return {
                "state": "ready",
                "rows_seen": 3,
                "created": 2,
                "updated": 0,
                "unchanged": 1,
                "created_ids": ["m1", "m2"],
                "pending_ids": ["m1", "m2"],
                "report": {"scanned": 3, "created": ["m1", "m2"], "updated": []},
            }

    outcome = run_source("wechat", collectors={"wechat": _Leg()})

    assert outcome["ok"] is True
    assert outcome["counts"]["created"] == 2
    assert outcome["report"]["created_ids"] == ["m1", "m2"]
    assert outcome["report"]["report"]["created"] == ["m1", "m2"]


def test_params_are_passed_through_to_the_leg() -> None:
    """腿自己认的那几格参数（回看几天／推送来的那批）由触发方递进来。"""
    from plobi.collectors.sweep import run_source

    seen: dict = {}

    class _Leg:
        def collect(self, service=None, **params):
            seen.update(params)
            return {"state": "ready", "created": 0, "updated": 0, "unchanged": 0}

    run_source("wechat", params={"lookback_days": 3}, collectors={"wechat": _Leg()})

    assert seen == {"lookback_days": 3}
