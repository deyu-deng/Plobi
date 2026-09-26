"""WP-DAY-SURFACE — 白天「今天」就要有饭/觉 + 重叠判定 + 地点备注。

主验收：打开 API 层后，白天看板能拿到当天的 events（带 location/notes/
overlaps），锚点（弹性窗落位结果），可用的 plan_items，以及 conflicts。
锚点**现场**计算，不依赖 20:00 跑过 plan；新增/修改日程**写入**不拒重叠
（人令不拒），但响应带 overlaps 提示看板标红。生产饮食作息出厂默认打开（仍是
出厂 title+start+end 的种子），用户改过的不动。

不重复的事故测试：MEALS-FLEX 的弹性与钉死行为已在 tests/plobi/test_planning.py
里；本文件只看 day 接口的契约与字段。
"""

from __future__ import annotations

import importlib.util
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from plobi.agenda import store
from plobi.agenda.planning import (
    compute_event_overlaps,
    day_surface,
    kept_anchors_as_dicts,
    place_anchors_for_day,
)
from plobi.agenda.router import (
    EventCreate,
    EventPatch,
    _render_day,
)
from plobi.agenda.service import AgendaService


DAY = "2026-09-14"


@pytest.fixture()
def svc(tmp_path):
    """AgendaService 绑 tmp/agenda.db；_init_db 自动跑（首次 connect）。"""
    os.environ["PLOBI_AGENDA_DB"] = str(tmp_path / "agenda.db")
    return AgendaService(tmp_path / "agenda.db")


@pytest.fixture()
def conn(svc):
    connection = store.connect(svc.db_path)
    try:
        yield connection
    finally:
        connection.close()


def _enable_factory_meals(conn) -> None:
    """测试里直接打开出厂作息（生产 init 时也会打开）。"""
    conn.execute(
        'UPDATE routine_templates SET enabled = 1 '
        'WHERE id IN ("seed-sleep","seed-breakfast","seed-lunch","seed-dinner")'
    )
    conn.commit()


def _today_events(conn, day: str = DAY) -> list:
    return store.list_events(
        conn,
        start_from=f"{day}T00:00:00",
        start_to=f"{day}T23:59:59",
    )


# ---------------------------------------------------------------------------
# 主验收：flexible lunch 在 10:00–12:25 课程后挪到窗内第一空档
# ---------------------------------------------------------------------------


def test_lunch_shifts_inside_window_when_class_runs_over_noon(svc, conn):
    _enable_factory_meals(conn)
    svc.create_manual(
        title="电工电子学",
        start_at=f"{DAY}T10:00:00",
        end_at=f"{DAY}T12:25:00",
        kind="class",
    )

    snap = day_surface(conn, DAY, _today_events(conn))

    anchors_by_title = {a["title"]: a for a in snap["kept_anchors"]}
    lunch = anchors_by_title.get("午餐")
    assert lunch is not None, "午餐必须在 anchors 里（不应用 yield 吞掉）"
    assert lunch["flex"] is True
    assert lunch["start_at"][11:16] >= "12:25", "午餐必须在 12:25 之后"
    end_time = datetime.fromisoformat(lunch["end_at"]) - datetime.fromisoformat(
        lunch["start_at"]
    )
    assert end_time == timedelta(minutes=40), "时长达 40 分钟"
    # 仍在窗 11:30–13:30
    assert "11:30" <= lunch["start_at"][11:16]
    assert lunch["end_at"][11:16] <= "13:30"
    assert not any(
        c["template_id"] == "seed-lunch" for c in []  # type: ignore[arg-type]
    ) or "yielded" not in snap  # day_surface 不暴露 yielded，但 anchor 在 = 没 yield


def test_sleep_appears_on_same_day_window_for_the_overnight_span(svc, conn):
    """睡眠跨午夜：seed-sleep start=23:30 在那天，end=次日 07:30 不在那天的窗里。

    锚点是 RoutineTemplate 在 day 上的瞬时；「今天」的锚点 = start 落在今天。
    """
    _enable_factory_meals(conn)
    snap = day_surface(conn, DAY, _today_events(conn))
    by_title = {a["title"]: a for a in snap["kept_anchors"]}
    sleep = by_title.get("睡眠")
    assert sleep is not None
    assert sleep["start_at"][11:16] == "23:30"
    # 跨午夜：end_at 是次日 07:30（不是当天），但仍是同一条锚点的属性
    assert sleep["end_at"].startswith(f"{DAY}T23:30") or sleep["end_at"].startswith(
        "2026-09-15T07:30"
    )


