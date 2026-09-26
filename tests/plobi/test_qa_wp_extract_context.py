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
        config=CollectorConfig(talkers=["班级群"], enabled=True),
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
def test_sent_9_10_23h_anchors_tomorrow_on_9_11(tmp_path, wall_clock):
    confirmer = HeuristicConfirmer(now_factory=lambda wc=wall_clock: wc)
    pipeline = build_pipeline(
        tmp_path, [message("明天下午三点开会")], confirmer, tag=str(wall_clock)
    )

    report = pipeline.run_once()

    assert report.created, f"wall clock {wall_clock} wrongly suppressed ingest"
    assert pipeline.service.list_pending()[0].start_at == "2026-09-11T15:00:00"


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


def test_not_users_plan_is_not_ingested_at_pipeline_level(tmp_path):
    cap = CaptureCompleter(
        '{"is_users_plan":false,"date":"2026-09-11","time":"15:00","title":"别人的课"}'
    )
    confirmer = ModelConfirmer(completer=cap, router=StubRouter())
    pipeline = build_pipeline(tmp_path, [message("明天下午三点开会")], confirmer)

    report = pipeline.run_once()

    assert report.created == []
    assert report.updated == []
    assert pipeline.service.list_pending() == []


@pytest.mark.parametrize("flag", ["false", "0", "no", "否", "不是"])
def test_string_false_flag_is_also_refused(flag):
    cap = CaptureCompleter(
        '{"is_users_plan":"%s","date":"2026-09-11","time":"15:00","title":"x"}' % flag
    )
    confirmer = ModelConfirmer(completer=cap, router=StubRouter())
    assert confirmer.confirm(message("明天下午三点开会"), MEETING) is None


# --------------------------------------------------------------------------
# 验收 1d — +/-1 neighbours only; the 3rd line never enters the prompt
# --------------------------------------------------------------------------


def test_only_immediate_neighbours_enter_the_prompt(tmp_path):
    markers = ["AAA", "BBB", "CCC", "DDD", "EEE"]
    msgs = [
        message(f"明天下午三点开会{m}", mid=f"m{i}", sender=f"人{i}")
        for i, m in enumerate(markers)
    ]
    cap = CaptureCompleter(None)  # reply None => nothing ingested, prompt captured
    confirmer = ModelConfirmer(completer=cap, router=StubRouter())
    pipeline = build_pipeline(tmp_path, msgs, confirmer)

    pipeline.run_once()

    # One prompt per matched message, in sweep order (index == message index).
    assert len(cap.prompts) == 5
    middle = cap.prompts[2]  # the "CCC" message
    assert "BBB" in middle and "DDD" in middle, "immediate neighbours missing"
    assert "AAA" not in middle, "2-lines-back leaked into the prompt"
    assert "EEE" not in middle, "2-lines-forward leaked into the prompt"

    first = cap.prompts[0]  # the "AAA" message, has no predecessor
    assert "BBB" in first and "上文" not in first

    last = cap.prompts[4]  # the "EEE" message, has no successor
    assert "DDD" in last and "下文" not in last


def test_neighbour_is_snippet_clipped_never_whole(tmp_path):
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


# --------------------------------------------------------------------------
# 验收 1e — evidence carries sender (and survives an empty sender)
# --------------------------------------------------------------------------


def test_evidence_carries_sender(tmp_path):
    confirmer = HeuristicConfirmer(now_factory=lambda: datetime(2026, 9, 10, 23, 0))
    pipeline = build_pipeline(tmp_path, [message("明天下午三点开会", sender="导师")], confirmer)

    pipeline.run_once()

    ev = pipeline.service.list_pending()[0].evidence
    assert ev["sender"] == "导师"
    assert ev["sent_at"] == "2026-09-10T23:00"
    assert ev["talker"] == "班级群"


def test_evidence_sender_key_present_even_when_empty(tmp_path):
    confirmer = HeuristicConfirmer(now_factory=lambda: datetime(2026, 9, 10, 23, 0))
    pipeline = build_pipeline(tmp_path, [message("明天下午三点开会", sender="")], confirmer)

    report = pipeline.run_once()

    assert report.created, "empty sender must not crash or suppress ingest"
    ev = pipeline.service.list_pending()[0].evidence
    assert "sender" in ev and ev["sender"] == ""


