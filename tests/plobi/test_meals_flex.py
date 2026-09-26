"""WP-MEALS-FLEX — 三餐弹性窗：有课就挪、没空才让位（后端2）。

sleep 无窗 = 钉死（重叠即让位，不滑）。三餐有窗 + duration：① 首选段空
放首选；② 否则窗内第一段够长的空档；③ 窗内都塞不下 → 让位并计入冲突。
窗与时长是人授权的事实，绝不由模型补。

种子自带窗但 ``enabled=0``（与 C4 旧行为完全一致），用户启用时才走弹性。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from plobi.agenda import store
from plobi.agenda.planning import generate_evening_plan

DAY = "2026-09-07"


@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "agenda.db"


@pytest.fixture()
def conn(db_path):
    connection = store.connect(db_path)
    try:
        yield connection
    finally:
        connection.close()


def _items(db_path, plan_id):
    connection = store.connect(db_path)
    try:
        return store.list_plan_items(connection, plan_id)
    finally:
        connection.close()


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


def _flex(db_path, template_id, *, enabled=True, **window):
    """Helper: upsert a flexible anchor with the given window fields."""
    connection = store.connect(db_path)
    try:
        tpl = store.get_routine_template(connection, template_id)
        kwargs = dict(
            template_id=tpl.id,
            title=tpl.title,
            start_time=tpl.start_time,
            end_time=tpl.end_time,
            weekdays=list(tpl.weekdays),
            enabled=enabled,
        )
        kwargs.update(window)
        return store.upsert_routine_template(connection, **kwargs)
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# 主验收 + 边界
# ---------------------------------------------------------------------------


def test_flex_lunch_slides_inside_window_when_class_overlaps(db_path, conn):
    """10:00–12:25「电工电子学」占首选 12:00–12:40 → 午餐在窗内挪位。"""
    store.create_event(
        conn, title="电工电子学", start_at=f"{DAY}T10:00:00",
        end_at=f"{DAY}T12:25:00", kind="class",
    )
    _flex(db_path, "seed-lunch", window_start="11:30", window_end="13:30", duration_min=40)
    plan = generate_evening_plan(DAY, db_path=db_path)
    items = _items(db_path, plan.id)
    lunches = [i for i in items if i.evidence.get("template_id") == "seed-lunch"]
    assert len(lunches) == 1, "弹性锚点必须落位，不能让位"
    lunch = lunches[0]
    start = datetime.fromisoformat(lunch.start_at)
    end = datetime.fromisoformat(lunch.end_at)
    assert (end - start) == timedelta(minutes=40)
    assert start >= datetime.fromisoformat(f"{DAY}T12:25:00")
    assert start < datetime.fromisoformat(f"{DAY}T13:30:00")
    assert lunch.evidence.get("flex") is True
    # summary 写了一句事实，用落下的钟点，不编原因
    assert any(t in plan.summary for t in ("12:25", "12:30", "12:40"))


def test_flex_lunch_stays_at_preferred_when_class_does_not_collide(db_path, conn):
    """首选不冲突：午餐仍 12:00–12:40。"""
    _flex(db_path, "seed-lunch", window_start="11:30", window_end="13:30", duration_min=40)
    plan = generate_evening_plan(DAY, db_path=db_path)
    items = _items(db_path, plan.id)
    lunch = next(i for i in items if i.evidence.get("template_id") == "seed-lunch")
    assert lunch.start_at == f"{DAY}T12:00:00"
    assert lunch.end_at == f"{DAY}T12:40:00"
    assert "flex" not in lunch.evidence  # 首选命中就不打 flex 标签


def test_flex_yields_when_window_is_too_busy(db_path, conn):
    """窗内塞不下 → 让位（与原 anchor_yields_to_event_and_is_counted 等价）。"""
    store.create_event(
        conn, title="晚宴", start_at=f"{DAY}T18:00:00",
        end_at=f"{DAY}T20:00:00", kind="meeting",
    )
    _flex(db_path, "seed-dinner", window_start="17:30", window_end="20:00", duration_min=40)
    plan = generate_evening_plan(DAY, db_path=db_path)
    items = _items(db_path, plan.id)
    assert not any(i.evidence.get("template_id") == "seed-dinner" for i in items)
    assert plan.evidence["yielded_anchors"][0]["template_id"] == "seed-dinner"
    assert "让位" in plan.summary


def test_flex_seeds_carry_windows_but_stay_disabled_by_default(conn):
    """出厂 seed：窗登记，但 enabled=0（与 C4 旧行为完全一致）。"""
    seeds = {t.id: t for t in store.list_routine_templates(conn)}
    for tid in ("seed-breakfast", "seed-lunch", "seed-dinner"):
        tpl = seeds[tid]
        assert not tpl.enabled
        assert tpl.window_start and tpl.window_end and tpl.duration_min
    sleep = seeds["seed-sleep"]
    assert not sleep.window_start and not sleep.window_end
    assert sleep.duration_min is None


def test_pinned_sleep_still_yields_when_overlapped(db_path, conn):
    """睡眠钉死，重叠即让位，不滑（与原 C4 行为一致）。"""
    store.create_event(
        conn, title="跨夜出差", start_at=f"{DAY}T22:00:00",
        end_at="2026-09-08T10:00:00", kind="meeting",
    )
    _enable(db_path, "seed-sleep")
    plan = generate_evening_plan(DAY, db_path=db_path)
    items = _items(db_path, plan.id)
    assert not any(i.evidence.get("template_id") == "seed-sleep" for i in items)
    assert plan.evidence["yielded_anchors"][0]["template_id"] == "seed-sleep"


# ---------------------------------------------------------------------------
# store 校验
# ---------------------------------------------------------------------------


def test_upsert_routine_template_rejects_partial_window(conn):
    """三个窗字段必须一起给（要么全有，要么全空）。"""
    with pytest.raises(store.AgendaValidationError):
        store.upsert_routine_template(
            conn, template_id="bad-1", title="半套窗",
            start_time="12:00", end_time="12:30",
            window_start="11:30", window_end="13:30",
        )


def test_upsert_routine_template_rejects_zero_duration(conn):
    with pytest.raises(store.AgendaValidationError):
        store.upsert_routine_template(
            conn, template_id="bad-2", title="零时长",
            start_time="12:00", end_time="12:30",
            window_start="11:30", window_end="13:30", duration_min=0,
        )


def test_upsert_routine_template_rejects_window_end_before_start(conn):
    with pytest.raises(store.AgendaValidationError):
        store.upsert_routine_template(
            conn, template_id="bad-3", title="倒窗",
            start_time="12:00", end_time="12:30",
            window_start="13:30", window_end="11:30", duration_min=40,
        )


def test_upsert_routine_template_without_window_kwargs_keeps_existing(conn):
    """旧调用方（看板/回访）不传窗参数 → 行为不变。"""
    store.upsert_routine_template(
        conn, template_id="rt-x", title="旧风",
        start_time="09:00", end_time="09:30", enabled=True,
    )
    store.upsert_routine_template(
        conn, template_id="rt-x", title="旧风",
        start_time="09:00", end_time="09:30", enabled=True,
    )
    tpl = store.get_routine_template(conn, "rt-x")
    assert tpl.window_start == "" and tpl.window_end == "" and tpl.duration_min is None


def test_factory_migration_keeps_user_edited_clock(conn):
    """用户改过钟点 → adopt 识别为「非出厂态」，窗字段不动、enabled 不强开。"""
    # 用户编辑：把 seed-lunch 改成 11:30/12:00、关停、设上自定义窗。
    store.upsert_routine_template(
        conn, template_id="seed-lunch", title="午餐",
        start_time="11:30", end_time="12:00",  # 与出厂首选 12:00/12:40 不同
        enabled=False,
        window_start="11:00", window_end="14:00", duration_min=30,
    )
    from plobi.agenda.store import _adopt_factory_windows
    _adopt_factory_windows(conn)
    tpl = store.get_routine_template(conn, "seed-lunch")
    # 钟点 / 自定义窗 / enabled 全部保持原样（adopt 不接管「非出厂态」行）。
    assert tpl.start_time == "11:30"
    assert tpl.end_time == "12:00"
    assert tpl.window_start == "11:00"
    assert tpl.window_end == "14:00"
    assert tpl.duration_min == 30
    assert tpl.enabled is False