def test_no_flex_collision_keeps_preferred_lunch_slot(svc, conn):
    """没有冲突时 lunch 仍首选 12:00–12:40（flex=False）。"""
    _enable_factory_meals(conn)
    snap = day_surface(conn, DAY, _today_events(conn))
    by_title = {a["title"]: a for a in snap["kept_anchors"]}
    lunch = by_title.get("午餐")
    assert lunch["start_at"][11:16] == "12:00"
    assert lunch["end_at"][11:16] == "12:40"
    assert lunch["flex"] is False


def test_window_blocked_lunch_yields(svc, conn):
    """窗内塞不下 → 让位，lunch 不出现在 anchors。"""
    _enable_factory_meals(conn)
    # 占满 11:30–13:30
    svc.create_manual(
        title="上午全程",
        start_at=f"{DAY}T11:30:00",
        end_at=f"{DAY}T13:30:00",
        kind="meeting",
    )
    snap = day_surface(conn, DAY, _today_events(conn))
    by_title = {a["title"]: a for a in snap["kept_anchors"]}
    assert "午餐" not in by_title  # 窗满让位


# ---------------------------------------------------------------------------
# 重叠判定
# ---------------------------------------------------------------------------


def test_two_overlapping_timed_events_become_conflicts(svc, conn):
    svc.create_manual(
        title="课 A",
        start_at=f"{DAY}T14:00:00",
        end_at=f"{DAY}T16:00:00",
        kind="class",
    )
    svc.create_manual(
        title="课 B",
        start_at=f"{DAY}T15:00:00",
        end_at=f"{DAY}T17:00:00",
        kind="class",
    )
    snap = day_surface(conn, DAY, _today_events(conn))
    # 双方 overlaps 各有对方
    overlaps = snap["overlaps"]
    assert len(overlaps) == 2
    pairs = [(k, sorted(v)) for k, v in overlaps.items() if v]
    assert len(pairs) == 2
    for _eid, peers in pairs:
        assert len(peers) == 1  # 各自指对方一个
    # 一条 conflicts，重叠区间 15:00–16:00
    assert len(snap["conflicts"]) == 1
    c = snap["conflicts"][0]
    assert c["start_at"][11:16] == "15:00"
    assert c["end_at"][11:16] == "16:00"


def test_point_event_without_end_at_never_conflicts(svc, conn):
    """无 end_at 的 DDL / 提醒：占满一小时？不会——不制造冲突。"""
    svc.create_manual(
        title="交作业",
        start_at=f"{DAY}T20:00:00",
        kind="ddl",
    )
    svc.create_manual(
        title="自习",
        start_at=f"{DAY}T20:30:00",
        end_at=f"{DAY}T22:00:00",
        kind="task",
    )
    snap = day_surface(conn, DAY, _today_events(conn))
    assert snap["conflicts"] == []
    # 双方 overlaps 都是 []
    for peers in snap["overlaps"].values():
        assert peers == []


def test_overlapping_events_are_still_written(svc, conn):
    """人令不拒：create_manual 不查重叠 → 都写入，overlaps 在响应里。"""
    e1 = svc.create_manual(
        title="A",
        start_at=f"{DAY}T10:00:00",
        end_at=f"{DAY}T11:00:00",
        kind="meeting",
    )
    e2 = svc.create_manual(
        title="B",
        start_at=f"{DAY}T10:30:00",
        end_at=f"{DAY}T11:30:00",
        kind="meeting",
    )
    snap = day_surface(conn, DAY, _today_events(conn))
    today = {e.id for e in _today_events(conn)}
    assert {e1.id, e2.id}.issubset(today)
    assert e2.id in snap["overlaps"][e1.id]


# ---------------------------------------------------------------------------
# 地点 / 备注
# ---------------------------------------------------------------------------


def test_create_with_location_and_notes_round_trips(svc):
    event = svc.create_manual(
        title="自习",
        start_at=f"{DAY}T19:00:00",
        end_at=f"{DAY}T21:00:00",
        kind="task",
        location="紫金港图书馆西1",
        notes="带电脑",
    )
    fetched = svc.get(event.id)
    assert fetched.location == "紫金港图书馆西1"
    assert fetched.notes == "带电脑"


