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
    # 裁 175：存的不再是排程自造的 ``routine:seed-sleep``，而是真在 events 里的那一行；
    # 材料链接还在上面那格 evidence.template_id 里（那条一个字没动）。
    anchor_event = store.get_event(store.connect(db_path), item.event_id)
    assert anchor_event is not None, "计划行又指回了一个不存在的身份"
    assert anchor_event.title == item.title
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


def _mouth_round(conn, start_hm: str, end_hm: str) -> dict:
    """让秘书自己排一次（裁定 163 的生产路径），提案只给一块。零额度：注入点不碰模型。"""
    from plobi.agenda import llm_planning

    def propose(_conn, day):
        return (
            [{"title": "推进 simulation", "start_at": f"{day}T{start_hm}:00",
              "end_at": f"{day}T{end_hm}:00"}],
            "把整块留给项目，避开人的作息。",
        )

    return llm_planning.plan_and_store_day(DAY, conn=conn, proposal_fn=propose)


def test_sleep_spillover_still_blocks_the_morning(conn, db_path):
    """前一天跨午夜睡眠溢出的清晨段仍然不算地方——**保证换了主人，没换层**。

    原来靠均分自己躲开它；代码不再排块之后，能执行它的只剩「清醒窗」那道闸（边界三，
    窗口由前一段睡眠的**结束时刻**算）。所以这里钉的是：秘书把块排到 07:00 ⇒ 点名拒绝 +
    零写入。拆掉 `_waking_from_spans` 里"取前一天睡眠段"那一格，这条立刻红。
    """
    _enable(db_path, "seed-sleep")  # 23:30–07:30
    store.create_event(
        conn, title="电工电子学", start_at=f"{DAY}T08:00:00",
        end_at=f"{DAY}T09:40:00", kind="class", source="timetable",
    )

    refused = _mouth_round(conn, "07:00", "07:30")

    assert refused["ok"] is False, refused
    assert refused["written"] == 0, "过不了闸还写＝静默落一版差不多的"
    assert "清醒窗口之外" in refused["reason"], refused["reason"]

    ok = _mouth_round(conn, "10:00", "11:00")
    assert ok["ok"] is True, ok
    assert ok["written"] > 0, ok



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
