"""Evening planner: empty window, overlaps, evidence, confirmed lock."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from plobi.agenda import store
from plobi.agenda.planning import (
    EMPTY_PLAN_PUSH,
    EMPTY_PLAN_SUMMARY,
    format_evening_plan_text,
    generate_evening_plan,
    intervals_overlap,
    l1_plan_view,
)

DAY = "2026-09-07"


@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "agenda.db"


@pytest.fixture()
def conn(db_path):
    connection = store.connect(db_path)
    yield connection
    connection.close()


def _items(db_path, plan_id):
    connection = store.connect(db_path)
    try:
        return store.list_plan_items(connection, plan_id)
    finally:
        connection.close()


def test_intervals_overlap_half_open_and_points():
    assert intervals_overlap(
        "2026-09-07T10:00:00",
        "2026-09-07T11:00:00",
        "2026-09-07T10:30:00",
        "2026-09-07T11:30:00",
    )
    assert not intervals_overlap(
        "2026-09-07T10:00:00",
        "2026-09-07T11:00:00",
        "2026-09-07T11:00:00",
        "2026-09-07T12:00:00",
    )
    assert intervals_overlap(
        "2026-09-07T10:30:00",
        None,
        "2026-09-07T10:00:00",
        "2026-09-07T11:00:00",
    )
    assert not intervals_overlap(
        "2026-09-07T11:00:00",
        None,
        "2026-09-07T10:00:00",
        "2026-09-07T11:00:00",
    )
    assert intervals_overlap("2026-09-07T10:00:00", None, "2026-09-07T10:00:00", None)
    assert not intervals_overlap("2026-09-07T10:00:00", None, "2026-09-07T10:01:00", None)


def test_empty_window_writes_honest_empty_plan(db_path):
    plan = generate_evening_plan(DAY, db_path=db_path)
    assert plan.status == "empty"
    assert plan.event_count == 0
    assert plan.conflict_count == 0
    assert plan.summary == EMPTY_PLAN_SUMMARY
    assert plan.confirm_seq is None
    assert plan.evidence == {
        "kind": "empty_window",
        "from": f"{DAY}T00:00:00",
        "to": f"{DAY}T23:59:59",
        "event_count": 0,
    }
    assert _items(db_path, plan.id) == []
    body = format_evening_plan_text(plan)
    assert EMPTY_PLAN_PUSH in body
    view = l1_plan_view(DAY, [], db_path=db_path)
    assert view["status"] == "empty"
    assert view["empty"] is True
    assert view["stale"] is False
    assert "plan_items" not in view


def test_default_for_date_is_tomorrow_empty(db_path):
    plan = generate_evening_plan(db_path=db_path)
    tomorrow = (datetime.now().date() + timedelta(days=1)).isoformat()
    assert plan.for_date == tomorrow
    assert plan.status == "empty"
    assert plan.summary == EMPTY_PLAN_SUMMARY
    assert _items(db_path, plan.id) == []


def test_overlapping_intervals_mark_conflict(db_path, conn):
    left = store.create_event(
        conn,
        title="组会",
        start_at=f"{DAY}T10:00:00",
        end_at=f"{DAY}T11:00:00",
        kind="meeting",
    )
    right = store.create_event(
        conn,
        title="答辩",
        start_at=f"{DAY}T10:30:00",
        end_at=f"{DAY}T11:30:00",
        kind="meeting",
    )
    plan = generate_evening_plan(DAY, db_path=db_path)
    assert plan.status == "pending"
    assert plan.event_count == 2
    assert plan.conflict_count >= 1
    assert plan.confirm_seq is not None
    items = {item.event_id: item for item in _items(db_path, plan.id)}
    assert right.id in items[left.id].conflict_with
    assert left.id in items[right.id].conflict_with


def test_cancelled_does_not_participate(db_path, conn):
    live = store.create_event(
        conn,
        title="课",
        start_at=f"{DAY}T10:00:00",
        end_at=f"{DAY}T11:00:00",
        kind="class",
    )
    dead = store.create_event(
        conn,
        title="取消的会",
        start_at=f"{DAY}T10:30:00",
        end_at=f"{DAY}T11:30:00",
        kind="meeting",
    )
    store.update_event(conn, dead.id, status="cancelled")
    plan = generate_evening_plan(DAY, db_path=db_path)
    assert plan.event_count == 1
    assert plan.conflict_count == 0
    items = _items(db_path, plan.id)
    assert [item.event_id for item in items] == [live.id]


def test_generated_items_always_have_evidence(db_path, conn):
    event = store.create_event(
        conn,
        title="手动课",
        start_at=f"{DAY}T09:00:00",
        kind="class",
        source="manual",
    )
    assert event.evidence is None
    plan = generate_evening_plan(DAY, db_path=db_path)
    items = _items(db_path, plan.id)
    assert len(items) == 1
    assert items[0].evidence == {"kind": "event", "event_id": event.id}


def test_copies_message_fields_onto_event_evidence(db_path, conn):
    event = store.create_event(
        conn,
        title="组会",
        start_at=f"{DAY}T14:00:00",
        kind="meeting",
        source="wechat",
        evidence={
            "msg_id": "m1",
            "talker": "wxid_a",
            "sent_at": "2026-09-06T20:01:00",
            "snippet": "明天下午两点组会",
            "extra": "ignore",
        },
    )
    plan = generate_evening_plan(DAY, db_path=db_path)
    evidence = _items(db_path, plan.id)[0].evidence
    assert evidence["kind"] == "event"
    assert evidence["event_id"] == event.id
    assert evidence["msg_id"] == "m1"
    assert evidence["talker"] == "wxid_a"
    assert evidence["sent_at"] == "2026-09-06T20:01:00"
    assert evidence["snippet"] == "明天下午两点组会"
    assert "extra" not in evidence


def test_store_still_rejects_empty_evidence(conn):
    plan = store.upsert_daily_plan(
        conn,
        for_date=DAY,
        status="pending",
        summary="x",
    )
    with pytest.raises(store.AgendaValidationError):
        store.replace_plan_items(
            conn,
            plan.id,
            [
                {
                    "event_id": "evt_x",
                    "title": "无证据",
                    "start_at": f"{DAY}T09:00:00",
                }
            ],
        )


def test_confirmed_plan_is_not_overwritten(db_path, conn):
    first_event = store.create_event(
        conn,
        title="高数",
        start_at=f"{DAY}T08:00:00",
        kind="class",
    )
    first = generate_evening_plan(DAY, db_path=db_path)
    locked = store.set_plan_status(conn, first.id, "confirmed")
    assert locked is not None
    store.create_event(
        conn,
        title="加出来的会",
        start_at=f"{DAY}T15:00:00",
        kind="meeting",
    )
    again = generate_evening_plan(DAY, db_path=db_path)
    assert again.status == "confirmed"
    assert again.generated_at == locked.generated_at
    assert again.summary == locked.summary
    assert again.event_count == first.event_count
    items = _items(db_path, again.id)
    assert [item.event_id for item in items] == [first_event.id]


def test_pending_and_empty_are_overwritten(db_path, conn):
    store.create_event(conn, title="A", start_at=f"{DAY}T09:00:00", kind="task")
    first = generate_evening_plan(DAY, db_path=db_path)
    assert first.status == "pending"
    store.create_event(conn, title="B", start_at=f"{DAY}T10:00:00", kind="task")
    second = generate_evening_plan(DAY, db_path=db_path)
    assert second.status == "pending"
    assert second.event_count == 2
    assert len(_items(db_path, second.id)) == 2


def test_l1_plan_view_missing_stale_and_no_items(db_path, conn):
    missing = l1_plan_view(DAY, [], db_path=db_path)
    assert missing["status"] == "missing"
    assert missing["empty"] is False
    assert missing["stale"] is False
    assert "plan_items" not in missing

    event = store.create_event(conn, title="课", start_at=f"{DAY}T08:00:00", kind="class")
    generate_evening_plan(DAY, db_path=db_path)
    fresh = l1_plan_view(DAY, [event.id], db_path=db_path)
    assert fresh["stale"] is False
    assert "plan_items" not in fresh
    stale = l1_plan_view(DAY, [event.id, "evt_new"], db_path=db_path)
    assert stale["stale"] is True
    assert "plan_items" not in stale


# --------------------------------------------------------------------------- #
# C5 project pace blocks: free-window filling, ≤2h/cap, DDL priority
# --------------------------------------------------------------------------- #


@pytest.fixture()
def paced_registry(tmp_path, monkeypatch):
    """Isolated registry; pacing controlled per-test via set_pace()."""
    from plobi.agents.registry import AgentEntry, AgentRegistry

    registry = AgentRegistry(path=tmp_path / "projects.yaml")

    def set_pace(name, weekly_hours=None, extra=None):
        raw = {"role": "l2_project", "description": name}
        if extra:
            raw.update(extra)
        if weekly_hours is not None:
            raw["pace"] = {"weekly_hours": weekly_hours}
        registry.upsert(AgentEntry.from_dict(name, raw))

    set_pace("baseline")  # 无 pace 的对照项目
    monkeypatch.setattr("plobi.agents.registry.load_registry", lambda path=None: registry)
    registry.set_pace = set_pace  # type: ignore[attr-defined]
    return registry


def _blocks(db_path, plan_id):
    return [
        i
        for i in _items(db_path, plan_id)
        if i.evidence.get("kind") == "project_block"
    ]


# --------------------------------------------------------------------------- #
# 均分本体退役之后（裁定 149／163③，`WP-NIGHTLY-PLAN-BY-AGENT`）
#
# 这里原来有 5 条钉"分配数学"的验收：空窗挑哪段、单块 ≤2h、总量 ≤ 日均、
# DDL 临近的项目先挑空窗、"未能安排：X（缺 Y 小时）"。那段函数已整体删除，
# 把它们"迁成假 planner"等于在测试里重写一遍被否掉的数学 —— 假绿，所以删。
# 仍然活着的保证换了两层来钉：材料（`plan_materials`）+ 写入前那道闸
# （`validate_proposal`），下面这条就是其中一层；"没接线就是没有块"那一族钉在
# 同档 `test_planning_ddl_blocks.py`。
#
# ⚠️ 两条**暂时没有承接方**的规则，别当成还在生效（派工单：`Docs/tasks/plan-budget-carrier-task.md`）：
#   · 作业/项目块的当日预算（`DDL_DAILY_BUDGET_MINUTES=240`、单块 ≤2h、pace 日均）——
#     原来由均分硬算，现在既不在闸里也不在口令里；
#   · 「排不下的作业 / 欠了几小时」那句上报（`plan.evidence["unscheduled"]` 与 summary 那句
#     "未能安排：…"）——planner 只交 `(blocks, note)`，代码无从知道它放弃了什么，那两句已是死分支。
# --------------------------------------------------------------------------- #


def test_no_pace_means_no_blocks(paced_registry, db_path):
    """无 pace 配置 → 零项目块（诚实空计划不受影响）。"""
    plan = generate_evening_plan(DAY, db_path=db_path)
    assert plan.status == "empty"
    assert _blocks(db_path, plan.id) == []


def test_events_word_vocabulary_untouched(paced_registry, db_path, conn):
    """纪律：events 词表（meeting/ddl/class/task）一个不扩。"""
    from plobi.agenda.store import KINDS

    paced_registry.set_pace("simulation", 14)
    store.create_event(
        conn, title="组会", start_at=f"{DAY}T10:00:00", end_at=f"{DAY}T11:00:00", kind="meeting"
    )
    plan = generate_evening_plan(DAY, db_path=db_path)
    kinds = {i.kind for i in _items(db_path, plan.id)}
    assert kinds <= set(KINDS)