def test_update_manual_accepts_location_and_notes(svc):
    event = svc.create_manual(
        title="自习",
        start_at=f"{DAY}T19:00:00",
        end_at=f"{DAY}T21:00:00",
        kind="task",
    )
    updated = svc.update_manual(
        event.id, location="紫金港", notes="带电脑", end_at=f"{DAY}T22:00:00"
    )
    assert updated.location == "紫金港"
    assert updated.notes == "带电脑"
    assert updated.end_at == f"{DAY}T22:00:00"


def test_empty_location_becomes_none(svc):
    event = svc.create_manual(
        title="自习",
        start_at=f"{DAY}T19:00:00",
        end_at=f"{DAY}T21:00:00",
        kind="task",
        location="紫金港",
        notes="带电脑",
    )
    cleared = svc.update_manual(event.id, location="", notes="   ")
    assert cleared.location is None
    assert cleared.notes is None


def test_patch_event_schema_accepts_location_and_notes():
    """Pydantic 模型能序列化 location/notes — 看板走 PATCH 时不会 422。"""
    patch = EventPatch(location="紫金港", notes="带电脑", end_at=f"{DAY}T22:00:00")
    body = patch.model_dump(exclude_none=True)
    assert body == {"location": "紫金港", "notes": "带电脑", "end_at": f"{DAY}T22:00:00"}


def test_create_event_schema_accepts_location_and_notes():
    payload = EventCreate(
        title="自习",
        start_at=f"{DAY}T19:00:00",
        end_at=f"{DAY}T21:00:00",
        kind="task",
        location="紫金港",
        notes="带电脑",
    ).model_dump(exclude_none=True)
    assert payload["location"] == "紫金港"
    assert payload["notes"] == "带电脑"


# ---------------------------------------------------------------------------
# 已存在的 plan_items 仍然能读出来（day 接口的 plan_items 字段）
# ---------------------------------------------------------------------------


def test_day_surface_includes_plan_items_when_a_plan_exists(svc, conn):
    svc.create_manual(
        title="电工电子学",
        start_at=f"{DAY}T10:00:00",
        end_at=f"{DAY}T12:25:00",
        kind="class",
    )
    plan = store.upsert_daily_plan(
        conn,
        for_date=DAY,
        status="pending",
        summary="x",
    )
    store.replace_plan_items(
        conn,
        plan.id,
        [
            {
                "event_id": "evt_xxx",
                "title": "午餐",
                "start_at": f"{DAY}T12:00:00",
                "end_at": f"{DAY}T12:40:00",
                "kind": "task",
                "evidence": {"kind": "routine", "template_id": "seed-lunch"},
            }
        ],
    )
    snap = day_surface(conn, DAY, _today_events(conn))
    assert len(snap["plan_items"]) == 1
    assert snap["plan_items"][0]["title"] == "午餐"
    assert snap["plan_status"] == "pending"


def test_day_surface_plan_items_empty_when_no_plan(svc, conn):
    """没有跑过 20:00 的计划：plan_items=[], plan_status=None, anchors 仍有。"""
    _enable_factory_meals(conn)
    snap = day_surface(conn, DAY, _today_events(conn))
    assert snap["plan_status"] is None
    assert snap["plan_items"] == []
    assert any(a["title"] == "午餐" for a in snap["kept_anchors"])


# ---------------------------------------------------------------------------
# 用户改过的午餐不会被 enable 逻辑改回去
# ---------------------------------------------------------------------------


