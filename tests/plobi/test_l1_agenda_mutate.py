"""WP-L1-AGENDA-MUTATE — L1 总秘书的第三个写意图 ``mutate_agenda``.

同一张嘴 ``plobi_secretary_ask``：加 / 改 / 取消日程直接落库。
写入绝不碰 chatlog（采集挂了也必须能写），绝不调用 ``refresh_agenda``。
用户口述 = 人令：create 落 ``source=manual`` + ``confirmed``；delete 对
confirmed 用 delete、对 pending 用 dismiss。定位不猜：0 条报错、2+ 条回
候选列表。只对准 manual/confirmed 是采集护栏（pipeline），这里必须能对准。
"""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from plobi.agenda.service import AgendaService
from plobi.agents.registry import (
    L1_SOUL_BLOCK,
    SECRETARY_ASK_INTENTS,
    run_mutate_agenda,
    run_secretary_ask,
)
from plobi.collectors.chatlog.client import ChatlogUnavailable
from plobi.collectors.chatlog.config import CollectorConfig
from plobi.collectors.chatlog.confirm import HeuristicConfirmer
from plobi.collectors.chatlog.pipeline import ChatlogPipeline
from plobi.collectors.chatlog.state import SeenStore, TalkerStore


def _load_master_tools():
    """插件目录带连字符，按文件路径加载 master_tools.py（同 test_master_tools）。"""
    path = (
        Path(__file__).resolve().parents[2]
        / "plugins" / "plobi-north-star" / "master_tools.py"
    )
    spec = importlib.util.spec_from_file_location("plobi_mutate_master_tools", path)
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


def _tomorrow_iso(hour: int = 15) -> str:
    day = datetime.now().date() + timedelta(days=1)
    return datetime.combine(day, datetime.min.time()).replace(hour=hour).isoformat()


def _day_after_iso(hour: int = 10) -> str:
    day = datetime.now().date() + timedelta(days=2)
    return datetime.combine(day, datetime.min.time()).replace(hour=hour).isoformat()


@pytest.fixture()
def secretary_home(tmp_path, monkeypatch):
    """Isolate PLOBI_HOME so spawn / registry never touch the real home."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    plobi = tmp_path / ".plobi"
    plobi.mkdir()
    monkeypatch.setenv("PLOBI_HOME", str(plobi))
    monkeypatch.setenv("PLOBI_PROJECTS_CONFIG", str(plobi / "plobi" / "projects.yaml"))
    monkeypatch.delenv("PLOBI_MODELS_CONFIG", raising=False)
    return tmp_path


@pytest.fixture()
def stub_spawn(secretary_home, monkeypatch):
    """Shrink create_profile to mkdir so tests don't clone a real profile."""
    import plobi_cli.profiles as profiles_mod

    def fake_create_profile(name, **kwargs):
        profile_dir = Path(secretary_home) / ".plobi" / "profiles" / name
        profile_dir.mkdir(parents=True, exist_ok=True)
        (profile_dir / "plobi").mkdir(exist_ok=True)
        (profile_dir / "config.yaml").write_text("model: {}\n", encoding="utf-8")
        return profile_dir

    def fake_profile_exists(name):
        if name == "default":
            return True
        return (Path(secretary_home) / ".plobi" / "profiles" / name).is_dir()

    def fake_get_profile_dir(name):
        if name == "default":
            return Path(secretary_home) / ".plobi"
        return Path(secretary_home) / ".plobi" / "profiles" / name

    monkeypatch.setattr(profiles_mod, "create_profile", fake_create_profile)
    monkeypatch.setattr(profiles_mod, "profile_exists", fake_profile_exists)
    monkeypatch.setattr(profiles_mod, "get_profile_dir", fake_get_profile_dir)
    monkeypatch.setattr("plobi_cli.profiles.create_profile", fake_create_profile)
    monkeypatch.setattr("plobi_cli.profiles.profile_exists", fake_profile_exists)
    monkeypatch.setattr("plobi_cli.profiles.get_profile_dir", fake_get_profile_dir)
    return secretary_home


