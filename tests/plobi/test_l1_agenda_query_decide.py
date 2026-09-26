"""WP-SEC-VOCAB — L1 总秘书的读意图 ``query_agenda`` 与决定意图 ``decide_pending``.

同一张嘴 ``plobi_secretary_ask``：问今天/明天/本周/某天/待确认 → 读**共享日程库**
（零采集、零 spawn、零 N3；采集死了照样答）；口头确认/忽略某条 → 等价看板点
Confirm/Dismiss（dismiss 对有 prev 的改动回滚、对新建的直接删）。定位复用
``resolve_manual_target`` 并收窄到 pending：0 条 / 2+ 条一律不写库、不猜。
"""

from __future__ import annotations

import importlib.util
import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from plobi.agenda import store
from plobi.agenda.service import AgendaService, ManualTargetMissing
from plobi.agents.registry import (
    L1_SOUL_BLOCK,
    SECRETARY_ASK_INTENTS,
    run_decide_pending,
    run_query_agenda,
    run_secretary_ask,
)
from plobi.collectors.chatlog.client import ChatlogUnavailable
from plobi.collectors.chatlog.config import CollectorConfig
from plobi.collectors.chatlog.confirm import HeuristicConfirmer
from plobi.collectors.chatlog.pipeline import ChatlogPipeline
from plobi.collectors.chatlog.state import SeenStore, TalkerStore


