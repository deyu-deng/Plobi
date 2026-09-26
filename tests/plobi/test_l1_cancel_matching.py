"""WP-SECRETARY-MOUTH — L1 总秘书的批量取消嘴。

「这期完了 / 这门课后面都不用去了」通过 ``mutate_agenda`` 的
``action=cancel_matching`` 一次说完：只删 ``source=timetable`` 的 future
confirmed 行，past / manual / wechat 一行不碰。禁止循环 ``delete`` 冒充。
返回 ``{ok, action, source, from_date, cancelled, sample[≤8]}``，N=0 也
是 ok，禁止编数量。
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from plobi.agenda.service import AgendaService
from plobi.agents.registry import run_secretary_ask
from plobi.collectors.chatlog.client import ChatlogUnavailable
from plobi.collectors.chatlog.config import CollectorConfig
from plobi.collectors.chatlog.confirm import HeuristicConfirmer
from plobi.collectors.chatlog.pipeline import ChatlogPipeline
from plobi.collectors.chatlog.state import SeenStore, TalkerStore


def _load_master_tools():
    path = (
        Path(__file__).resolve().parents[2]
        / "plugins" / "plobi-north-star" / "master_tools.py"
    )
    spec = importlib.util.spec_from_file_location("plobi_mouth_master_tools", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MT = _load_master_tools()


class _DeadChatlog:
    def __init__(self):
        self.health_checks = 0

    def healthy(self) -> bool:
        self.health_checks += 1
        return False

    def fetch(self, talker, day=None):
        raise ChatlogUnavailable("boom")

    def list_talkers(self, keyword="", limit=10000):
        raise ChatlogUnavailable("boom")


def _at(offset_days: int, hour: int, minute: int = 0) -> str:
    day = datetime.now().date() + timedelta(days=offset_days)
    return datetime.combine(day, datetime.min.time()).replace(
        hour=hour, minute=minute
    ).isoformat()


@pytest.fixture()
def secretary_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    hermes = tmp_path / ".hermes"
    hermes.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes))
    monkeypatch.setenv(
        "PLOBI_PROJECTS_CONFIG", str(hermes / "plobi" / "projects.yaml")
    )
    monkeypatch.delenv("PLOBI_MODELS_CONFIG", raising=False)
    return tmp_path


@pytest.fixture()
def stub_spawn(secretary_home, monkeypatch):
    import hermes_cli.profiles as profiles_mod

    def fake_create_profile(name, **kwargs):
        profile_dir = Path(secretary_home) / ".hermes" / "profiles" / name
        profile_dir.mkdir(parents=True, exist_ok=True)
        (profile_dir / "plobi").mkdir(exist_ok=True)
        (profile_dir / "config.yaml").write_text("model: {}\n", encoding="utf-8")
        return profile_dir

    def fake_profile_exists(name):
        if name == "default":
            return True
        return (Path(secretary_home) / ".hermes" / "profiles" / name).is_dir()

    def fake_get_profile_dir(name):
        if name == "default":
            return Path(secretary_home) / ".hermes"
        return Path(secretary_home) / ".hermes" / "profiles" / name

    monkeypatch.setattr(profiles_mod, "create_profile", fake_create_profile)
    monkeypatch.setattr(profiles_mod, "profile_exists", fake_profile_exists)
    monkeypatch.setattr(profiles_mod, "get_profile_dir", fake_get_profile_dir)
    return secretary_home


def _pipeline(tmp_path) -> tuple[ChatlogPipeline, AgendaService, _DeadChatlog]:
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


def _list_all(service) -> list:
    """2-year window including cancelled — for verifying the seed and post-state."""
    from datetime import datetime as _dt, timedelta

    start = _dt.combine(_dt.now().date() - timedelta(days=365), _dt.min.time())
    end = _dt.combine(_dt.now().date() + timedelta(days=365), _dt.max.time())
    return service.list_agenda(start, end, include_cancelled=True)


def _seed_timetable(pipe, service):
    """3 future + 1 past + 1 manual + 1 wechat."""
    future = [_at(1, 8), _at(3, 10), _at(7, 14)]
    past = [_at(-1, 9)]
    for idx, start in enumerate(future + past):
        service.create_from_source(
            title=f"高数课 周{idx}",
            start_at=start,
            source="timetable",
            evidence={"location": "紫金港西2-415", "calendar_name": "浙大课程表"},
        )
    service.create_manual(title="牙医复诊", start_at=_at(2, 15), kind="task")
    service.ingest_candidate(
        title="篮球改期", start_at=_at(4, 20), source="wechat"
    )


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------


def test_schema_lists_cancel_matching_in_action_enum():
    schema = MT.SECRETARY_ASK_SCHEMA
    enum = schema["parameters"]["properties"]["action"]["enum"]
    assert "cancel_matching" in enum
    # 「这期完了」必须进 description。
    assert "这期完了" in schema["description"]


# ---------------------------------------------------------------------------
# cancel_matching — happy path
# ---------------------------------------------------------------------------


def test_cancel_matching_drops_only_future_timetable_rows(stub_spawn, tmp_path):
    pipe, service, _client = _pipeline(tmp_path)
    _seed_timetable(pipe, service)
    before = _list_all(service)
    assert len(before) == 6  # 3 future + 1 past timetable + 1 manual + 1 pending wechat

    out = run_secretary_ask(
        "mutate_agenda",
        "这期完了",
        pipeline=pipe,
        action="cancel_matching",
        source="timetable",
        start_at=datetime.now().date().isoformat(),
    )

    assert out["ok"] is True
    assert out["action"] == "cancel_matching"
    assert out["source"] == "timetable"
    assert out["cancelled"] == 3
    assert len(out["sample"]) == 3
    for row in out["sample"]:
        assert set(row) == {"id", "title", "start_at"}

    rows = _list_all(service)
    # 3 future timetable 走 delete（status="cancelled"）；1 past timetable 仍在；
    # 1 manual + 1 wechat pending 仍原样。
    confirmed_timetable = [r for r in rows if r.source == "timetable" and r.status == "confirmed"]
    assert len(confirmed_timetable) == 1, (
        "past timetable should survive; the 3 future ones must be gone"
    )
    assert confirmed_timetable[0].start_at == _at(-1, 9)
    # manual 与 wechat pending 都不在删除范围。
    manual_alive = [r for r in rows if r.source == "manual" and r.status == "confirmed"]
    assert len(manual_alive) == 1
    pending_wechat = [r for r in rows if r.source == "wechat" and r.status == "pending"]
    assert len(pending_wechat) == 1


def test_cancel_matching_with_title_substring_only_cancels_that_course(stub_spawn, tmp_path):
    pipe, service, _client = _pipeline(tmp_path)
    _seed_timetable(pipe, service)
    # 6 条已存在，3 条 future 是「高数课 周0/1/2」，1 条 past 是「高数课 周3」。

    out = run_secretary_ask(
        "mutate_agenda",
        "这期完了",
        pipeline=pipe,
        action="cancel_matching",
        source="timetable",
        title="高数课",
        start_at=datetime.now().date().isoformat(),
    )

    assert out["ok"] is True
    # 3 future 「高数课」全删；past 的「高数课」保留；manual/wechat 不动。
    assert out["cancelled"] == 3
    rows = _list_all(service)
    timetable_alive = [
        r for r in rows
        if r.source == "timetable" and r.status == "confirmed"
    ]
    assert len(timetable_alive) == 1
    assert timetable_alive[0].start_at == _at(-1, 9)


def test_cancel_matching_with_zero_matches_is_ok_with_zero(stub_spawn, tmp_path):
    pipe, service, _client = _pipeline(tmp_path)
    _seed_timetable(pipe, service)

    out = run_secretary_ask(
        "mutate_agenda",
        "这门课后面都不用去了",
        pipeline=pipe,
        action="cancel_matching",
        source="timetable",
        title="根本不存在的课名 xyz",
        start_at=datetime.now().date().isoformat(),
    )

    assert out["ok"] is True
    assert out["cancelled"] == 0
    assert out["sample"] == []
    # 不删任何东西。
    rows = _list_all(service)
    assert len(rows) == 6


def test_cancel_matching_default_from_date_is_today(stub_spawn, tmp_path):
    pipe, service, _client = _pipeline(tmp_path)
    _seed_timetable(pipe, service)

    out = run_secretary_ask(
        "mutate_agenda",
        "这期完了",
        pipeline=pipe,
        action="cancel_matching",
        source="timetable",
    )

    assert out["ok"] is True
    assert out["cancelled"] == 3
    assert out["from_date"] == datetime.now().date().isoformat()


def test_cancel_matching_keeps_health_checks_zero(stub_spawn, tmp_path):
    """chatlog 死了也照删；不探测采集。"""
    pipe, service, client = _pipeline(tmp_path)
    _seed_timetable(pipe, service)

    out = run_secretary_ask(
        "mutate_agenda",
        "这期完了",
        pipeline=pipe,
        action="cancel_matching",
        source="timetable",
    )

    assert out["ok"] is True
    assert client.health_checks == 0  # never probed chatlog


# ---------------------------------------------------------------------------
# cancel_matching — guardrails
# ---------------------------------------------------------------------------


def test_cancel_matching_rejects_unknown_source_with_zero_writes(stub_spawn, tmp_path):
    pipe, service, _client = _pipeline(tmp_path)
    _seed_timetable(pipe, service)
    before = len(service.list_agenda())

    out = run_secretary_ask(
        "mutate_agenda",
        "这期完了",
        pipeline=pipe,
        action="cancel_matching",
        source="wechat",  # not wired this round
    )

    assert out["ok"] is False
    assert "timetable" in out["error"]
    assert out.get("cancelled", 0) == 0
    assert len(service.list_agenda()) == before  # zero writes


def test_cancel_matching_requires_source(stub_spawn, tmp_path):
    pipe, service, _client = _pipeline(tmp_path)
    _seed_timetable(pipe, service)

    out = run_secretary_ask(
        "mutate_agenda",
        "这期完了",
        pipeline=pipe,
        action="cancel_matching",
    )

    assert out["ok"] is False
    assert "source" in out["error"]


def test_cancel_matching_rejects_bad_start_at_date(stub_spawn, tmp_path):
    pipe, service, _client = _pipeline(tmp_path)
    _seed_timetable(pipe, service)

    out = run_secretary_ask(
        "mutate_agenda",
        "这期完了",
        pipeline=pipe,
        action="cancel_matching",
        source="timetable",
        start_at="not-a-date",
    )

    assert out["ok"] is False
    assert "日期" in out["error"] or "date" in out["error"]


# ---------------------------------------------------------------------------
# decide_pending — user_text needle (long paste with no title)
# ---------------------------------------------------------------------------


def test_decide_pending_user_text_needle_hits_by_snippet(stub_spawn, tmp_path):
    """证据原文含「楼补办」，user_text 是贴进来的长粘贴，无 title → 命中。"""
    pipe, service, _client = _pipeline(tmp_path)
    service.ingest_candidate(
        title="楼补办",
        start_at=_at(0, 14),
        source="wechat",
        evidence={"snippet": "楼补办，如不能按时报到…@所有人"},
    )

    out = run_secretary_ask(
        "decide_pending",
        "确认 楼补办，如不能按时报到…@所有人",
        decision="confirm",
        pipeline=pipe,
    )

    assert out["ok"] is True, f"expected ok, got {out}"
    assert out["intent"] == "decide_pending"
    assert out["decision"] == "confirm"
    assert out["event"]["title"] == "楼补办"
    assert out["event"]["status"] == "confirmed"


def test_decide_pending_user_text_needle_ambiguous_returns_candidates(stub_spawn, tmp_path):
    """两条都含同一子串 → candidates，零写入。"""
    pipe, service, _client = _pipeline(tmp_path)
    service.ingest_candidate(
        title="楼补办",
        start_at=_at(0, 14),
        source="wechat",
        evidence={"snippet": "楼补办 A 楼"},
    )
    service.ingest_candidate(
        title="楼补办",
        start_at=_at(2, 10),
        source="wechat",
        evidence={"snippet": "楼补办 B 楼"},
    )

    out = run_secretary_ask(
        "decide_pending",
        "楼补办",
        decision="dismiss",
        pipeline=pipe,
    )

    assert out["ok"] is False
    assert "多条" in out["error"] or "不猜" in out["error"]
    assert "candidates" in out and len(out["candidates"]) == 2
    # 零写入：两条都仍是 pending。
    pending = [r for r in _list_all(service) if r.status == "pending"]
    assert len(pending) == 2


def test_decide_pending_user_text_needle_zero_match_errors(stub_spawn, tmp_path):
    pipe, service, _client = _pipeline(tmp_path)
    service.ingest_candidate(
        title="完全无关",
        start_at=_at(0, 14),
        source="wechat",
        evidence={"snippet": "完全不搭边的内容"},
    )

    out = run_secretary_ask(
        "decide_pending",
        "楼补办，如不能按时报到",
        decision="confirm",
        pipeline=pipe,
    )

    assert out["ok"] is False
    assert "没有匹配" in out["error"] or "没有待确认" in out["error"]


def test_decide_pending_user_text_needle_strips_at_all_marker(stub_spawn, tmp_path):
    """``@所有人`` 不会让 0 匹配变有匹配。"""
    pipe, service, _client = _pipeline(tmp_path)
    service.ingest_candidate(
        title="楼补办",
        start_at=_at(0, 14),
        source="wechat",
        evidence={"snippet": "楼补办通知"},
    )

    # user_text 唯一能与 snippet 相交的子串是「楼补办通知」，@所有人 没起作用
    out = run_secretary_ask(
        "decide_pending",
        "@所有人 楼补办通知",
        decision="confirm",
        pipeline=pipe,
    )
    assert out["ok"] is True
    assert out["event"]["title"] == "楼补办"