def _dead_pipeline(tmp_path) -> tuple[ChatlogPipeline, AgendaService, _DeadChatlog]:
    """Pipeline wired to an always-dead chatlog client."""
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


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


def test_mutate_create_lands_manual_confirmed(stub_spawn, tmp_path):
    pipe, service, _client = _dead_pipeline(tmp_path)
    out = run_secretary_ask(
        "mutate_agenda",
        "明天下午三点约个牙医",
        pipeline=pipe,
        action="create",
        title="牙医",
        start_at=_tomorrow_iso(15),
    )
    assert out["ok"] is True
    assert out["intent"] == "mutate_agenda"
    assert out["action"] == "create"
    assert out["event"]["source"] == "manual"
    assert out["event"]["status"] == "confirmed"
    assert out["event"]["title"] == "牙医"

    rows = service.list_agenda()
    assert len(rows) == 1
    assert rows[0].source == "manual"
    assert rows[0].status == "confirmed"


def test_mutate_create_survives_dead_chatlog(stub_spawn, tmp_path):
    """写入与采集解耦：chatlog 死了 create 仍然成功，且零探测。"""
    pipe, _service, client = _dead_pipeline(tmp_path)
    out = run_secretary_ask(
        "mutate_agenda",
        "后天上午十点开会",
        pipeline=pipe,
        action="create",
        title="开会",
        start_at=_day_after_iso(10),
        kind="meeting",
    )
    assert out["ok"] is True
    assert out["event"]["kind"] == "meeting"
    assert client.health_checks == 0  # never even probed chatlog


def test_mutate_create_without_start_at_writes_nothing(stub_spawn, tmp_path):
    pipe, service, _client = _dead_pipeline(tmp_path)
    out = run_secretary_ask(
        "mutate_agenda",
        "明天下午约牙医",
        pipeline=pipe,
        action="create",
        title="牙医",
    )
    assert out["ok"] is False
    assert "start_at" in out["error"]
    assert service.list_agenda() == []  # zero writes
    dump = json.dumps([e.to_dict() for e in service.list_agenda()], ensure_ascii=False)
    assert "09:00" not in dump


def test_mutate_create_without_title_errors(stub_spawn, tmp_path):
    pipe, service, _client = _dead_pipeline(tmp_path)
    out = run_secretary_ask(
        "mutate_agenda",
        "明天下午三点记一下",
        pipeline=pipe,
        action="create",
        start_at=_tomorrow_iso(15),
    )
    assert out["ok"] is False
    assert "title" in out["error"]
    assert service.list_agenda() == []


def test_mutate_missing_action_errors_zero_writes(stub_spawn, tmp_path):
    pipe, service, _client = _dead_pipeline(tmp_path)
    out = run_secretary_ask(
        "mutate_agenda",
        "帮我把日程改一下",
        pipeline=pipe,
        title="组会",
    )
    assert out["ok"] is False
    assert "action" in out["error"]
    assert service.list_agenda() == []


# ---------------------------------------------------------------------------
# update / delete
# ---------------------------------------------------------------------------


def test_mutate_update_changes_manual_start_at(stub_spawn, tmp_path):
    pipe, service, _client = _dead_pipeline(tmp_path)
    service.create_manual(title="组会", start_at=_tomorrow_iso(9), kind="meeting")

    out = run_secretary_ask(
        "mutate_agenda",
        "把组会改到明天下午三点",
        pipeline=pipe,
        action="update",
        title="组会",
        start_at=_tomorrow_iso(15),
    )
    assert out["ok"] is True
    assert out["action"] == "update"
    assert out["event"]["start_at"] == _tomorrow_iso(15)
    assert out["event"]["source"] == "manual"
    assert out["event"]["status"] == "confirmed"


def test_mutate_delete_removes_confirmed(stub_spawn, tmp_path):
    pipe, service, _client = _dead_pipeline(tmp_path)
    service.create_manual(title="健身", start_at=_tomorrow_iso(19))

    out = run_secretary_ask(
        "mutate_agenda",
        "取消明天的健身",
        pipeline=pipe,
        action="delete",
        title="健身",
    )
    assert out["ok"] is True
    assert out["action"] == "delete"
    assert out["deleted"] is True
    assert service.list_agenda() == []


