"""勾一次，**往后每一天**都不许再排它（`WP-PLAN-COMPLETION-ACK` 的跨天那一格）。

同档 `test_plan_completion.py` 钉的是「当晚重跑，那块消失」。现核铁证比那更狠：真库只读取
（10-16，`agenda.db`）——`evt_ac46920dc0c1`「第5次课后作业-(2.3)」`kind=ddl`、`status=pending`、
`start_at=2026-10-18T23:55:00`，**还没到期**，却在 10-12／10-13／10-14／10-15／10-16 五天各排了一块
（`plan_items.event_id = 'ddl:evt_ac46920dc0c1'`）。原因是排程往前看 14 天
（`plobi/agenda/planning.py:34` 的 `DDL_LOOKAHEAD_DAYS`），所以一条作业在到期之前**每天都会被重排一块**。

⇒ 完成态必须同时是**往后每一天**的排程输入。只治当晚等于没治：那正是「做完勾一下就别再排给我」
这句话的字面要求。三条测试都写成关系，不写数值快照；负例（未勾时天天都在）先行，
这样「因为排程压根没排它」这种假绿过不了本档。

**裁定 163 之后这一档怎么读**（10-16 深夜随 `WP-NIGHTLY-PLAN-BY-AGENT` 删均分本体迁过）：
`generate_evening_plan` 不再自己造块，没接 planner 就一个块都没有（那条兜底正是裁定 149 要退役的
形状）。所以本档注入一个假 planner，**作业池取自秘书真看到的那份材料**
（:func:`plobi.agenda.llm_planning.plan_materials`）——在测试里自己数一遍作业，等于把判据搬到
测试里，代码那侧怎么改都还是绿。池子来自生产 ⇒ 「勾一次，往后每一天都不再排它」钉的仍然是**排程输入**。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from plobi.agenda import llm_planning, planning, store

DUE = date(2026, 10, 18)          # 铁证那条的截止日（真库 start_at=2026-10-18T23:55:00）
DAYS = (date(2026, 10, 16), date(2026, 10, 17), DUE)


@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "agenda.db"


@pytest.fixture()
def conn(db_path):
    connection = store.connect(db_path)
    yield connection
    connection.close()


def _real_ddl(connection) -> str:
    """铁证那条形状：学在浙大采来的课件名作业，截止在未来。"""
    return store.create_event(
        connection,
        title="第5次课后作业-(2.3)",
        start_at=f"{DUE.isoformat()}T23:55:00",
        kind="ddl",
        status="pending",
        source="zju",
        evidence={
            "uid": "evt_ac46920dc0c1",
            "course": "电工电子学",
            "course_id": "1",
            "due": f"{DUE.isoformat()}T23:55:00",
        },
    ).id


def _material_ddl_ids(conn, day: date) -> set[str]:
    """秘书那一发的**排程输入**里，这几天看得见哪些作业（生产口径，不自己数）。"""
    materials = llm_planning.plan_materials(conn, day.isoformat())
    return {str(row["id"]) for row in [*materials["deadlines"], *materials["upcoming_deadlines"]]}


def _planner_from_materials(conn, day):
    """假 planner：交的材料池 = :func:`_material_ddl_ids`，槽位按序号点名（夹具不是分配数学）。"""
    materials = llm_planning.plan_materials(conn, str(day))
    start = datetime.fromisoformat(f"{day}T09:00:00")
    pool = [*materials["deadlines"], *materials["upcoming_deadlines"]]
    blocks = [
        {
            "event_id": f"ddl:{row['id']}",
            "title": f"写作业 · {row['title']}",
            "start_at": (start + timedelta(minutes=60 * i)).isoformat(),
            "end_at": (start + timedelta(minutes=60 * (i + 1))).isoformat(),
            "kind": "task",
            "conflict_with": [],
            "evidence": {
                "kind": "ddl_block", "planner": "l1", "source": f"ddl:{row['id']}",
                "ddl_event_id": row["id"], "due_at": row["start_at"],
            },
        }
        for i, row in enumerate(pool)
    ]
    return blocks, "（测试注入的那一发）"


def _planned_ids(connection, db_path, day: date) -> set[str]:
    """重跑那一天那份计划，取计划里的事件 id（去掉 `ddl:` 那一层前缀，比的是同一件事）。"""
    plan = planning.generate_evening_plan(day, db_path=db_path, planner=_planner_from_materials)
    return {item.event_id.split(":", 1)[-1] for item in store.list_plan_items(connection, plan.id)}


def test_without_an_ack_the_same_ddl_is_planned_on_every_day_up_to_its_deadline(conn, db_path):
    """负例控制：没勾的时候，它**确实**到期前几天天天被排——本档后面那两条才有意义。"""
    event_id = _real_ddl(conn)

    for day in DAYS:
        assert event_id in _material_ddl_ids(conn, day), f"材料里没有它＝下面那条空对空 {day}"
        assert event_id in _planned_ids(conn, db_path, day), day


def test_one_ack_clears_it_from_every_later_day_and_nothing_else_moves_in(conn, db_path):
    event_id = _real_ddl(conn)
    other = store.create_event(
        conn, title="实验报告提交-实验三", start_at=f"{DUE.isoformat()}T20:00:00",
        kind="ddl", status="pending", source="zju",
        evidence={"uid": "evt_other", "course": "电工电子学", "course_id": "1"},
    ).id
    before = {day: _planned_ids(conn, db_path, day) for day in DAYS}
    assert all({event_id, other} <= ids for ids in before.values()), before

    store.mark_event_completed(conn, event_id)

    for day in DAYS:
        assert event_id not in _material_ddl_ids(conn, day), f"还在材料里＝没关掉 {day}"
        after = _planned_ids(conn, db_path, day)
        assert event_id not in after, day
        # 关系断言：只少了勾掉那一条，别的作业没被挪进来替它顶位
        # （均分与预算是裁定 148/149 与 `WP-PLAN-BY-LLM` 的事，本刀不并）。
        assert after == before[day] - {event_id}, day


def test_undo_puts_it_back_on_the_later_days(conn, db_path):
    """撤销承认不是删除：历史一行不动，排程那一侧重新看得见它。"""
    event_id = _real_ddl(conn)
    store.mark_event_completed(conn, event_id)
    assert all(event_id not in _planned_ids(conn, db_path, day) for day in DAYS)

    store.mark_event_completed(conn, event_id, completed=False)

    assert conn.execute(
        "SELECT completed_at FROM events WHERE id = ?", (event_id,)
    ).fetchone()[0] is None
    assert event_id in _planned_ids(conn, db_path, DAYS[1])


def test_the_read_side_still_sees_history_so_the_filter_is_what_did_it(conn, db_path):
    """消失必须归功于排程侧那一句 `include_completed=False`，不是"记录被抹了"。

    读侧默认值没动：勾过的仍然从 `store.list_events` 里回得来。谁把默认值改成 `False`
    （想"干净"一点），这条当场红——那会把历史一起抹掉，是另一件事。
    """
    event_id = _real_ddl(conn)
    store.mark_event_completed(conn, event_id)

    window = {"start_from": f"{DAYS[0].isoformat()}T00:00:00", "start_to": f"{DUE.isoformat()}T23:59:59"}
    assert event_id in {e.id for e in store.list_events(conn, **window)}
    assert event_id not in {
        e.id for e in store.list_events(conn, include_completed=False, **window)
    }