def _load_master_tools():
    """插件目录带连字符，按文件路径加载 master_tools.py（同 test_l1_agenda_mutate）。"""
    path = (
        Path(__file__).resolve().parents[2]
        / "plugins" / "plobi-north-star" / "master_tools.py"
    )
    spec = importlib.util.spec_from_file_location("plobi_query_master_tools", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MT = _load_master_tools()


class _DeadChatlog:
    """Always-unhealthy stand-in; counts health probes to prove zero touches."""

    def __init__(self):
        self.health_checks = 0

    def healthy(self) -> bool:
        self.health_checks += 1
        return False

    def fetch(self, talker: str, day=None):
        raise ChatlogUnavailable("boom")

    def list_talkers(self, keyword: str = "", limit: int = 10000) -> list[str]:
        raise ChatlogUnavailable("boom")


def _at(offset: int, hour: int, minute: int = 0) -> str:
    """today+offset 的本地 ISO 钟点（测试自己造事实，不借产品钟点）。"""
    day = datetime.now().date() + timedelta(days=offset)
    return datetime.combine(day, datetime.min.time()).replace(
        hour=hour, minute=minute
    ).isoformat()


def _day(offset: int) -> str:
    return (datetime.now().date() + timedelta(days=offset)).isoformat()


@pytest.fixture()
def secretary_home(tmp_path, monkeypatch):
    """Isolate PLOBI_HOME so a stray read/spawn can never touch the real home."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    plobi = tmp_path / ".plobi"
    plobi.mkdir()
    monkeypatch.setenv("PLOBI_HOME", str(plobi))
    monkeypatch.setenv("PLOBI_PROJECTS_CONFIG", str(plobi / "plobi" / "projects.yaml"))
    monkeypatch.delenv("PLOBI_MODELS_CONFIG", raising=False)
    return tmp_path


def _dead_pipeline(tmp_path) -> tuple[ChatlogPipeline, AgendaService, _DeadChatlog]:
    service = AgendaService(tmp_path / "agenda.db")
    client = _DeadChatlog()
    pipe = ChatlogPipeline(
        config=CollectorConfig(talkers=["班级群"], enabled=True),
        client=client,
        service=service,
        seen=SeenStore(tmp_path / "seen.db"),
        talkers=TalkerStore(tmp_path / "talkers.db"),
        confirmer=HeuristicConfirmer(),
    )
    return pipe, service, client


def _actions(service: AgendaService) -> list[dict]:
    conn = store.connect(service.db_path)
    try:
        return store.list_actions(conn)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# query_agenda
# ---------------------------------------------------------------------------


def test_query_today_lists_confirmed_only_in_time_order(secretary_home, tmp_path):
    pipe, service, _client = _dead_pipeline(tmp_path)
    service.create_manual(title="组会", start_at=_at(0, 16), kind="meeting")
    service.create_manual(title="高数课", start_at=_at(0, 8), end_at=_at(0, 9, 40), kind="class")
    service.ingest_candidate(title="篮球", start_at=_at(0, 20), source="wechat")

    out = run_secretary_ask("query_agenda", "今天有什么", range="today", pipeline=pipe)

    assert out["ok"] is True
    assert out["intent"] == "query_agenda"
    assert out["range"] == "today"
    assert out["from"] == _day(0) and out["to"] == _day(0)
    assert [e["title"] for e in out["events"]] == ["高数课", "组会"]
    assert [p["title"] for p in out["pending"]] == ["篮球"]
    row = out["events"][0]
    assert set(row) == {"id", "title", "start_at", "end_at", "kind", "status", "source"}
    assert row["status"] == "confirmed"
    assert row["end_at"] == _at(0, 9, 40)


def test_query_never_invents_an_end_time(secretary_home, tmp_path):
    pipe, service, _client = _dead_pipeline(tmp_path)
    service.create_manual(title="组会", start_at=_at(0, 10), kind="meeting")

    out = run_secretary_ask("query_agenda", "今天有什么", range="today", pipeline=pipe)

    assert out["events"][0]["end_at"] is None
    assert "11:00" not in json.dumps(out, ensure_ascii=False)


def test_query_tomorrow_window_excludes_today(secretary_home, tmp_path):
    pipe, service, _client = _dead_pipeline(tmp_path)
    service.create_manual(title="今天的事", start_at=_at(0, 10))
    service.create_manual(title="明天的事", start_at=_at(1, 10))

    out = run_query_agenda("明天几点有课", range="tomorrow", pipeline=pipe)

    assert out["from"] == _day(1) and out["to"] == _day(1)
    assert [e["title"] for e in out["events"]] == ["明天的事"]


def test_query_week_covers_seven_days(secretary_home, tmp_path):
    pipe, service, _client = _dead_pipeline(tmp_path)
    service.create_manual(title="第六天", start_at=_at(6, 10))
    service.create_manual(title="第八天", start_at=_at(7, 10))

    out = run_query_agenda("这周安排", range="week", pipeline=pipe)

    assert out["from"] == _day(0) and out["to"] == _day(6)
    assert [e["title"] for e in out["events"]] == ["第六天"]


def test_query_date_uses_the_explicit_day(secretary_home, tmp_path):
    pipe, service, _client = _dead_pipeline(tmp_path)
    service.create_manual(title="远会", start_at=_at(3, 14))

    out = run_query_agenda("9 月 17 号有什么", range="date", date=_day(3), pipeline=pipe)

    assert out["from"] == _day(3) and out["to"] == _day(3)
    assert [e["title"] for e in out["events"]] == ["远会"]


def test_query_date_without_date_errors_without_writes(secretary_home, tmp_path):
    pipe, service, _client = _dead_pipeline(tmp_path)

    out = run_secretary_ask("query_agenda", "那天有什么", range="date", pipeline=pipe)

    assert out["ok"] is False
    assert "date" in out["error"]
    assert service.list_agenda() == []


def test_query_rejects_unknown_range(secretary_home, tmp_path):
    pipe, _service, _client = _dead_pipeline(tmp_path)
    out = run_secretary_ask("query_agenda", "看看日程", range="month", pipeline=pipe)
    assert out["ok"] is False
    assert "range" in out["error"]


def test_query_pending_is_not_bounded_by_date(secretary_home, tmp_path):
    pipe, service, _client = _dead_pipeline(tmp_path)
    service.ingest_candidate(title="下个月的体检", start_at=_at(30, 9), source="wechat")
    service.ingest_candidate(title="今天的篮球", start_at=_at(0, 20), source="wechat")
    service.create_manual(title="已确认的课", start_at=_at(0, 8), kind="class")

    out = run_query_agenda("有什么待确认的", range="pending", pipeline=pipe)

    assert out["events"] == []
    assert [p["title"] for p in out["pending"]] == ["今天的篮球", "下个月的体检"]
    assert all(p["status"] == "pending" for p in out["pending"])
    assert out["from"] is None and out["to"] is None


def test_query_empty_store_is_ok_and_empty(secretary_home, tmp_path):
    pipe, _service, _client = _dead_pipeline(tmp_path)
    out = run_query_agenda("今天有什么", range="today", pipeline=pipe)
    assert out["ok"] is True
    assert out["events"] == [] and out["pending"] == []


def test_query_answers_with_a_dead_collector(secretary_home, tmp_path):
    """读共享库，不打采集：chatlog 死了照样答，且零探测。"""
    pipe, service, client = _dead_pipeline(tmp_path)
    service.create_manual(title="高数课", start_at=_at(0, 8), kind="class")

    out = run_secretary_ask("query_agenda", "今天还剩什么", range="today", pipeline=pipe)

    assert out["ok"] is True
    assert [e["title"] for e in out["events"]] == ["高数课"]
    assert client.health_checks == 0


def test_query_does_not_spawn_l2_or_write_n3(secretary_home, tmp_path):
    pipe, _service, _client = _dead_pipeline(tmp_path)
    out = run_query_agenda("今天有什么", range="today", pipeline=pipe)

    assert "sessionId" not in out
    assert not (Path(secretary_home) / ".plobi" / "profiles").exists(), "查询不派工、不 spawn"


# ---------------------------------------------------------------------------
# decide_pending
# ---------------------------------------------------------------------------


def test_decide_confirm_by_event_id(secretary_home, tmp_path):
    pipe, service, _client = _dead_pipeline(tmp_path)
    pending = service.ingest_candidate(title="篮球", start_at=_at(1, 20), source="wechat").event

    out = run_secretary_ask(
        "decide_pending", "确认那个篮球", decision="confirm", event_id=pending.id, pipeline=pipe
    )

    assert out["ok"] is True
    assert out["intent"] == "decide_pending"
    assert out["decision"] == "confirm"
    assert out["event"]["id"] == pending.id
    assert out["event"]["status"] == "confirmed"
    assert service.list_pending() == []
    assert service.get(pending.id).status == "confirmed"


def test_decide_dismiss_rolls_back_a_change(secretary_home, tmp_path):
    """有 prev_value 的采集改动：忽略 = 回滚原值，不是删掉那条日程。"""
    pipe, service, _client = _dead_pipeline(tmp_path)
    base = service.create_manual(title="高数课", start_at=_at(1, 8), end_at=_at(1, 9, 40), kind="class")
    service.ingest_candidate(
        title="高数课",
        start_at=_at(1, 10),
        end_at=_at(1, 11, 40),
        kind="class",
        source="wechat",
        target_event_id=base.id,
    )

    out = run_secretary_ask(
        "decide_pending", "把高数课那个改动忽略掉", decision="dismiss", title="高数课", pipeline=pipe
    )

    assert out["ok"] is True
    assert out["event"]["id"] == base.id
    assert out["event"]["status"] == "confirmed"
    assert out["event"]["start_at"] == _at(1, 8)
    assert "deleted" not in out
    assert service.list_pending() == []
    assert [e.start_at for e in service.list_agenda()] == [_at(1, 8)]


def test_decide_dismiss_deletes_a_new_pending(secretary_home, tmp_path):
    pipe, service, _client = _dead_pipeline(tmp_path)
    pending = service.ingest_candidate(title="篮球", start_at=_at(1, 20), source="wechat").event

    out = run_secretary_ask(
        "decide_pending", "忽略篮球", decision="dismiss", title="篮球", pipeline=pipe
    )

    assert out["ok"] is True
    assert out["deleted"] is True
    assert out["event"]["status"] == "dismissed"
    assert out["event"]["id"] == pending.id
    assert service.list_pending() == []
    assert service.list_agenda() == []


def test_decide_zero_match_writes_nothing(secretary_home, tmp_path):
    pipe, service, _client = _dead_pipeline(tmp_path)
    service.ingest_candidate(title="篮球", start_at=_at(1, 20), source="wechat")

    out = run_secretary_ask(
        "decide_pending", "确认那个聚餐", decision="confirm", title="聚餐", pipeline=pipe
    )

    assert out["ok"] is False
    assert "没有匹配的待确认项" in out["error"]
    assert len(service.list_pending()) == 1
    assert _actions(service) == []


def test_decide_ambiguous_returns_candidates_and_writes_nothing(secretary_home, tmp_path):
    """跨天两条同名：不猜，回候选（id/title/start_at），库原样。"""
    pipe, service, _client = _dead_pipeline(tmp_path)
    service.ingest_candidate(title="组会", start_at=_at(1, 10), source="wechat")
    service.ingest_candidate(title="组会", start_at=_at(3, 10), source="wechat")

    out = run_secretary_ask(
        "decide_pending", "确认那个组会", decision="confirm", title="组会", pipeline=pipe
    )

    assert out["ok"] is False
    assert "多条匹配" in out["error"]
    assert {c["start_at"] for c in out["candidates"]} == {_at(1, 10), _at(3, 10)}
    assert all({"id", "title", "start_at"} <= set(c) for c in out["candidates"])
    assert len(service.list_pending()) == 2
    assert _actions(service) == []


def test_decide_refuses_a_confirmed_target(secretary_home, tmp_path):
    pipe, service, _client = _dead_pipeline(tmp_path)
    confirmed = service.create_manual(title="组会", start_at=_at(1, 10), kind="meeting")

    out = run_secretary_ask(
        "decide_pending", "确认那个组会", decision="confirm", event_id=confirmed.id, pipeline=pipe
    )

    assert out["ok"] is False
    assert out["error"] == "这条不是待确认项"
    assert service.get(confirmed.id).status == "confirmed"


def test_decide_needs_a_target(secretary_home, tmp_path):
    # WP-SECRETARY-MOUTH: 当 user_text 本身就是针，「确认那个」会走全文匹配；
    # store 里没待确认就回「没有待确认项」。语义没变（仍然 ok=false），
    # 错误措辞跟随 user_text-only needle 新路径。完全没给针的旧护栏由
    # ``run_secretary_ask`` 入口的「user_text is required」守住（不在本任务范围）。
    pipe, _service, _client = _dead_pipeline(tmp_path)
    out = run_secretary_ask("decide_pending", "确认那个", decision="confirm", pipeline=pipe)
    assert out["ok"] is False
    assert "没有待确认项" in out["error"] or "没有匹配" in out["error"]


def test_decide_rejects_a_bad_decision(secretary_home, tmp_path):
    pipe, _service, _client = _dead_pipeline(tmp_path)
    out = run_secretary_ask(
        "decide_pending", "确认那个", decision="approve", title="组会", pipeline=pipe
    )
    assert out["ok"] is False
    assert "decision" in out["error"]


def test_decide_logs_the_human_decision(secretary_home, tmp_path):
    pipe, service, _client = _dead_pipeline(tmp_path)
    pending = service.ingest_candidate(title="篮球", start_at=_at(1, 20), source="wechat").event

    run_secretary_ask(
        "decide_pending", "确认篮球", decision="confirm", event_id=pending.id, pipeline=pipe
    )

    rows = _actions(service)
    assert len(rows) == 1
    assert rows[0]["event_id"] == pending.id
    assert rows[0]["action"] == "confirm"
    assert rows[0]["event_source"] == "wechat"


def test_decide_survives_a_dead_collector(secretary_home, tmp_path):
    pipe, service, client = _dead_pipeline(tmp_path)
    pending = service.ingest_candidate(title="篮球", start_at=_at(1, 20), source="wechat").event

    out = run_secretary_ask(
        "decide_pending", "确认篮球", decision="confirm", title="篮球", pipeline=pipe
    )

    assert out["ok"] is True
    assert client.health_checks == 0
    assert service.get(pending.id).status == "confirmed"


def test_decide_can_be_scoped_to_a_day(secretary_home, tmp_path):
    pipe, service, _client = _dead_pipeline(tmp_path)
    service.ingest_candidate(title="组会", start_at=_at(1, 10), source="wechat")
    wanted = service.ingest_candidate(title="组会", start_at=_at(3, 10), source="wechat").event

    out = run_decide_pending(
        "确认第三天的组会", decision="confirm", title="组会", date=_day(3), pipeline=pipe
    )

    assert out["ok"] is True
    assert out["event"]["id"] == wanted.id


# ---------------------------------------------------------------------------
# resolve_manual_target(statuses=...)
# ---------------------------------------------------------------------------


def test_statuses_none_keeps_searching_every_row(secretary_home, tmp_path):
    service = AgendaService(tmp_path / "agenda.db")
    confirmed = service.create_manual(title="组会", start_at=_at(1, 10), kind="meeting")
    found = service.resolve_manual_target(title="组会")
    assert found.id == confirmed.id


def test_statuses_narrows_title_search_to_pending(secretary_home, tmp_path):
    service = AgendaService(tmp_path / "agenda.db")
    service.create_manual(title="组会", start_at=_at(1, 10), kind="meeting")
    pending = service.ingest_candidate(title="组会", start_at=_at(1, 16), source="wechat").event

    found = service.resolve_manual_target(title="组会", statuses=("pending",))
    assert found.id == pending.id

    # 待确认里没有这条 → Missing（已确认的同名行被挡掉，不会被误确认）
    service.create_manual(title="高数课", start_at=_at(1, 8), kind="class")
    with pytest.raises(ManualTargetMissing):
        service.resolve_manual_target(title="高数课", statuses=("pending",))


def test_statuses_narrows_the_event_id_path_too(secretary_home, tmp_path):
    service = AgendaService(tmp_path / "agenda.db")
    confirmed = service.create_manual(title="组会", start_at=_at(1, 10), kind="meeting")

    with pytest.raises(ManualTargetMissing):
        service.resolve_manual_target(event_id=confirmed.id, statuses=("pending",))

    assert service.resolve_manual_target(event_id=confirmed.id).id == confirmed.id


# ---------------------------------------------------------------------------
# schema / SOUL
# ---------------------------------------------------------------------------


def test_secretary_ask_intents_match_the_schema_enum():
    props = MT.SECRETARY_ASK_SCHEMA["parameters"]["properties"]
    enum = props["intent"]["enum"]
    # 7 intents: refresh_agenda, write_briefing, mutate_agenda, query_agenda,
    # decide_pending (SEC-VOCAB) + project_status, plan_day (PROJECT-PORTFOLIO).
    assert len(enum) == 7
    assert set(enum) == set(SECRETARY_ASK_INTENTS)
    assert {"query_agenda", "decide_pending"} <= set(SECRETARY_ASK_INTENTS)
    assert props["range"]["enum"] == ["today", "tomorrow", "week", "date", "pending"]
    assert props["decision"]["enum"] == ["confirm", "dismiss"]
    assert "date" in props
    assert MT.SECRETARY_ASK_SCHEMA["parameters"]["required"] == ["intent", "user_text"]


def test_handle_secretary_ask_passes_read_fields(monkeypatch):
    captured = {}

    def fake_run(intent, user_text, **kwargs):
        captured["intent"] = intent
        captured.update(kwargs)
        return {"ok": True, "intent": intent, "range": kwargs.get("range")}

    monkeypatch.setattr("plobi.agents.registry.run_secretary_ask", fake_run)
    out = MT.handle_secretary_ask(
        {
            "intent": "query_agenda",
            "user_text": "今天有什么",
            "range": "today",
            "date": "2026-09-15",
            "decision": "confirm",
        }
    )
    assert json.loads(out)["ok"] is True
    assert captured["intent"] == "query_agenda"
    assert captured["range"] == "today"
    assert captured["date"] == "2026-09-15"
    assert captured["decision"] == "confirm"


def test_bad_intent_error_lists_every_intent():
    out = run_secretary_ask("kanban_dispatch", "把任务派出去")
    assert out["ok"] is False
    for intent in SECRETARY_ASK_INTENTS:
        assert intent in out["error"]


def test_soul_block_carries_the_read_and_decide_rules():
    assert "query_agenda" in L1_SOUL_BLOCK
    assert "decide_pending" in L1_SOUL_BLOCK
    assert "**不是** `refresh_agenda`" in L1_SOUL_BLOCK
    assert "不替用户挑" in L1_SOUL_BLOCK
    assert "别把没批的当成已安排" in L1_SOUL_BLOCK
    # 旧硬规则一字不动
    assert "不准用 `refresh_agenda` / `write_briefing` 冒充写入" in L1_SOUL_BLOCK
    # 写意图仍然指向 mutate
    assert "mutate_agenda" in L1_SOUL_BLOCK


# ---------------------------------------------------------------------------
# WP-QUERY-DAY：query_agenda 带上 anchors + plan_items（range=today/tomorrow/date）
# ---------------------------------------------------------------------------


def _enable_factory_meals(service: AgendaService) -> None:
    """Test helper: open the factory meals + sleep so day_surface 会有锚点。"""
    from plobi.agenda import store

    conn = store.connect(service.db_path)
    try:
        conn.execute(
            "UPDATE routine_templates SET enabled = 1 "
            'WHERE id IN ("seed-sleep","seed-breakfast","seed-lunch","seed-dinner")'
        )
        conn.commit()
    finally:
        conn.close()


def test_query_today_carries_anchors_and_plan_items_keys(secretary_home, tmp_path):
    """range=today 必须带 anchors + plan_items 两个键——这样 L1 的嘴能念作息。"""
    pipe, service, _client = _dead_pipeline(tmp_path)
    _enable_factory_meals(service)

    out = run_secretary_ask("query_agenda", "今天有什么", range="today", pipeline=pipe)

    assert out["ok"] is True
    assert "anchors" in out
    assert "plan_items" in out
    # 旧字段语义一字不改。
    assert "events" in out
    assert "pending" in out
    assert out["range"] == "today"


def test_query_today_anchors_match_day_surface_for_lunch(secretary_home, tmp_path):
    """午饭锚点的 start_at 必须与 day_surface(同一 events 列表) 的输出一致——
    这是裁定 34.0「嘴和轴用同一份纯函数」的硬约束。"""
    pipe, service, _client = _dead_pipeline(tmp_path)
    _enable_factory_meals(service)

    today = datetime.now().date().isoformat()
    # 不放课：让弹性午餐的首选段（12:00–12:40）不被让位。
    out = run_secretary_ask("query_agenda", "今天有什么", range="today", pipeline=pipe)

    lunch_anchor = [a for a in out["anchors"] if a["title"] == "午餐"]
    assert len(lunch_anchor) == 1, f"应恰好一条午餐锚点；实得 {out['anchors']}"
    lunch = lunch_anchor[0]
    # 起止时间的日期部分 = today；时间 12:00 → 12:40（首选段未让位）。
    assert lunch["start_at"].startswith(today)
    assert "T12:00" in lunch["start_at"]
    assert "T12:40" in lunch["end_at"]
    assert "flex" in lunch
    # 没让位 = flex=False。
    assert lunch["flex"] is False


def test_query_empty_store_anchors_and_plan_items_are_empty_lists(secretary_home, tmp_path):
    """空 agenda：anchors / plan_items 都该是 [] 而非 None——前端能直接 .length。"""
    pipe, service, _client = _dead_pipeline(tmp_path)
    # 不 enable 出厂作息、也不建任何日程。

    out = run_secretary_ask("query_agenda", "今天有什么", range="today", pipeline=pipe)

    assert out["ok"] is True
    assert out["anchors"] == []
    assert out["plan_items"] == []
    assert out["events"] == []  # 旧字段语义保留。


def test_query_tomorrow_also_carries_anchors(secretary_home, tmp_path):
    """range=tomorrow 同 range=today：单日轴才有 anchors/plan_items。"""
    pipe, service, _client = _dead_pipeline(tmp_path)
    _enable_factory_meals(service)

    out = run_secretary_ask(
        "query_agenda", "明天有什么", range="tomorrow", pipeline=pipe
    )

    assert "anchors" in out
    assert "plan_items" in out


def test_query_date_with_explicit_day_carries_anchors(secretary_home, tmp_path):
    """range=date 带具体日期：单日轴 = 也要带 anchors/plan_items。"""
    pipe, service, _client = _dead_pipeline(tmp_path)
    _enable_factory_meals(service)

    target = (datetime.now().date()).isoformat()
    out = run_secretary_ask(
        "query_agenda", "这天有什么", range="date", date=target, pipeline=pipe
    )

    assert "anchors" in out
    assert "plan_items" in out
    lunch_anchor = [a for a in out["anchors"] if a["title"] == "午餐"]
    assert len(lunch_anchor) == 1
    assert lunch_anchor[0]["start_at"].startswith(target)


def test_query_week_does_not_carry_anchors(secretary_home, tmp_path):
    """range=week 是 7 天窗口——不是「一天轴」——不准塞 anchors/plan_items。"""
    pipe, service, _client = _dead_pipeline(tmp_path)
    _enable_factory_meals(service)

    out = run_secretary_ask("query_agenda", "本周", range="week", pipeline=pipe)

    assert out["ok"] is True
    # week 走的是 7 天窗口——不应有 anchors / plan_items。
    assert "anchors" not in out
    assert "plan_items" not in out


def test_query_pending_does_not_carry_anchors(secretary_home, tmp_path):
    """range=pending 没「那一天」——不准塞 anchors/plan_items；旧字段保留。"""
    pipe, service, _client = _dead_pipeline(tmp_path)
    _enable_factory_meals(service)

    out = run_secretary_ask("query_agenda", "有什么待确认", range="pending", pipeline=pipe)

    assert out["ok"] is True
    assert "anchors" not in out
    assert "plan_items" not in out
    assert "events" in out
    assert "pending" in out


def test_query_old_event_and_pending_fields_unchanged(secretary_home, tmp_path):
    """旧 events / pending / from / to / range / user_text / ok 字段一字不改。"""
    pipe, service, _client = _dead_pipeline(tmp_path)
    today = datetime.now().date().isoformat()
    service.create_manual(title="高数课", start_at=f"{today}T08:00:00", end_at=f"{today}T09:40:00", kind="class")
    service.ingest_candidate(title="篮球", start_at=f"{today}T20:00:00", source="wechat")

    out = run_secretary_ask("query_agenda", "今天", range="today", pipeline=pipe)

    # 旧字段的语义保留。
    assert out["ok"] is True
    assert out["range"] == "today"
    assert out["from"] == today
    assert out["to"] == today
    assert out["user_text"] == "今天"
    assert {e["title"] for e in out["events"]} == {"高数课"}
    assert {p["title"] for p in out["pending"]} == {"篮球"}
    # 每条 event / pending 的字段保持旧契约。
    assert set(out["events"][0]) == {"id", "title", "start_at", "end_at", "kind", "status", "source"}


def test_soul_block_tells_l1_to_read_anchors_and_use_checkin():
    """SOUL「查今天/明天/某天要把饭和觉一并念出来」段必须存在 + 含 plobi_checkin_respond 指引。"""
    from plobi.agents.registry import L1_SOUL_BLOCK

    assert "查今天/明天/某天要把饭和觉一并念出来" in L1_SOUL_BLOCK
    assert "anchors" in L1_SOUL_BLOCK
    # 不准用 mutate_agenda 写午饭——必须走 plobi_checkin_respond。
    assert "plobi_checkin_respond" in L1_SOUL_BLOCK
    # 写明 range=week / range=pending 不带 anchors。
    assert "week" in L1_SOUL_BLOCK
    assert "pending" in L1_SOUL_BLOCK
