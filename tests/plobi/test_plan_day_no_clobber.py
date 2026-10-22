"""P0 `WP-PLAN-DAY-CLOBBER`：说一句「排一下明天」不许把排好的块抹掉。

现核成因（10-17 17:2x，读码不是推测）：`SECRETARY_ASK_SCHEMA` 的描述写着「『排一下明天』→
intent=`plan_day`」，而 `plan_day` **不带 `action`** 时走 `registry.run_plan_day` →
`planning.generate_evening_plan`，那个函数**先 DELETE 当天 `plan_items` 再重插**；排程本体已按
裁定 149 退役 ⇒ 它重插不出任何块，于是拿一份 0 块的计划覆盖掉秘书真排出来的东西
（这台 15:53 落的那 8 块就在这个口子上）。同一趟用户自己也说得出一样的话，所以这不是边角。

两条判据钉在一处：
① 缺 action ⇒ 照实拒绝 + **零写入**，`plan_items` 一行都不许动（负例先行：先证明那块确实在库里）；
② 秘书那一发（嘴→`plan_and_store_day`）照常能写——不许为了安全把嘴焊死。

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
    plan = store.get_daily_plan(conn, TOMORROW)
    assert plan is not None, "起点必须先有那份计划，否则下面的『没变』是空对空"
    return [
        (item.id, item.title, str(item.start_at), str(item.end_at), item.kind,
         json.dumps(item.evidence or {}, sort_keys=True, ensure_ascii=False))
        for item in store.list_plan_items(conn, plan.id)
    ]


def test_a_plan_day_without_action_refuses_and_writes_nothing(conn, db_path):
    """判据①：负例先行——先证那八块确实在，再调那句「安排一下明天的任务」，逐字不变。"""
    before = _seed_eight(conn, db_path)
    planned = [row for row in before if '"planner"' in row[5]]
    assert len(planned) == 8, f"起点必须有 8 块带 planner 的行，不然这条没有信息量：{len(planned)}"

    result = registry.run_secretary_ask(
        "plan_day", "安排一下明天的任务", date=TOMORROW, pipeline=_FakePipeline(db_path)
    )

    assert result["ok"] is False, result
    assert result.get("written") == 0, result
    text = str(result.get("error") or "")
    assert "plan_tomorrow" in text, f"得把能落库的那条路指给用户：{text[:120]}"
    assert _snapshot(conn) == before, "plan_day 缺 action 仍然动了那批已排好的块"


def test_b_the_refusal_is_not_just_empty_handed_it_names_the_reason(conn, db_path):
    """拒绝必须说得出为什么（否则用户只看到「没做成」）：那句得点出「会删掉已排好的」。"""
    _seed_eight(conn, db_path)

    result = registry.run_secretary_ask(
        "plan_day", "排一下明天", date=TOMORROW, pipeline=_FakePipeline(db_path)
    )

    text = str(result.get("error") or "")
    assert ("删" in text or "抹" in text) and ("块" in text or "计划" in text), text


def test_c_a_missing_plan_is_reported_as_missing_not_generated(conn, db_path):
    """那天本来没计划 ⇒ 照实说没有，**不**顺手生成一份（生成＝DELETE 重插，正成本刀要堵的口）。

    两条都走一遍：缺 action 的那句在路由上就被拒；真走到 `run_plan_day`（带 action 的下游形态）
    也必须只读——所以断言的是「读完之后库里还是什么都没有」。
    """
    assert store.get_daily_plan(conn, TOMORROW) is None

    refused = registry.run_secretary_ask(
        "plan_day", "明天有什么安排", date=TOMORROW, pipeline=_FakePipeline(db_path)
    )
    assert refused["ok"] is False, refused
    assert store.get_daily_plan(conn, TOMORROW) is None, "说一句就多出一份计划＝还在走生成那条路"

    read = registry.run_plan_day("明天有什么安排", date=TOMORROW, pipeline=_FakePipeline(db_path))
    assert read["ok"] is False, read
    assert read["items"] == [], read
    assert store.get_daily_plan(conn, TOMORROW) is None, "只读那条又去生成了"


def test_d_the_secretary_shot_still_writes_eight_blocks(conn, db_path):
    """判据②：嘴那条路照旧能落库——安全不能靠把写入口焊死。"""
    _seed_eight(conn, db_path)
    plan = store.get_daily_plan(conn, TOMORROW)
    blocks = [item for item in store.list_plan_items(conn, plan.id)
              if (item.evidence or {}).get("planner")]
    assert len(blocks) == 8, [item.title for item in blocks]
    assert all((item.evidence or {}).get("source") for item in blocks), \
        [dict(item.evidence) for item in blocks]
