"""计划项要有完成态（`WP-PLAN-COMPLETION-ACK`）：写完的作业不许第二天又被排进来。

现核铁证是同一条 `ddl:evt_ac46920dc0c1` 连排五天（10-12…10-16，15 块只对应 5 个 event）。
两条测试，负例优先，都写成关系不写数值快照：

1. **标完成 → 重跑当晚那份计划**：那一块必须消失，**且不许别的作业块被挪进它的位置**
   （预算/均分是裁定 148 与秘书线 `WP-PLAN-BY-LLM` 的事，本刀不并）。
2. **状态落在事件上、不落在那一晚的计划行上**：排程每晚把当天的 `plan_items` DELETE 后重插，
   把完成态存在计划行里＝存在它会抹掉它的那次重排里。撤销不是删除；查不到那条不许静默成功。
"""

from __future__ import annotations

from datetime import date

import pytest

from plobi.agenda import planning, store

DAY = date(2026, 10, 16)


@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "agenda.db"


@pytest.fixture()
def conn(db_path):
    connection = store.connect(db_path)
    yield connection
    connection.close()


def _ddl(connection, uid: str, title: str) -> str:
    event = store.create_event(
        connection, title=title, start_at=f"{DAY.isoformat()}T23:55:00",
        kind="ddl", status="pending", source="zju",
        evidence={"uid": uid, "course": "电工电子学", "course_id": "1", "due": f"{DAY.isoformat()}T23:55:00"},
    )
    return event.id


def _planned_event_ids(connection, db_path):
    """`plan_items.event_id` 带来源前缀（现核：`ddl:evt_ac46920dc0c1` 连排五天）
    ⇒ 比的是**去掉前缀之后**那件事，别把前缀形状当成本刀要钉的关系。"""
    plan = planning.generate_evening_plan(DAY, db_path=db_path)
    items = store.list_plan_items(connection, plan.id)
    return plan, {item.event_id.split(":", 1)[-1] for item in items}


def test_marking_done_removes_that_block_and_nothing_moves_in(conn, db_path):
    first = _ddl(conn, "evt_first", "第5次课后作业-(2.3)")
    other = _ddl(conn, "evt_other", "实验报告提交-实验三")

    _, before = _planned_event_ids(conn, db_path)
    assert before == {first, other}                       # 两条都在板上（前置，不是断言目标）

    assert conn.execute("SELECT completed_at FROM events WHERE id = ?", (first,)).fetchone()[0] is None
    store.mark_event_completed(conn, first)

    _, after = _planned_event_ids(conn, db_path)

    assert after == {other}        # 关系断言：减一条，**没有第三条被挪进来**
    assert first not in after


def test_completion_lives_on_the_event_and_is_not_silent(conn, db_path):
    event_id = _ddl(conn, "evt_undo", "焊接作业一")

    store.mark_event_completed(conn, event_id, at="2026-10-16T07:00:00")
    assert store.get_event(conn, event_id).completed_at == "2026-10-16T07:00:00"

    # 撤销承认：那一格回到 None，事件行本身一行没少（撤销不是删除）
    store.mark_event_completed(conn, event_id, completed=False)
    again = store.get_event(conn, event_id)
    assert again.completed_at is None and again.title == "焊接作业一"

    # 不存在的 id ⇒ 明说没有，不许回一句成功
    assert store.mark_event_completed(conn, "evt_never_existed") is None

    # 迁移幂等：init 跑第二遍不许炸（旧库第一次跑会 ALTER 加列）
    store.init_db(conn)
    assert "completed_at" in {row["name"] for row in conn.execute("PRAGMA table_info(events)")}
