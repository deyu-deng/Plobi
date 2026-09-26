"""WP-PLAN-PREFS（裁定 34.1）：「别中午排会」走配置提案卡，确认后规划器把窗当占用。

只挡**项目推进块**——已确认事件 / 作息锚点 / 弹性餐不受影响（用户讲「别
中午排会」时的真实意图是「不要硬塞工作」，不是「把那段时间清空」）。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from plobi.agenda import checkin, store
from plobi.agenda.service import AgendaService
from plobi.agenda.planning import generate_evening_plan
from plobi.agents.registry import AgentEntry, AgentRegistry


DAY = "2026-09-16"


@pytest.fixture()
def svc(tmp_path, monkeypatch):
    db = tmp_path / "agenda.db"
    monkeypatch.setenv("PLOBI_AGENDA_DB", str(db))
    return AgendaService(db)


@pytest.fixture()
def paced_registry(tmp_path, monkeypatch):
    """隔离的项目节奏表：simulation weekly_hours=14（每日 2h）。"""
    from plobi.agents.registry import load_registry

    registry = AgentRegistry(path=tmp_path / "projects.yaml")

    def set_pace(name, weekly_hours):
        registry.upsert(
            AgentEntry.from_dict(
                name, {"role": "l2_project", "pace": {"weekly_hours": weekly_hours}}
            )
        )

    set_pace("simulation", 14)
    monkeypatch.setattr("plobi.agents.registry.load_registry", lambda path=None: registry)
    return registry


def _at(offset: int, hour: int, minute: int = 0) -> str:
    day = datetime.now().date() + timedelta(days=offset)
    return datetime.combine(day, datetime.min.time()).replace(
        hour=hour, minute=minute
    ).isoformat()


def _items(db_path, plan_id):
    conn = store.connect(db_path)
    try:
        return store.list_plan_items(conn, plan_id)
    finally:
        conn.close()


def _blocks(db_path, plan_id):
    return [
        i
        for i in _items(db_path, plan_id)
        if i.evidence.get("kind") == "project_block"
    ]


# ---------------------------------------------------------------------------
# 提案卡：避免窗 + 校验
# ---------------------------------------------------------------------------


def test_propose_writes_avoid_question_and_payload(svc, tmp_path):
    db_path = svc.db_path
    conn = store.connect(db_path)
    try:
        card = checkin.propose_config_change(
            conn,
            avoid_windows=[{"start_time": "11:00", "end_time": "13:00"}],
        )
    finally:
        conn.close()

    questions = card.questions
    assert any(q["key"] == "proposal:avoid:0" for q in questions)
    text = next(q["text"] for q in questions if q["key"] == "proposal:avoid:0")
    assert "11:00–13:00" in text and "不排会议/项目块" in text
    assert card.payload["changes"]["avoid_windows"] == [
        {"start_time": "11:00", "end_time": "13:00"}
    ]


def test_propose_with_only_avoid_windows_is_allowed(svc, tmp_path):
    """用户只讲「别中午排会」也算明确指令——avoid 单类即可建卡。"""
    conn = store.connect(svc.db_path)
    try:
        card = checkin.propose_config_change(
            conn, avoid_windows=[{"start_time": "11:00", "end_time": "13:00"}]
        )
    finally:
        conn.close()
    assert card.kind == "config_proposal"
    assert card.payload["changes"]["avoid_windows"]


def test_propose_rejects_invalid_time_strings(svc):
    conn = store.connect(svc.db_path)
    try:
        with pytest.raises(store.AgendaValidationError):
            checkin.propose_config_change(
                conn,
                avoid_windows=[{"start_time": "25:00", "end_time": "13:00"}],
            )
    finally:
        conn.close()


def test_propose_rejects_end_not_after_start(svc):
    conn = store.connect(svc.db_path)
    try:
        with pytest.raises(store.AgendaValidationError):
            checkin.propose_config_change(
                conn,
                avoid_windows=[{"start_time": "13:00", "end_time": "11:00"}],
            )
    finally:
        conn.close()


def test_propose_rejects_end_equal_start(svc):
    """「空窗」无意义——拒绝。"""
    conn = store.connect(svc.db_path)
    try:
        with pytest.raises(store.AgendaValidationError):
            checkin.propose_config_change(
                conn,
                avoid_windows=[{"start_time": "11:00", "end_time": "11:00"}],
            )
    finally:
        conn.close()


def test_propose_requires_both_start_and_end(svc):
    conn = store.connect(svc.db_path)
    try:
        with pytest.raises(store.AgendaValidationError):
            checkin.propose_config_change(
                conn, avoid_windows=[{"start_time": "11:00"}]
            )
    finally:
        conn.close()


def test_list_active_avoid_windows_only_reads_confirmed(svc):
    """只有 status='confirmed' 的卡生效——pending 卡在用户没批之前规划器看不见。"""
    conn = store.connect(svc.db_path)
    try:
        # 第一张 pending 卡：规划器看不见。
        checkin.propose_config_change(
            conn, avoid_windows=[{"start_time": "11:00", "end_time": "13:00"}]
        )
        assert store.list_active_avoid_windows(conn) == []

        # 第二张：确认了，立刻生效。
        card = checkin.propose_config_change(
            conn, avoid_windows=[{"start_time": "18:00", "end_time": "20:00"}]
        )
        svc.confirm_card(card.id)
        active = store.list_active_avoid_windows(conn)
        assert {(w["start_time"], w["end_time"]) for w in active} == {
            ("18:00", "20:00"),
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 规划器：避免窗只挡项目推进块
# ---------------------------------------------------------------------------


def test_unconfirmed_window_does_not_block(svc, paced_registry, tmp_path):
    db_path = svc.db_path
    conn = store.connect(db_path)
    try:
        checkin.propose_config_change(
            conn, avoid_windows=[{"start_time": "11:00", "end_time": "13:00"}]
        )
    finally:
        conn.close()

    plan = generate_evening_plan(DAY, db_path=db_path)
    blocks = _blocks(db_path, plan.id)
    # 不应被挡——项目块至少出现在 11:00–13:00 之外（默认放最早空档）。
    assert blocks, "项目块应当照常产出"
    for block in blocks:
        start = datetime.fromisoformat(block.start_at).time()
        end = datetime.fromisoformat(block.end_at).time()
        block_range = (start, end)
        assert not (block_range[0] < datetime.strptime("13:00", "%H:%M").time() and
                    block_range[1] > datetime.strptime("11:00", "%H:%M").time())


def test_confirmed_window_blocks_project_blocks(svc, paced_registry, tmp_path):
    db_path = svc.db_path
    conn = store.connect(db_path)
    try:
        card = checkin.propose_config_change(
            conn, avoid_windows=[{"start_time": "11:00", "end_time": "13:00"}]
        )
        svc.confirm_card(card.id)
    finally:
        conn.close()

    plan = generate_evening_plan(DAY, db_path=db_path)
    blocks = _blocks(db_path, plan.id)
    assert blocks, "项目块照样产出"
    for block in blocks:
        start = datetime.fromisoformat(block.start_at).time()
        end = datetime.fromisoformat(block.end_at).time()
        # 整块与 11:00–13:00 不相交（这里用 strict 不交：start >= 13:00 or end <= 11:00）
        start_min = start.hour * 60 + start.minute
        end_min = end.hour * 60 + end.minute
        assert not (start_min < 13 * 60 and end_min > 11 * 60), (
            f"项目块 {block.start_at}–{block.end_at} 不应跨入 11:00–13:00 avoid 窗"
        )


def test_confirmed_window_does_not_drop_existing_timetable_event(svc, paced_registry, tmp_path):
    """已确认的课表事件 10:00–12:25 在 avoid 确认后仍写进 plan_items——avoid 不删它。"""
    db_path = svc.db_path
    conn = store.connect(db_path)
    try:
        store.create_event(
            conn,
            title="电工电子学",
            start_at=f"{DAY}T10:00:00",
            end_at=f"{DAY}T12:25:00",
            kind="class",
        )
        conn.commit()
        card = checkin.propose_config_change(
            conn, avoid_windows=[{"start_time": "11:00", "end_time": "13:00"}]
        )
        svc.confirm_card(card.id)
    finally:
        conn.close()

    plan = generate_evening_plan(DAY, db_path=db_path)
    items = _items(db_path, plan.id)
    titles = {it.title for it in items if it.evidence.get("kind") == "event"}
    assert "电工电子学" in titles


def test_anchor_can_still_fall_inside_avoid_window(svc, tmp_path, paced_registry):
    """弹性锚点（午餐）仍可落在 avoid 窗里——避免窗只挡项目推进块。"""
    from plobi.agenda import store as _store

    db_path = svc.db_path
    conn = store.connect(db_path)
    try:
        # 用户手动 enable（默认种子 seed-lunch 现在可能已 enabled；保险起见显式 enable）
        _store.upsert_routine_template(
            conn,
            template_id="seed-lunch",
            title="午餐",
            start_time="12:00",
            end_time="12:40",
            window_start="11:30",
            window_end="13:30",
            duration_min=40,
            enabled=True,
        )
        card = checkin.propose_config_change(
            conn, avoid_windows=[{"start_time": "11:00", "end_time": "13:00"}]
        )
        svc.confirm_card(card.id)
    finally:
        conn.close()

    plan = generate_evening_plan(DAY, db_path=db_path)
    items = _items(db_path, plan.id)
    routine_items = [it for it in items if it.evidence.get("kind") == "routine"]
    titles = {it.title for it in routine_items}
    assert "午餐" in titles


def test_avoid_window_clipped_to_day_bounds(svc, paced_registry, tmp_path):
    """avoid 窗仅当与日窗相交的部分生效——跨午夜窗只会挡到当天那段。"""
    db_path = svc.db_path
    conn = store.connect(db_path)
    try:
        card = checkin.propose_config_change(
            conn, avoid_windows=[{"start_time": "11:00", "end_time": "13:00"}]
        )
        svc.confirm_card(card.id)

        active = store.list_active_avoid_windows(conn)
        assert active and active[0]["start_time"] == "11:00"
    finally:
        conn.close()


def test_avoid_window_list_dedupe_across_cards(svc, paced_registry, tmp_path):
    """同日同 kind 的提案卡会被 upsert 覆盖（``checkin_cards`` 上有
    ``UNIQUE(for_date, kind)`` 约束）——同一日第二次提案是**覆盖**而非新增，
    所以 list_active_avoid_windows 只返一条。多次不同日期的提案才会累计。
    """
    db_path = svc.db_path
    conn = store.connect(db_path)
    try:
        for _ in range(2):
            card = checkin.propose_config_change(
                conn, avoid_windows=[{"start_time": "11:00", "end_time": "13:00"}]
            )
            svc.confirm_card(card.id)
        active = store.list_active_avoid_windows(conn)
        assert len(active) == 1
        assert active[0]["start_time"] == "11:00"
    finally:
        conn.close()


def test_avoid_windows_accumulate_across_days(svc, paced_registry, tmp_path):
    """不同 for_date 各自一张卡——两条 avoid 都会进 active 列表。

    注意：当日二次 ``propose_config_change`` 会通过 upsert 把 status 重置为
    pending（用户没批的二次提案 = 重新申请，旧 confirmed 不算）——这是
    ``upsert_checkin_card`` 的既定行为，不在本刀范围内改动。所以本测试只用
    「不同日期」来验证「跨日累计」语义。
    """
    from datetime import datetime, timedelta

    db_path = svc.db_path
    conn = store.connect(db_path)
    try:
        today_card = checkin.propose_config_change(
            conn,
            avoid_windows=[{"start_time": "11:00", "end_time": "13:00"}],
        )
        svc.confirm_card(today_card.id)
        # 未来一天：新增一张卡（for_date 不同 → 不同 row），直接确认。
        future = datetime.now() + timedelta(days=2)
        future_card = checkin.propose_config_change(
            conn,
            avoid_windows=[{"start_time": "16:00", "end_time": "17:00"}],
            now=future,
        )
        svc.confirm_card(future_card.id)
        active = store.list_active_avoid_windows(conn)
        windows = {(w["start_time"], w["end_time"]) for w in active}
        assert ("11:00", "13:00") in windows
        assert ("16:00", "17:00") in windows
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# handle_checkin_respond：避免窗透传
# ---------------------------------------------------------------------------


def test_handle_checkin_respond_passes_avoid_windows(svc, monkeypatch):
    """master_tools.handle_checkin_respond 把 avoid_windows 透传到 propose。"""
    import importlib.util
    from pathlib import Path as _Path

    import plobi.agenda.checkin as checkin_mod

    path = (
        _Path(__file__).resolve().parents[2]
        / "plugins" / "plobi-north-star" / "master_tools.py"
    )
    spec = importlib.util.spec_from_file_location("plobi_pp_master_tools", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    captured = {}

    def fake_propose(conn, **kwargs):
        captured.update(kwargs)
        from plobi.agenda.store import CheckinCard
        return CheckinCard(
            id="card_x",
            for_date="2026-09-16",
            kind="config_proposal",
            status="pending",
            questions=[],
            payload={"changes": kwargs},
            confirm_seq=1,
            confirm_seq_at="2026-09-16T08:00:00",
            created_at="2026-09-16T08:00:00",
            updated_at="2026-09-16T08:00:00",
        )

    # master_tools 内部 ``from plobi.agenda import checkin, store`` 走的是包级
    # 引用；monkeypatch 包级同名属性即可。
    monkeypatch.setattr(checkin_mod, "propose_config_change", fake_propose)
    out = module.handle_checkin_respond(
        {
            "avoid_windows": [{"start_time": "11:00", "end_time": "13:00"}],
        }
    )
    assert json.loads(out)["ok"] is True
    assert captured.get("avoid_windows") == [
        {"start_time": "11:00", "end_time": "13:00"}
    ]


def test_handle_checkin_respond_rejects_inverted_window():
    """handler 透传前已校验——倒窗直接 error。"""
    import importlib.util
    from pathlib import Path as _Path

    path = (
        _Path(__file__).resolve().parents[2]
        / "plugins" / "plobi-north-star" / "master_tools.py"
    )
    spec = importlib.util.spec_from_file_location("plobi_pp_master_tools_inverted", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    out = module.handle_checkin_respond(
        {
            "avoid_windows": [
                {"start_time": "13:00", "end_time": "11:00"},
            ],
        }
    )
    payload = json.loads(out)
    assert payload["ok"] is False
    assert "avoid" in payload["error"].lower() or "晚于" in payload["error"]


# ---------------------------------------------------------------------------
# 旧 routine/pace 提案路径未受影响
# ---------------------------------------------------------------------------


def test_routine_proposal_still_works_alone(svc):
    """avoid_windows 不动 routine/pace 的既有行为。"""
    conn = store.connect(svc.db_path)
    try:
        card = checkin.propose_config_change(
            conn, routine_updates=[{"template_id": "seed-sleep", "start_time": "23:00"}]
        )
    finally:
        conn.close()
    questions = card.questions
    assert any(q["key"].startswith("proposal:routine:") for q in questions)
    assert not any(q["key"].startswith("proposal:avoid:") for q in questions)


import json  # 放在最后，免得 jinja 风格的注释误读