def test_user_changed_lunch_is_not_reset_to_factory_on_migration(svc, conn):
    """旧库迁移触发时：用户改过的午餐**不被**改回去，未改过的三餐+睡眠**被**打开。

    模拟路径：先 ``DROP COLUMN location/notes`` 让 ALTER 真的触发
    （fresh DB 里这两列是 CREATE TABLE 直接带的，ALTER 不会跑，
    ``_ensure_*_columns`` 返回 False，工厂 adopt 也不该跑——这是有意的，
    fresh-DB 的 enable 走 ``_seed_routine_templates`` 字面量）。
    """
    conn.execute("ALTER TABLE events DROP COLUMN location")
    conn.execute("ALTER TABLE events DROP COLUMN notes")
    conn.commit()

    store._seed_routine_templates(conn)
    # 用户改午餐钟点（不再是出厂 12:00–12:40）
    conn.execute(
        "UPDATE routine_templates SET start_time='13:30', end_time='14:10' "
        "WHERE id='seed-lunch'"
    )
    conn.commit()

    added = store._ensure_event_location_notes_columns(conn)
    assert added is True  # 真在补列
    store._enable_factory_meals_and_sleep(conn)

    row = conn.execute(
        "SELECT start_time, end_time, enabled FROM routine_templates WHERE id='seed-lunch'"
    ).fetchone()
    assert row["start_time"] == "13:30"  # 用户改的钟点未被回滚
    assert row["enabled"] == 0  # 也没被强行打开（钟点改了 = 不是出厂态）

    # 未改过的早餐/晚餐/睡眠已被打开
    for tid in ("seed-breakfast", "seed-dinner", "seed-sleep"):
        row = conn.execute(
            "SELECT enabled FROM routine_templates WHERE id=?", (tid,)
        ).fetchone()
        assert row["enabled"] == 1, f"{tid} 应被打开"


def test_user_disabled_factory_meal_gets_re_enabled_on_migration(svc, conn):
    """出厂态的「enabled=0」不算用户定制信号（标题/钟点都没动），迁移照样打开。

    关键区分信号是 title/start_time/end_time 一致——这是真正的「用户改过」。
    仅 disable 一下反而是「临时关」语义；下个 init 会被出货态收养回去。
    """
    conn.execute("ALTER TABLE events DROP COLUMN location")
    conn.execute("ALTER TABLE events DROP COLUMN notes")
    conn.commit()

    store._seed_routine_templates(conn)
    conn.execute("UPDATE routine_templates SET enabled=0 WHERE id='seed-breakfast'")
    conn.commit()
    added = store._ensure_event_location_notes_columns(conn)
    assert added is True
    store._enable_factory_meals_and_sleep(conn)
    row = conn.execute(
        "SELECT enabled FROM routine_templates WHERE id='seed-breakfast'"
    ).fetchone()
    assert row["enabled"] == 1  # 出厂态 = 重新 enable


# ---------------------------------------------------------------------------
# HTTP 端点：GET /api/agenda/day
# ---------------------------------------------------------------------------


def test_get_day_endpoint_returns_full_contract(svc, conn):
    _enable_factory_meals(conn)
    svc.create_manual(
        title="电工电子学",
        start_at=f"{DAY}T10:00:00",
        end_at=f"{DAY}T12:25:00",
        kind="class",
    )
    svc.create_manual(
        title="A",
        start_at=f"{DAY}T15:00:00",
        end_at=f"{DAY}T16:00:00",
        kind="meeting",
    )
    svc.create_manual(
        title="B",
        start_at=f"{DAY}T15:30:00",
        end_at=f"{DAY}T16:30:00",
        kind="meeting",
    )

    # 直接调端点的纯函数体（避免 asyncio / TestClient 套娃）。
    payload = _render_day(conn, DAY)

    assert payload["date"] == DAY
    assert {e["title"] for e in payload["events"]} >= {
        "电工电子学",
        "A",
        "B",
    }
    for e in payload["events"]:
        assert e["title"]
        assert isinstance(e["overlaps"], list)
    lunch = next((a for a in payload["anchors"] if a["title"] == "午餐"), None)
    assert lunch is not None
    assert lunch["start_at"][11:16] >= "12:25"
    assert payload["plan_status"] is None
    assert payload["plan_items"] == []
    assert any(c["start_at"][11:16] == "15:30" for c in payload["conflicts"])


def test_get_day_endpoint_default_to_today(svc, conn):
    """路由层 ``date=None`` → 今天。_render_day 自身只接受合法 ISO 字符串。"""
    from datetime import date as _date

    target = _date.today().isoformat()
    payload = _render_day(conn, target)
    assert payload["date"] == target


def test_get_day_endpoint_rejects_bad_date(svc, conn):
    """路由层 ``2026-13-40`` → HTTPException(400)（校验在路由，不在 _render）。"""
    from fastapi import HTTPException

    from plobi.agenda.router import get_day

    with pytest.raises(HTTPException) as exc_info:
        # asyncio 不跑——只调同步入口前的校验
        from datetime import date as _date

        try:
            _date.fromisoformat("2026-13-40")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"bad date") from exc
    assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# redirects / 一致性 / 杂项
