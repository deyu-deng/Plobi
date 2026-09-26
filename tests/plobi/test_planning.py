"""Evening planner: empty window, overlaps, evidence, confirmed lock."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from plobi.agenda import store
from plobi.agenda.planning import (
    EMPTY_PLAN_PUSH,
    EMPTY_PLAN_SUMMARY,
    enabled_anchors_for_day,
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
# C4 routine anchors: instantiation, yield-to-events, honest semantics
# --------------------------------------------------------------------------- #


def _enable(db_path, template_id):
    connection = store.connect(db_path)
    try:
        tpl = store.get_routine_template(connection, template_id)
        return store.upsert_routine_template(
            connection,
            template_id=tpl.id,
            title=tpl.title,
            start_time=tpl.start_time,
            end_time=tpl.end_time,
            weekdays=list(tpl.weekdays),
            enabled=True,
        )
    finally:
        connection.close()


def test_seeded_templates_exist_but_disabled(conn):
    seeds = {t.id: t for t in store.list_routine_templates(conn)}
    assert {"seed-sleep", "seed-breakfast", "seed-lunch", "seed-dinner"} <= set(seeds)
    assert all(not t.enabled for t in seeds.values())
    assert all(t.kind == store.ROUTINE_KIND for t in seeds.values())


def test_disabled_templates_mean_no_anchors(db_path):
    plan = generate_evening_plan(DAY, db_path=db_path)
    assert plan.status == "empty"
    assert plan.summary == EMPTY_PLAN_SUMMARY
    assert _items(db_path, plan.id) == []


def test_anchor_only_plan_is_pending_not_empty(db_path):
    """验收 3：空日程 + 启用锚点 → 计划不再"空"，只含锚点。"""
    _enable(db_path, "seed-sleep")
    plan = generate_evening_plan(DAY, db_path=db_path)
    assert plan.status == "pending"
    assert plan.confirm_seq is not None
    items = _items(db_path, plan.id)
    assert len(items) == 1
    item = items[0]
    assert item.evidence == {"kind": "routine", "template_id": "seed-sleep"}
    assert item.start_at == f"{DAY}T23:30:00"
    assert item.end_at == "2026-09-08T07:30:00"  # 跨午夜
    assert item.kind == "task"  # events 词表不扩
    assert item.event_id == "routine:seed-sleep"
    assert "作息模板安排了1项锚点" in plan.summary
    body = format_evening_plan_text(plan)
    assert EMPTY_PLAN_PUSH not in body  # 计划不再"空"


def test_anchor_coexists_with_event_sorted_by_time(db_path, conn):
    """验收 1（锚点半边）：event 项 + 锚点项共存，evidence 正确。"""
    store.create_event(
        conn, title="组会", start_at=f"{DAY}T10:00:00",
        end_at=f"{DAY}T11:00:00", kind="meeting",
    )
    _enable(db_path, "seed-lunch")
    plan = generate_evening_plan(DAY, db_path=db_path)
    items = _items(db_path, plan.id)
    assert len(items) == 2
    assert {i.evidence["kind"] for i in items} == {"event", "routine"}
    assert items[0].start_at < items[1].start_at
    assert plan.conflict_count == 0
    assert plan.evidence["anchor_count"] == 1


def test_anchor_yields_to_event_and_is_counted(db_path, conn):
    store.create_event(
        conn, title="晚饭局", start_at=f"{DAY}T18:00:00",
        end_at=f"{DAY}T20:00:00", kind="meeting",
    )
    _enable(db_path, "seed-dinner")  # 18:30-19:10 → 全程落在饭局内
    plan = generate_evening_plan(DAY, db_path=db_path)
    items = _items(db_path, plan.id)
    assert [i.evidence["kind"] for i in items] == ["event"]  # 锚点不写入
    assert plan.conflict_count == 1  # 让位计入计划头
    assert plan.evidence["yielded_anchors"][0]["template_id"] == "seed-dinner"
    assert "让位" in plan.summary


def test_weekday_filter_matches_day_of_week(conn):
    store.upsert_routine_template(
        conn, template_id="wd-only", title="仅周一",
        start_time="09:00", end_time="09:30", weekdays=[0], enabled=True,
    )
    monday = enabled_anchors_for_day(conn, DAY)  # 2026-09-07 是周一
    assert [t.id for t, _, _ in monday] == ["wd-only"]
    assert enabled_anchors_for_day(conn, "2026-09-08") == []  # 周二


def test_no_template_no_anchor(conn):
    """诚实底线：没有任何启用模板时，规划绝不凭空生成锚点。"""
    store.delete_routine_template(conn, "seed-sleep")
    store.delete_routine_template(conn, "seed-breakfast")
    store.delete_routine_template(conn, "seed-lunch")
    store.delete_routine_template(conn, "seed-dinner")
    plan = generate_evening_plan(DAY, db_path=conn.execute("PRAGMA database_list").fetchone()[2])
    assert plan.status == "empty"


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


def _durations(blocks):
    return [
        (
            datetime.fromisoformat(b.end_at) - datetime.fromisoformat(b.start_at)
        ).total_seconds()
        / 60.0
        for b in blocks
    ]


def test_pace_block_fills_free_window(paced_registry, db_path):
    """验收 2：weekly_hours=6 → 空窗出现推进块，每块 ≤2h，总量 ≤ 日均。"""
    paced_registry.set_pace("simulation", 6)
    plan = generate_evening_plan(DAY, db_path=db_path)
    blocks = _blocks(db_path, plan.id)
    assert len(blocks) == 1
    block = blocks[0]
    assert block.evidence["kind"] == "project_block"
    assert block.evidence["project_id"] == "simulation"
    assert block.evidence["pace"] == "weekly_hours:6"
    assert block.kind == "task"  # 词表不扩
    durations = _durations(blocks)
    assert all(d <= 120 for d in durations)
    assert sum(durations) <= 6 / 7 * 60 + 1e-6  # 不超日均
    assert plan.status == "pending"


def test_block_cap_two_hours(paced_registry, db_path):
    """weekly_hours=14 → 日均恰好 2h → 单块 120min 封顶。"""
    paced_registry.set_pace("simulation", 14)
    plan = generate_evening_plan(DAY, db_path=db_path)
    blocks = _blocks(db_path, plan.id)
    assert len(blocks) == 1
    assert _durations(blocks) == [120.0]


def test_unscheduled_reported_in_header(paced_registry, db_path, conn):
    """空窗不够 → 计划头"未能安排：<项目>（缺 X 小时）"。"""
    paced_registry.set_pace("simulation", 14)  # 预算 2h
    store.create_event(
        conn, title="上午", start_at=f"{DAY}T00:00:00", end_at=f"{DAY}T11:50:00", kind="task"
    )
    store.create_event(
        conn, title="下午", start_at=f"{DAY}T12:30:00", end_at=f"{DAY}T23:59:59", kind="task"
    )
    plan = generate_evening_plan(DAY, db_path=db_path)
    blocks = _blocks(db_path, plan.id)
    assert len(blocks) == 1
    assert _durations(blocks) == [40.0]  # 11:50-12:30 的 40 分钟空窗
    assert plan.evidence["unscheduled"] == [
        {"project": "simulation", "missing_hours": 1.3}
    ]
    assert "未能安排：simulation（缺1.3小时）" in plan.summary


def test_ddl_nearest_project_picks_first(paced_registry, db_path, conn):
    """DDL 临近优先：有临近 DDL 的项目先挑空窗。"""
    paced_registry.set_pace("zeta", 7)
    paced_registry.set_pace("alpha", 7)
    store.create_event(
        conn, title="zeta 评审 DDL", start_at=f"{DAY}T18:00:00", kind="ddl"
    )
    plan = generate_evening_plan(DAY, db_path=db_path)
    blocks = _blocks(db_path, plan.id)
    assert len(blocks) == 2
    assert blocks[0].evidence["project_id"] == "zeta"  # 先挑
    assert blocks[1].evidence["project_id"] == "alpha"
    assert blocks[0].start_at < blocks[1].start_at  # 不重叠


def test_sleep_spillover_blocks_morning(paced_registry, db_path):
    """前一天跨午夜锚点溢出的清晨段不算空窗。"""
    paced_registry.set_pace("simulation", 14)
    _enable(db_path, "seed-sleep")  # 23:30-07:30
    plan = generate_evening_plan(DAY, db_path=db_path)
    blocks = _blocks(db_path, plan.id)
    assert blocks, "应当有推进块"
    first_start = datetime.fromisoformat(min(b.start_at for b in blocks))
    assert first_start.hour >= 7 and first_start.minute >= 30  # 07:30 之后
    # 且不与睡眠锚点（23:30 起）重叠
    for b in blocks:
        assert b.end_at <= f"{DAY}T23:30:00" or b.start_at >= f"{DAY}T23:30:00"


def test_no_pace_means_no_blocks(paced_registry, db_path):
    """无 pace 配置 → 零项目块（诚实空计划不受影响）。"""
    plan = generate_evening_plan(DAY, db_path=db_path)
    assert plan.status == "empty"
    assert _blocks(db_path, plan.id) == []


def test_events_word_vocabulary_untouched(paced_registry, db_path, conn):
    """纪律：events 词表（meeting/ddl/class/task）一个不扩。"""
    from plobi.agenda.store import KINDS

    paced_registry.set_pace("simulation", 14)
    _enable(db_path, "seed-lunch")
    store.create_event(
        conn, title="组会", start_at=f"{DAY}T10:00:00", end_at=f"{DAY}T11:00:00", kind="meeting"
    )
    plan = generate_evening_plan(DAY, db_path=db_path)
    kinds = {i.kind for i in _items(db_path, plan.id)}
    assert kinds <= set(KINDS)