def test_mutate_delete_pending_uses_dismiss(stub_spawn, tmp_path):
    """口头取消微信候选 = 人令：pending 走 dismiss，不是留卡确认。"""
    pipe, service, _client = _dead_pipeline(tmp_path)
    service.ingest_candidate(
        title="篮球", start_at=_tomorrow_iso(20), source="wechat"
    )
    assert len(service.list_pending()) == 1

    out = run_secretary_ask(
        "mutate_agenda",
        "把篮球那条取消",
        pipeline=pipe,
        action="delete",
        title="篮球",
    )
    assert out["ok"] is True
    assert out["deleted"] is True
    assert out["resolved"]["status"] == "pending"
    assert service.list_pending() == []
    assert service.list_agenda() == []


def test_mutate_ambiguous_returns_candidates_not_guess(stub_spawn, tmp_path):
    """同日两条同名：不猜，回候选 id/title/start 列表。"""
    pipe, service, _client = _dead_pipeline(tmp_path)
    service.create_manual(title="组会", start_at=_tomorrow_iso(9))
    service.create_manual(title="组会（第二轮）", start_at=_tomorrow_iso(16))

    out = run_secretary_ask(
        "mutate_agenda",
        "把组会改到晚上",
        pipeline=pipe,
        action="update",
        title="组会",
        start_at=_tomorrow_iso(20),
    )
    assert out["ok"] is False
    assert len(out["candidates"]) == 2
    titles = {c["title"] for c in out["candidates"]}
    assert "组会" in titles and "组会（第二轮）" in titles
    starts = {c["start_at"] for c in out["candidates"]}
    assert _tomorrow_iso(9) in starts and _tomorrow_iso(16) in starts
    # 不猜：库原样
    assert len(service.list_agenda()) == 2


def test_mutate_zero_match_errors(stub_spawn, tmp_path):
    pipe, _service, _client = _dead_pipeline(tmp_path)
    out = run_secretary_ask(
        "mutate_agenda",
        "把聚餐改到六点",
        pipeline=pipe,
        action="update",
        title="聚餐",
        start_at=_tomorrow_iso(18),
    )
    assert out["ok"] is False
    assert "找不到" in out["error"]


def test_mutate_event_id_wins(stub_spawn, tmp_path):
    """event_id 存在 → 优先用它，标题关键词不参与。"""
    pipe, service, _client = _dead_pipeline(tmp_path)
    kept = service.create_manual(title="组会", start_at=_tomorrow_iso(9))
    service.create_manual(title="完全不同的事", start_at=_tomorrow_iso(11))

    out = run_secretary_ask(
        "mutate_agenda",
        "把那条改到下午两点",
        pipeline=pipe,
        action="update",
        event_id=kept.id,
        start_at=_tomorrow_iso(14),
    )
    assert out["ok"] is True
    assert out["event"]["id"] == kept.id
    assert out["event"]["start_at"] == _tomorrow_iso(14)
    titles = [e.title for e in service.list_agenda()]
    assert "完全不同的事" in titles


# ---------------------------------------------------------------------------
# routing / schema / SOUL
# ---------------------------------------------------------------------------


def test_secretary_ask_intents_include_mutate():
    assert "mutate_agenda" in SECRETARY_ASK_INTENTS


def test_bad_intent_error_mentions_mutate(stub_spawn):
    out = run_secretary_ask("kanban_dispatch", "把任务派出去")
    assert out["ok"] is False
    assert "mutate_agenda" in out["error"]


def test_refresh_path_still_dies_honestly(stub_spawn, tmp_path):
    """读路径回归护栏：refresh_agenda 死采集仍 ok=False / dead=True。"""
    pipe, _service, _client = _dead_pipeline(tmp_path)
    out = run_secretary_ask("refresh_agenda", "明天的日常安排是什么", pipeline=pipe)
    assert out["ok"] is False
    assert out["dead"] is True


