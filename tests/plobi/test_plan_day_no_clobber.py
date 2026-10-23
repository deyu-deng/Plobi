"""「看看明天」是只读，不许顺手动库（`WP-PLAN-DAY-CLOBBER` 退役后的口径）。

计划层 10-22 整层退役后，`plan_day` 不带 `action` 只读当天的 **events**；
只有 `action=plan_tomorrow` 才打模型、过闸、写 events。判据钉在四处：

① 缺 action ⇒ 只读：`written=0`，库里逐字快照一行不变（负例先行：先证那八块确实在）；
② 只读也要说得出看到了什么（summary 的点数）；
③ 那天本来没日程 ⇒ 照实说没有，**不**顺手生成一份；
④ 秘书那一发（`action=plan_tomorrow`）照常能落八块，且每块带得回材料来源。

全程零额度：写入用 `proposal_fn` 注入点，不碰模型、不碰真 `agenda.db`。
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from plobi.agenda import llm_planning, store
from plobi.agents import registry

TOMORROW = (date.today() + timedelta(days=1)).isoformat()
COURSE = "高等数学"


class _FakeService:
    def __init__(self, db_path):
        self.db_path = db_path


class _FakePipeline:
    """`_secretary_db_path` 只认 `pipeline.service.db_path`——把这条路由钉到 tmp 那份库上。"""

    def __init__(self, db_path):
        self.service = _FakeService(db_path)


def _eight_blocks(_conn, day=TOMORROW):
    """八块互不重叠——就是想让库里确实有八块带 `planner` 的行。"""
    spans = [("09:00", "09:50"), ("10:00", "10:50"), ("11:00", "11:50"), ("13:00", "13:50"),
             ("14:00", "14:50"), ("15:00", "15:50"), ("16:00", "16:50"), ("17:00", "17:50")]
    items = [{"title": f"{COURSE} 第{i + 1}次作业", "start_at": f"{day}T{a}:00",
              "end_at": f"{day}T{b}:00"} for i, (a, b) in enumerate(spans)]
    return items, "上午清两份，下午按截止远近依次清，晚间留整段给项目。"


@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "agenda.db"


@pytest.fixture()
def conn(db_path):
    connection = store.connect(db_path)
    yield connection
    connection.close()


def _seed_eight(conn, db_path) -> list[tuple]:
    """落一版八块的真计划，并回**逐字快照**（含 id 与 evidence，不只数行）。"""
    store.create_event(conn, title=f"{COURSE} 第三次作业", start_at=f"{TOMORROW}T20:00:00",
                       kind="ddl", status="pending", source="zju", evidence={"course": COURSE})
    result = llm_planning.plan_and_store_day(TOMORROW, conn=conn, proposal_fn=_eight_blocks)
    assert result["ok"] is True, result
    assert result["block_count"] == 8, result
    return _snapshot(conn)


def _snapshot(conn) -> list[tuple]:
    events = store.list_events(
        conn, start_from=f"{TOMORROW}T00:00:00", start_to=f"{TOMORROW}T23:59:59")
    assert events, "起点必须先有那天的行，否则下面的『没变』是空对空"
    return [
        (event.id, event.title, str(event.start_at), str(event.end_at), event.kind,
         json.dumps(event.evidence or {}, sort_keys=True, ensure_ascii=False))
        for event in events
    ]


def test_a_plan_day_without_action_is_read_only(conn, db_path):
    """判据①：负例先行——先证那八块确实在，再调那句「安排一下明天的任务」，逐字不变。"""
    before = _seed_eight(conn, db_path)
    planned = [row for row in before if '"planner"' in row[5]]
    assert len(planned) == 8, f"起点必须有 8 块带 planner 的行，不然这条没有信息量：{len(planned)}"

    result = registry.run_secretary_ask(
        "plan_day", "安排一下明天的任务", date=TOMORROW, pipeline=_FakePipeline(db_path)
    )

    assert result["ok"] is True, result
    assert result.get("written") == 0, result
    assert len(result.get("items") or []) == len(before), result
    assert _snapshot(conn) == before, "plan_day 缺 action 仍然动了那批已排好的行"


def test_b_read_only_still_names_what_it_saw(conn, db_path):
    """只读也要说得出看到了什么，不能只回一个空对象。"""
    _seed_eight(conn, db_path)

    result = registry.run_secretary_ask(
        "plan_day", "排一下明天", date=TOMORROW, pipeline=_FakePipeline(db_path)
    )

    assert result["ok"] is True, result
    assert "9 条日程" in str(result.get("summary") or ""), result   # 八块 + 那条作业截止


def test_c_a_missing_day_is_reported_empty_not_generated(conn, db_path):
    """那天本来没日程 ⇒ 照实说没有，**不**顺手生成一份。"""
    assert store.list_events(conn, start_from=f"{TOMORROW}T00:00:00",
                             start_to=f"{TOMORROW}T23:59:59") == []

    read = registry.run_secretary_ask(
        "plan_day", "明天有什么安排", date=TOMORROW, pipeline=_FakePipeline(db_path)
    )
    assert read["ok"] is True, read
    assert read["items"] == [], read
    assert "还没有安排任何事" in str(read.get("summary") or ""), read

    direct = registry.run_plan_day("明天有什么安排", date=TOMORROW, pipeline=_FakePipeline(db_path))
    assert direct["ok"] is True and direct["items"] == [], direct
    assert store.list_events(conn, start_from=f"{TOMORROW}T00:00:00",
                             start_to=f"{TOMORROW}T23:59:59") == [], "只读那条又去生成了"


def test_d_the_secretary_shot_still_writes_eight_blocks(conn, db_path):
    """判据②：嘴那条路照旧能落库——安全不能靠把写入口焊死。"""
    _seed_eight(conn, db_path)
    events = store.list_events(
        conn, start_from=f"{TOMORROW}T00:00:00", start_to=f"{TOMORROW}T23:59:59")
    blocks = [event for event in events if store.is_planner_event(event)]
    assert len(blocks) == 8, [event.title for event in blocks]
    assert all((event.evidence or {}).get("source") for event in blocks), \
        [dict(event.evidence) for event in blocks]
