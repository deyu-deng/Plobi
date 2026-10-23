"""完成态（`WP-PLAN-COMPLETION-ACK`）：写完的作业不许第二天又被排进来。

现核铁证是同一条 DDL 连排五天。计划层 10-22 退役后，完成态的真源仍是
`events.completed_at`，排程由 L1 交提案、过闸后直接写 events。两条测试，负例优先：

1. **标完成 → 重跑当晚那一发**：那一条必须从材料里消失（因此提案里也不再出现），
   **且不许别的作业被挪进它的位置**。
2. **状态落在事件上**：重排按（日, kind, 标题）覆盖同一行时不动这一格。
   撤销不是删除；查不到那条不许静默成功。
"""

from __future__ import annotations

from datetime import date

import pytest

from plobi.agenda import llm_planning, store

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


def _planned_titles(connection, day: date, titles: set[str]) -> set[str]:
    """让假 L1 按**材料里现有的**作业交提案；返回真正落成 events 的标题。"""
    def proposal(_conn, for_date):
        materials = llm_planning.plan_materials(_conn, for_date)
        wanted = [row for row in materials["deadlines"] if row["title"] in titles]
        items = []
        for index, row in enumerate(wanted):
            hour = 9 + index
            items.append({
                "title": row["title"],
                "start_at": f"{for_date}T{hour:02d}:00:00",
                "end_at": f"{for_date}T{hour + 1:02d}:00:00",
            })
        return items, "按材料里现有的作业排"

    result = llm_planning.plan_and_store_day(
        day.isoformat(), conn=connection, proposal_fn=proposal)
    assert result["ok"] is True, result
    return {event["title"] for event in result["events"]}


def test_marking_done_removes_that_block_and_nothing_moves_in(conn, db_path):
    first = _ddl(conn, "evt_first", "第5次课后作业-(2.3)")
    _ddl(conn, "evt_other", "实验报告提交-实验三")
    first_title = "第5次课后作业-(2.3)"
    other_title = "实验报告提交-实验三"
    titles = {first_title, other_title}

    before = _planned_titles(conn, DAY, titles)
    assert before == titles                      # 两条都在（前置，不是断言目标）

    assert conn.execute("SELECT completed_at FROM events WHERE id = ?", (first,)).fetchone()[0] is None
    store.mark_event_completed(conn, first)

    after = _planned_titles(conn, DAY, titles)

    assert after == {other_title}   # 关系断言：减一条，**没有第三条被挪进来**
    assert store.get_event(conn, first).completed_at is not None, "完成的那条事件本身不许被删"


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