# --------------------------------------------------------------------------
# 反编造 / 不回退 — no fabricated clock, no invented duration
# --------------------------------------------------------------------------


def test_date_without_time_is_unresolved_not_defaulted():
    cap = CaptureCompleter('{"date":"2026-08-26","time":null,"title":"交表"}')
    confirmer = ModelConfirmer(
        completer=cap, router=StubRouter(), now_factory=lambda: datetime(2026, 8, 25, 10, 0)
    )
    hit = RuleHit(category="ddl", matched_keywords=["截止"])
    assert confirmer.confirm(message("8月26日截止交表"), hit) is None


def test_end_time_becomes_end_at():
    cap = CaptureCompleter(
        '{"is_users_plan":true,"date":"2026-08-26","time":"15:00","end_time":"16:30","title":"组会"}'
    )
    confirmer = ModelConfirmer(completer=cap, router=StubRouter())
    cand = confirmer.confirm(message("明天下午三点到四点半开会"), MEETING)
    assert cand is not None
    assert cand.start_at == "2026-08-26T15:00:00"
    assert cand.end_at == "2026-08-26T16:30:00"


def test_missing_end_time_is_not_invented():
    cap = CaptureCompleter('{"is_users_plan":true,"date":"2026-08-26","time":"15:00","title":"组会"}')
    confirmer = ModelConfirmer(completer=cap, router=StubRouter())
    cand = confirmer.confirm(message("明天下午三点开会"), MEETING)
    assert cand is not None and cand.end_at is None


def test_garbage_end_time_is_not_invented():
    cap = CaptureCompleter(
        '{"is_users_plan":true,"date":"2026-08-26","time":"15:00","end_time":"soon","title":"组会"}'
    )
    confirmer = ModelConfirmer(completer=cap, router=StubRouter())
    cand = confirmer.confirm(message("明天下午三点开会"), MEETING)
    assert cand is not None and cand.end_at is None


# --------------------------------------------------------------------------
# ADR-0011 — L1 is still rejected on the production route
# --------------------------------------------------------------------------


def test_l1_route_is_rejected_and_confirm_degrades_to_none():
    confirmer = ModelConfirmer(completer=CaptureCompleter(), router=StubRouter(l1=True))
    with pytest.raises(RoutingError):
        confirmer.route()
    assert confirmer.confirm(message("明天下午三点开会"), MEETING) is None


def test_default_production_path_wires_heuristic_then_model(tmp_path):
    pipeline = ChatlogPipeline(
        config=CollectorConfig(talkers=["班级群"], enabled=True),
        client=FakeClient({}),
        service=AgendaService(tmp_path / "agenda.db"),
        seen=SeenStore(tmp_path / "seen.db"),
        talkers=TalkerStore(tmp_path / "talkers.db"),
    )
    assert isinstance(pipeline.confirmer, FallbackConfirmer)
    assert isinstance(pipeline.confirmer._primary, HeuristicConfirmer)
    assert isinstance(pipeline.confirmer._fallback, ModelConfirmer)


def test_default_confirmer_degrades_gracefully_when_quota_unavailable(tmp_path, monkeypatch):
    import sys

    # Make "from plobi.quota.route import QuotaAwareCompleter" raise.
    monkeypatch.setitem(sys.modules, "plobi.quota.route", None)

    pipeline = ChatlogPipeline(
        config=CollectorConfig(talkers=["班级群"], enabled=True),
        client=FakeClient({}),
        service=AgendaService(tmp_path / "agenda.db"),
        seen=SeenStore(tmp_path / "seen.db"),
        talkers=TalkerStore(tmp_path / "talkers.db"),
    )

    assert isinstance(pipeline.confirmer, HeuristicConfirmer)
    assert not isinstance(pipeline.confirmer, FallbackConfirmer)


# --------------------------------------------------------------------------
# Supporting parses (sent_at / is_self) — independent of the impl's cases
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