def test_schema_enum_and_fields():
    schema = MT.SECRETARY_ASK_SCHEMA
    props = schema["parameters"]["properties"]
    assert "mutate_agenda" in props["intent"]["enum"]
    # WP-SECRETARY-MOUTH: cancel_matching 进了 action 枚举，schema enum 仍是 7 intent。
    assert props["action"]["enum"] == ["create", "update", "delete", "cancel_matching"]
    assert props["kind"]["enum"] == ["meeting", "task", "ddl", "class"]
    for field in ("action", "title", "start_at", "end_at", "kind", "event_id", "source"):
        assert field in props
    assert schema["parameters"]["required"] == ["intent", "user_text"]
    # WP-SECRETARY-MOUTH: schema description 写明秘书身份 + 四个 intent 例句。
    desc = schema["description"]
    assert "Plobi 总秘书" in desc
    assert "这期完了" in desc
    assert "给每个项目配 L2" in desc
    assert "吃饭睡觉还没安排" in desc


def test_soul_block_write_discipline():
    assert "mutate_agenda" in L1_SOUL_BLOCK
    assert "plobi_agenda_upsert" not in L1_SOUL_BLOCK
    # 写入口硬规则在场；旧的「口述/看板」逃生门不在场
    assert "不准用 `refresh_agenda` / `write_briefing` 冒充写入" in L1_SOUL_BLOCK
    assert "不准叫用户去看板手点" in L1_SOUL_BLOCK
    assert "缺钟点就先问一句" in L1_SOUL_BLOCK
    assert "不准编 9:00" in L1_SOUL_BLOCK


def test_handle_secretary_ask_passes_mutate_fields(monkeypatch):
    captured = {}

    def fake_run(intent, user_text, **kwargs):
        captured["intent"] = intent
        captured["user_text"] = user_text
        captured.update(kwargs)
        return {"ok": True, "intent": intent, "action": kwargs.get("action")}

    monkeypatch.setattr("plobi.agents.registry.run_secretary_ask", fake_run)
    out = MT.handle_secretary_ask(
        {
            "intent": "mutate_agenda",
            "user_text": "明天三点开会",
            "action": "create",
            "title": "开会",
            "start_at": "2026-09-12T15:00:00",
            "end_at": None,
            "kind": "meeting",
            "event_id": "evt-1",
        }
    )
    assert json.loads(out)["ok"] is True
    assert captured["intent"] == "mutate_agenda"
    assert captured["action"] == "create"
    assert captured["title"] == "开会"
    assert captured["start_at"] == "2026-09-12T15:00:00"
    assert captured["end_at"] is None
    assert captured["kind"] == "meeting"
    assert captured["event_id"] == "evt-1"


def test_handle_secretary_ask_tool_error_on_failure(monkeypatch):
    monkeypatch.setattr(
        "plobi.agents.registry.run_secretary_ask",
        lambda *a, **k: {"ok": False, "error": "create 需要 start_at", "action": "create"},
    )
    out = MT.handle_secretary_ask(
        {"intent": "mutate_agenda", "user_text": "明天约牙医", "action": "create"}
    )
    payload = json.loads(out)  # tool_error returns a JSON string
    assert payload["ok"] is False
    assert "start_at" in payload["error"]


def test_run_mutate_agenda_rejects_bad_action(stub_spawn, tmp_path):
    pipe, _service, _client = _dead_pipeline(tmp_path)
    out = run_mutate_agenda("看着办", action="upsert", pipeline=pipe)
    assert out["ok"] is False
    assert "action" in out["error"]


def test_run_mutate_agenda_rejects_bad_kind(stub_spawn, tmp_path):
    pipe, _service, _client = _dead_pipeline(tmp_path)
    out = run_mutate_agenda(
        "记个东西",
        action="create",
        title="杂事",
        start_at=_tomorrow_iso(10),
        kind="holiday",
        pipeline=pipe,
    )
    assert out["ok"] is False
    assert "kind" in out["error"]