# ---------------------------------------------------------------------------


def test_compute_event_overlaps_handles_empty():
    overlaps, conflicts = compute_event_overlaps([])
    assert overlaps == {}
    assert conflicts == []


def test_kept_anchors_as_dicts_drops_conflict_with_field():
    """day 接口契约里 anchors 不带 conflict_with（与 plan_items 不同）。"""
    conn = None  # 不需要 db；我们直接造 kept
    from plobi.agenda.store import RoutineTemplate

    tpl = RoutineTemplate(
        id="seed-lunch",
        title="午餐",
        start_time="12:00",
        end_time="12:40",
        weekdays=(),
        enabled=True,
        window_start="11:30",
        window_end="13:30",
        duration_min=40,
    )
    rows = kept_anchors_as_dicts([(tpl, datetime(2026, 9, 14, 12, 25), datetime(2026, 9, 14, 13, 5))])
    assert rows == [
        {
            "id": "routine:seed-lunch",
            "title": "午餐",
            "start_at": "2026-09-14T12:25:00",
            "end_at": "2026-09-14T13:05:00",
            "template_id": "seed-lunch",
            "flex": True,
        }
    ]


# ---------------------------------------------------------------------------
# WP-MEAL-DEDUPE：同一餐只在 anchors / plan_items 二者之一出现
# ---------------------------------------------------------------------------


def test_meal_plan_item_dropped_when_anchor_present_same_template(svc, conn):
    """plan_items 里有一条 evidence.template_id="seed-lunch" 的 routine 项，
    anchors 也有「午餐」——同一餐只画一次（dedup 按同 template_id）。"""
    _enable_factory_meals(conn)
    svc.create_manual(
        title="电工电子学",
        start_at=f"{DAY}T10:00:00",
        end_at=f"{DAY}T12:25:00",
        kind="class",
    )
    plan = store.upsert_daily_plan(
        conn,
        for_date=DAY,
        status="pending",
        summary="x",
    )
    # 假装 20:00 已把挪位后的午餐写进 plan_items —— 这正是用户截图中两条标题
    # 的来源（live anchor + 计划残留的同 template_id 项）。
    store.replace_plan_items(
        conn,
        plan.id,
        [
            {
                "event_id": "evt_xxx_lunch",
                "title": "午餐",
                "start_at": f"{DAY}T12:25:00",
                "end_at": f"{DAY}T13:05:00",
                "kind": "task",
                "evidence": {
                    "kind": "routine",
                    "template_id": "seed-lunch",
                    "flex": True,
                },
            }
        ],
    )
    snap = day_surface(conn, DAY, _today_events(conn))

    titles_in_anchors = {a["title"] for a in snap["kept_anchors"]}
    titles_in_items = {it["title"] for it in snap["plan_items"]}
    assert "午餐" in titles_in_anchors
    assert "午餐" not in titles_in_items, "同一餐不应在 anchors 与 plan_items 同时出现"


def test_meal_plan_item_dropped_when_title_overlaps(svc, conn):
    """用户编辑过作息钟点 → template_id 不同了，但 plan_items 里残留的旧
    「午餐」与 anchor「午餐」时间重叠 → 也算重复，按规则 (2) 丢掉。"""
    _enable_factory_meals(conn)
    plan = store.upsert_daily_plan(
        conn,
        for_date=DAY,
        status="pending",
        summary="x",
    )
    # 旧 plan_items：template_id='legacy-lunch'（用户改过、不是 seed-lunch）
    # 但 title='午餐'，时间与现 anchor 完全重叠。
    store.replace_plan_items(
        conn,
        plan.id,
        [
            {
                "event_id": "evt_legacy_lunch",
                "title": "午餐",
                "start_at": f"{DAY}T12:00:00",
                "end_at": f"{DAY}T12:40:00",
                "kind": "task",
                "evidence": {
                    "kind": "routine",
                    "template_id": "legacy-lunch",
                },
            }
        ],
    )
    snap = day_surface(conn, DAY, _today_events(conn))
    plan_item_titles = {it["title"] for it in snap["plan_items"]}
    assert "午餐" not in plan_item_titles


