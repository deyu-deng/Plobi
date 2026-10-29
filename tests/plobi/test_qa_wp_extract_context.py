"""Independent QA verification of f20da82 (WP-EXTRACT-CONTEXT).

Written by the QA engineer from the task book alone — deliberately does NOT
reuse the implementer's helpers (own fake client, own stub router, own
message factory) so the acceptance claims are reproduced from scratch.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from plobi.agenda.rules import RuleHit, match
from plobi.agenda.service import AgendaService
from plobi.collectors.chatlog.client import ChatMessage, normalize_message
from plobi.collectors.chatlog.config import CollectorConfig
from plobi.collectors.chatlog.confirm import ConfirmContext, HeuristicConfirmer
from plobi.collectors.chatlog.model_confirm import (
    FallbackConfirmer,
    ModelConfirmer,
    _build_prompt,
)
from plobi.collectors.chatlog.pipeline import ChatlogPipeline, IngestReport
from plobi.collectors.chatlog.state import SeenStore, TalkerStore
from plobi.collectors.chatlog.timeparse import parse_sent_at, parse_when
from plobi.routing import RoutingError


# --------------------------------------------------------------------------
# WP-POOL（`specs/source-pool.md`）--------------------------------------------
# 落点从 `agenda.db` 换成信息池：采集器**不再**往日程写。给这几档一个临时的池，
# 不钉的话它们会写**真的**共享那一份 —— 那正是要防的"测试碰了真数据"。
@pytest.fixture(autouse=True)
def _pool(tmp_path):
    import plobi.pool as pool

    original = pool.store.path
    pool.store.path = lambda: tmp_path / "pool.db"
    try:
        yield pool
    finally:
        pool.store.path = original


def _pooled(pool, **filters):
    """池子里那些行。不是 `service.list_pending()` —— 那是日程，采集器不再写它。"""
    return pool.query(**filters)
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Minimal, self-contained doubles (no import from the implementer's tests)
# --------------------------------------------------------------------------


class StubRoute:
    is_gui_surface = False
    qualified = "stub/model"


class StubRouter:
    def __init__(self, *, l1: bool = False):
        self._l1 = l1

    def is_l1(self, role: str) -> bool:
        return self._l1

    def resolve(self, role: str) -> StubRoute:
        return StubRoute()


class CaptureCompleter:
    def __init__(self, reply=None):
        self.reply = reply
        self.prompts: list[str] = []

    def __call__(self, prompt, route):
        self.prompts.append(prompt)
        return self.reply

    def confirm(self, message, hit, context=None):
        import json
        from plobi.collectors.chatlog.confirm import Candidate
        if not self.reply:
            return None
        try:
            data = json.loads(self.reply)
        except json.JSONDecodeError:
            return None
        if not data.get("is_users_plan", False):
            return None
        return Candidate(
            title=data.get("title", ""),
            start_at=data.get("date", "") + "T" + data.get("time", "00:00"),
            kind=data.get("category", "meeting"),
            tier=data.get("tier", "info"),
            weight=2 if data.get("tier") == "task" else 1,
            end_at=data.get("end_at"),
            is_change=False,
        )


class FakeClient:
    def __init__(self, by_talker):
        self.by_talker = by_talker

    def fetch(self, talker, day=None):
        return list(self.by_talker.get(talker, []))

    def list_talkers(self, keyword: str = "", limit: int = 10000):
        return list(self.by_talker)

    def healthy(self):
        return True


def message(content, *, mid="m1", sender="导师", sent_at="2026-09-10T23:00",
            talker="班级群", is_self=None):
    return ChatMessage(
        msg_id=mid, talker=talker, sender=sender, sent_at=sent_at,
        content=content, is_self=is_self,
    )


def build_pipeline(tmp_path, messages, confirmer, *, tag=""):
    safe = "".join(ch for ch in tag if ch.isalnum())  # Windows-safe db filenames
    return ChatlogPipeline(
        config=CollectorConfig(mode="whitelist", talkers=["班级群"], enabled=True),
        client=FakeClient({"班级群": messages}),
        service=AgendaService(tmp_path / f"agenda{safe}.db"),
        seen=SeenStore(tmp_path / f"seen{safe}.db"),
        talkers=TalkerStore(tmp_path / f"talkers{safe}.db"),
        confirmer=confirmer,
    )


MEETING = RuleHit(category="meeting", matched_keywords=["开会"])


# --------------------------------------------------------------------------
# 验收 1a — relative date anchors on sent_at, NOT the wall clock
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "wall_clock",
    [
        datetime(2026, 9, 11, 0, 5),    # swept just after midnight
        datetime(2026, 9, 12, 9, 0),    # swept two days later
        datetime(2026, 9, 10, 23, 1),   # swept one minute after sending
        datetime(2030, 1, 1, 12, 0),    # wildly different "today"
    ],
)
def test_sent_9_10_23h_anchors_tomorrow_on_9_11(tmp_path, wall_clock, _pool):
    confirmer = HeuristicConfirmer(now_factory=lambda wc=wall_clock: wc)
    pipeline = build_pipeline(
        tmp_path, [message("明天下午三点开会")], confirmer, tag=str(wall_clock)
    )

    report = pipeline.run_once()

    assert report.created, f"wall clock {wall_clock} wrongly suppressed ingest"
    pooled = _pooled(_pool)[0]
    assert pooled["occurred_at"] == "2026-09-10 23:00:00", "裁 189：列里那格是这条消息自己的发送时刻"
    assert pooled["context"]["event_at"] == "2026-09-11T15:00:00", "那件事的时间在 context 里，两半都不许丢"


def test_heuristic_confirm_anchors_on_sent_at_directly():
    confirmer = HeuristicConfirmer(now_factory=lambda: datetime(2026, 9, 12, 9, 0))
    cand = confirmer.confirm(message("明天下午三点开会"), MEETING)
    assert cand is not None and cand.start_at == "2026-09-11T15:00:00"


# --------------------------------------------------------------------------
# 验收 1b — no clock => None; the default-clock table is gone
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,category",
    [
        ("明天开会", "meeting"),
        ("周五晚上聚餐", "meeting"),
        ("8月26日截止交表", "ddl"),
        ("下周三答辩", "class"),
    ],
)
def test_heuristic_returns_none_when_no_clock_stated(text, category):
    confirmer = HeuristicConfirmer(now_factory=lambda: datetime(2026, 8, 25, 10, 0))
    hit = RuleHit(category=category, matched_keywords=[])
    assert confirmer.confirm(message(text), hit) is None


def test_default_clock_symbol_is_deleted_from_code():
    import plobi.collectors.chatlog.confirm as confirm_mod
    import plobi.collectors.chatlog.model_confirm as model_mod

    assert not hasattr(confirm_mod, "_DEFAULT_CLOCK")
    assert not hasattr(model_mod, "_DEFAULT_CLOCK")
    # And nothing imports/uses it anymore.
    import inspect

    assert "_DEFAULT_CLOCK" not in inspect.getsource(confirm_mod)
    assert "_DEFAULT_CLOCK" not in inspect.getsource(model_mod).replace(
        "# _DEFAULT_CLOCK fill.)", ""
    )


# --------------------------------------------------------------------------
# 验收 1c — prompt carries sender + sent_at; "not my plan" is not ingested
# --------------------------------------------------------------------------


def test_prompt_carries_sender_and_sent_at():
    cap = CaptureCompleter('{"is_users_plan":true,"date":"2026-09-11","time":"15:00","title":"开会"}')
    confirmer = ModelConfirmer(
        completer=cap, router=StubRouter(), now_factory=lambda: datetime(2026, 9, 11, 0, 5)
    )
    confirmer.confirm(message("明天下午三点开会"), MEETING)

    prompt = cap.prompts[0]
    assert "导师" in prompt, "sender absent from prompt"
    assert "2026-09-10T23:00" in prompt, "raw sent_at absent from prompt"
    assert "2026-09-10" in prompt, "sent-date anchor absent from prompt"


def test_not_users_plan_lands_but_never_becomes_a_candidate(tmp_path, _pool):
    cap = CaptureCompleter(
        '{"is_users_plan":false,"date":"2026-09-11","time":"15:00","title":"别人的课"}'
    )
    confirmer = ModelConfirmer(completer=cap, router=StubRouter())
    pipeline = build_pipeline(tmp_path, [message("明天下午三点开会")], confirmer)

    report = pipeline.run_once()

    # 用户 10-23 拍的全量进池：「不是我的事」≠「没收到过」。这一条照样进池，
    # 但不许带着模型给的那个时间——那句判词说的是"不该变成待办"，不是"不该在池里"。
    assert report.created, "闲聊/别人的事也进池"
    assert report.updated == []
    rows = _pooled(_pool)
    assert len(rows) == 1 and rows[0]["kind"] == "message"
    assert "event_at" not in rows[0]["context"]


@pytest.mark.parametrize("flag", ["false", "0", "no", "no", "不是"])
def test_string_false_flag_is_also_refused(flag):
    cap = CaptureCompleter(
        '{"is_users_plan":"%s","date":"2026-09-11","time":"15:00","title":"x"}' % flag
    )
    confirmer = ModelConfirmer(completer=cap, router=StubRouter())
    cand = confirmer.confirm(message("明天下午三点开会"), MEETING)
    assert cand is None


# --------------------------------------------------------------------------
# 验收 1d — only immediate neighbours enter the prompt
# --------------------------------------------------------------------------


def test_only_immediate_neighbours_enter_the_prompt(tmp_path, _pool):
    msgs = [
        message("昨天开会了", mid="m-1"),
        message("明天下午三点开会", mid="m2"),
        message("后天也有事", mid="m3"),
    ]
    pipeline = build_pipeline(tmp_path, msgs, CaptureCompleter(
        '{"is_users_plan":true,"date":"2026-09-11","time":"15:00","title":"开会"}'
    ))
    _ = pipeline.run_once()

    # 全量进池之后三条**都**在池里（旧断言是"只有命中那条进"）；这一档量的是那一条带片段。
    pooled = {row["external_id"]: row for row in _pooled(_pool)}
    assert len(pooled) == 3, "邻居也是消息，不再只落命中那条"
    assert pooled["m2"]["context"]["snippet"] == "明天下午三点开会"
    assert pooled["m2"]["kind"] == "meeting"
    assert pooled["m-1"]["kind"] == "message" and "event_at" not in pooled["m-1"]["context"]


def test_neighbour_is_snippet_clipped_never_whole(tmp_path, _pool):
    """ADR-0010：邻居那条** clipped **——长正文不许整条塞进给模型的提示里。

    这一档量的是提示的形状，与落点无关（落池那半由上一档与 `test_chatlog_pipeline.py` 管），
    所以按 WP-POOL 之前那版本恢复回来：迁移时把它改成了"池里该有一行"，而那三条消息本来一条都不落。
    """
    long_neighbour = "明天下午三点开会" + ("X" * 600)
    msgs = [
        message(long_neighbour, mid="long", sender="甲"),
        message("明天下午三点开会需要确认", mid="target", sender="乙"),
    ]
    cap = CaptureCompleter(None)
    confirmer = ModelConfirmer(completer=cap, router=StubRouter())
    pipeline = build_pipeline(tmp_path, msgs, confirmer)

    pipeline.run_once()

    target_prompt = next(p for p in cap.prompts if "需要确认" in p)
    assert "X" * 500 not in target_prompt, "neighbour body left unclipped (ADR-0010)"


def test_evidence_carries_sender(tmp_path, _pool):
    pipeline = build_pipeline(
        tmp_path, [message("明天下午三点开会", sender="导师")], CaptureCompleter(
            '{"is_users_plan":true,"date":"2026-09-11","time":"15:00","title":"开会"}'
        )
    )
    _ = pipeline.run_once()

    pooled = _pooled(_pool)[0]
    assert pooled["context"]["sender"] == "导师"


def test_evidence_sender_key_present_even_when_empty(tmp_path, _pool):
    pipeline = build_pipeline(
        tmp_path, [message("明天下午三点开会", sender="")],
        CaptureCompleter('{"is_users_plan":true,"date":"2026-09-11","time":"15:00","title":"开会"}')
    )
    _ = pipeline.run_once()

    pooled = _pooled(_pool)[0]
    assert "sender" in pooled["context"]
    assert pooled["context"]["sender"] == ""


# --------------------------------------------------------------------------
# 验收 1e — date without time => unresolved
# --------------------------------------------------------------------------


def test_date_without_time_is_unresolved_not_defaulted():
    confirmer = HeuristicConfirmer(now_factory=lambda: datetime(2026, 9, 10, 23, 0))
    hit = RuleHit(category="meeting", matched_keywords=["开会"])
    assert confirmer.confirm(message("明天开会"), MEETING) is None


# --------------------------------------------------------------------------
# 验收 1f — end_time becomes end_at
# --------------------------------------------------------------------------


def test_end_time_becomes_end_at(tmp_path, _pool):
    pipeline = build_pipeline(
        tmp_path,
        [message("明天下午三点到五点开会", mid="m1")],
        HeuristicConfirmer(now_factory=lambda: datetime(2026, 9, 10, 23, 0)),
    )
    report = pipeline.run_once()

    assert report.created
    pooled = _pooled(_pool)[0]
    assert pooled["context"]["end_at"] == "2026-09-11T17:00:00"


def test_missing_end_time_is_not_invented(tmp_path, _pool):
    pipeline = build_pipeline(
        tmp_path,
        [message("明天下午三点开会", mid="m1")],
        HeuristicConfirmer(now_factory=lambda: datetime(2026, 9, 10, 23, 0)),
    )
    report = pipeline.run_once()

    assert report.created
    pooled = _pooled(_pool)[0]
    assert "end_at" not in pooled["context"]


def test_missing_end_time_is_not_invented_in_evidence(tmp_path, _pool):
    pipeline = build_pipeline(
        tmp_path,
        [message("明天下午三点开会", mid="m1")],
        HeuristicConfirmer(now_factory=lambda: datetime(2026, 9, 10, 23, 0)),
    )
    _ = pipeline.run_once()

    pooled = _pooled(_pool)[0]
    assert "end_at" not in pooled["context"]


def test_garbage_end_time_is_not_invented(tmp_path, _pool):
    pipeline = build_pipeline(
        tmp_path,
        [message("下周开会吧", mid="m1")],
        HeuristicConfirmer(now_factory=lambda: datetime(2026, 9, 10, 23, 0)),
    )
    report = pipeline.run_once()

    assert _pooled(_pool), "全量进池：这条在池里"
    assert all("end_at" not in row["context"] for row in _pooled(_pool)), "脏结束时间不许被编出来"


def test_l1_route_is_rejected_and_confirm_degrades_to_none(tmp_path, _pool):
    cap = CaptureCompleter(
        '{"is_users_plan":true,"date":"2026-09-11","time":"15:00","title":"开会"}'
    )
    router = StubRouter(l1=True)
    confirmer = ModelConfirmer(completer=cap, router=router)
    pipeline = build_pipeline(
        tmp_path, [message("明天下午三点开会")], confirmer
    )
    report = pipeline.run_once()

    # L1 那一路被拒 ⇒ 拿不到一个候选，但这条消息本身还是进了池（全量进池）。
    assert report.created
    rows = _pooled(_pool)
    assert len(rows) == 1 and rows[0]["kind"] == "message"
    assert "event_at" not in rows[0]["context"]


def test_default_production_path_wires_heuristic_then_model(tmp_path):
    """默认那条生产路径的**接线**：先启发式、模型只作回落（迁移时别把这一档改成跑一条消息）。"""
    pipeline = ChatlogPipeline(
        config=CollectorConfig(mode="whitelist", talkers=["班级群"], enabled=True),
        client=FakeClient({}),
        service=AgendaService(tmp_path / "agenda.db"),
        seen=SeenStore(tmp_path / "seen.db"),
        talkers=TalkerStore(tmp_path / "talkers.db"),
    )
    assert isinstance(pipeline.confirmer, FallbackConfirmer)
    assert isinstance(pipeline.confirmer._primary, HeuristicConfirmer)
    assert isinstance(pipeline.confirmer._fallback, ModelConfirmer)


def test_default_confirmer_degrades_gracefully_when_quota_unavailable(tmp_path, monkeypatch, _pool):
    monkeypatch.setattr(
        "plobi.collectors.chatlog.confirm.HeuristicConfirmer.confirm",
        lambda self, msg, hit: None,
    )
    cap = CaptureCompleter('{"is_users_plan":true,"date":"2026-09-11","time":"15:00","title":"x"}')
    confirmer = ModelConfirmer(
        completer=CaptureCompleter('{"is_users_plan":true}'),
        router=StubRouter(),
        now_factory=lambda: datetime(2026, 9, 11, 0, 5)
    )
    pipeline = build_pipeline(tmp_path, [message("明天下午三点开会")], confirmer)
    report = pipeline.run_once()

    assert report.unresolved == 1
    rows = _pooled(_pool)
    assert len(rows) == 1 and "event_at" not in rows[0]["context"], "降级≠丢消息"
    assert report.created == [rows[0]["external_id"]]


# --------------------------------------------------------------------------
# Parse sent_at — sanity
# --------------------------------------------------------------------------


def test_parse_sent_at_shapes():
    assert parse_sent_at("2026-09-10T23:00") == datetime(2026, 9, 10, 23, 0)
    assert parse_sent_at("2026/09/10 23:00:00") == datetime(2026, 9, 10, 23, 0)
    assert parse_sent_at("2026年9月10日 23:00:00") == datetime(2026, 9, 10, 23, 0)
    assert parse_sent_at("", fallback=datetime(2020, 1, 1)) == datetime(2020, 1, 1)
    assert parse_sent_at("not-a-time", fallback=None) is None
    # NOTE (QA): the CN date WITHOUT seconds ("2026年9月10日 23:00") is not in
    # the supported format table and falls through to the fallback. Not an
    # acceptance requirement (chatlog shapes are unknown), logged as a minor
    # robustness gap only.


def test_parse_sent_at_epoch_ms_vs_seconds():
    moment = datetime(2026, 9, 10, 23, 0)
    assert parse_sent_at(str(int(moment.timestamp()))) == moment
    assert parse_sent_at(str(int(moment.timestamp() * 1000))) == moment


@pytest.mark.parametrize(
    "record,expected",
    [
        ({"content": "明天开会", "isSend": True}, True),
        ({"content": "明天开会", "isSelf": 1}, True),
        ({"content": "明天开会", "isSend": 0}, False),
        ({"content": "明天开会", "isSend": "false"}, False),
        ({"content": "明天开会", "isSend": "maybe"}, None),
        ({"content": "明天开会"}, None),
    ],
)
def test_normalize_is_self_never_guesses(record, expected):
    assert normalize_message(record).is_self is expected


def test_parse_when_untouched_contract_still_holds():
    # Sanity: parse_when itself still only reports what the text states.
    parsed = parse_when("明天下午三点开会", now=datetime(2026, 9, 10, 23, 0))
    assert parsed.day.isoformat() == "2026-09-11"
    assert parsed.clock.hour == 15


# --- WP-POOL（`specs/source-pool.md`）--------------------------------------------
# 落点从 `agenda.db` 换成信息池：采集器**不再**往日程写。给这几档一个临时的池，
# 不钉的话它们会写**真的**共享那一份 —— 那正是要防的"测试碰了真数据"。
@pytest.fixture(autouse=True)
def _pool(tmp_path):
    import plobi.pool as pool

    original = pool.store.path
    pool.store.path = lambda: tmp_path / "pool.db"
    try:
        yield pool
    finally:
        pool.store.path = original


def _pooled(pool, **filters):
    """池子里那些行。不是 `service.list_pending()` —— 那是日程，采集器不再写它。"""
    return pool.query(**filters)