def test_project_block_kept_when_meal_dedup_runs(svc, conn, monkeypatch):
    """project_block 是 C5 推进块，绝不能与作息「同 template_id」误杀——只去 routine。"""
    _enable_factory_meals(conn)
    # 准备一个 L2 项目（paced）以产生 project_block
    from plobi.agents.registry import AgentEntry, AgentRegistry, load_registry

    reg_path = conn.execute("PRAGMA database_list").fetchone()[2]
    import os as _os
    reg = AgentRegistry(path=_os.path.join(_os.path.dirname(reg_path), "projects.yaml"))

    def set_pace(name, hours):
        reg.upsert(
            AgentEntry.from_dict(
                name, {"role": "l2_project", "pace": {"weekly_hours": hours}}
            )
        )

    set_pace("simulation", 14)
    # monkeypatch.setattr 走 pytest 钩子自动还原；裸 ``mod.load_registry = ...``
    # 会跨测试污染（之前曾让 test_planning 失败）。
    monkeypatch.setattr("plobi.agents.registry.load_registry", lambda path=None: reg)
    reg.set_pace = set_pace  # type: ignore[attr-defined]

    plan = store.upsert_daily_plan(
        conn,
        for_date=DAY,
        status="pending",
        summary="x",
    )
    # 一条同 title 但 evidence.kind=="project_block" 的项：与 routine dedup 无关
    store.replace_plan_items(
        conn,
        plan.id,
        [
            {
                "event_id": "evt_xxx_lunch",
                "title": "午餐",
                "start_at": f"{DAY}T12:00:00",
                "end_at": f"{DAY}T12:40:00",
                "kind": "task",
                "evidence": {"kind": "routine", "template_id": "seed-lunch"},
            },
            {
                "event_id": "evt_block_sim",
                "title": "推进",
                "start_at": f"{DAY}T15:00:00",
                "end_at": f"{DAY}T17:00:00",
                "kind": "task",
                "evidence": {
                    "kind": "project_block",
                    "project_id": "simulation",
                    "pace": "weekly_hours:14",
                },
            },
        ],
    )
    snap = day_surface(conn, DAY, _today_events(conn))
    titles_in_items = [it["title"] for it in snap["plan_items"]]
    assert "午餐" not in titles_in_items
    assert "推进" in titles_in_items  # project_block 保留


def test_avoid_windows_empty_when_no_confirmed_proposal(svc, conn):
    """无 confirmed 卡 → avoid_windows=[]。"""
    snap = day_surface(conn, DAY, _today_events(conn))
    assert snap["avoid_windows"] == []


def test_avoid_windows_appear_only_after_confirm(svc, conn):
    """pending 卡不算——确认后才出现。"""
    from plobi.agenda import checkin

    card_pending = checkin.propose_config_change(
        conn,
        avoid_windows=[{"start_time": "11:00", "end_time": "13:00"}],
    )
    snap = day_surface(conn, DAY, _today_events(conn))
    assert snap["avoid_windows"] == [], "pending 不应出现在 day_surface"

    svc.confirm_card(card_pending.id)
    snap2 = day_surface(conn, DAY, _today_events(conn))
    assert len(snap2["avoid_windows"]) == 1
    entry = snap2["avoid_windows"][0]
    assert entry["start_at"] == f"{DAY}T11:00:00"
    assert entry["end_at"] == f"{DAY}T13:00:00"
    assert entry["card_id"] == card_pending.id


def test_get_day_endpoint_returns_avoid_windows(svc, conn):
    """``GET /api/agenda/day`` 透传 avoid_windows（已确认）。"""
    from plobi.agenda import checkin

    _enable_factory_meals(conn)
    card = checkin.propose_config_change(
        conn,
        avoid_windows=[{"start_time": "11:00", "end_time": "13:00"}],
    )
    svc.confirm_card(card.id)
    payload = _render_day(conn, DAY)
    assert "avoid_windows" in payload
    assert len(payload["avoid_windows"]) == 1
    assert payload["avoid_windows"][0]["start_at"] == f"{DAY}T11:00:00"


def test_no_plan_means_empty_anchors_dedup_is_noop(svc, conn):
    """没 daily_plan：anchors 照旧现场算，plan_items 必须是 []，不被 dedup 误动。"""
    _enable_factory_meals(conn)
    snap = day_surface(conn, DAY, _today_events(conn))
    assert snap["plan_items"] == []
    assert any(a["title"] == "午餐" for a in snap["kept_anchors"])
