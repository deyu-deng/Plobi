"""Collector pipeline: whitelist, dedupe, snippet-only evidence, change matching."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from plobi.agenda.service import AgendaService
from plobi.collectors.chatlog.client import ChatlogClient, ChatMessage, normalize_message
from plobi.collectors.chatlog.config import CollectorConfig
from plobi.collectors.chatlog.confirm import HeuristicConfirmer
from plobi.collectors.chatlog.pipeline import (
    ChatlogPipeline,
    IngestReport,
    refresh_agenda,
)
from plobi.collectors.chatlog.state import SeenStore, TalkerStore

NOW = datetime(2026, 8, 25, 10, 0)


class FakeClient:
    """Stands in for the chatlog HTTP service."""

    def __init__(
        self,
        by_talker: dict[str, list[ChatMessage]] | None = None,
        sessions: list[str] | None = None,
        fail: bool = False,
    ):
        self.by_talker = by_talker or {}
        self.sessions = sessions if sessions is not None else list(self.by_talker.keys())
        self.fail = fail
        self.calls: list[tuple[str, date | None]] = []

    def fetch(self, talker: str, day=None):
        self.calls.append((talker, day))
        if self.fail:
            from plobi.collectors.chatlog.client import ChatlogUnavailable

            raise ChatlogUnavailable("boom")
        return self.by_talker.get(talker, [])

    def list_talkers(self, keyword: str = "", limit: int = 10000) -> list[str]:
        if keyword:
            return [t for t in self.sessions if keyword in t]
        return list(self.sessions)

    def healthy(self):
        return not self.fail


def message(content: str, *, talker: str = "班级群", msg_id: str = "m1") -> ChatMessage:
    return ChatMessage(
        msg_id=msg_id,
        talker=talker,
        sender="导师",
        sent_at="2026-08-25 10:00:00",
        content=content,
    )


@pytest.fixture()
def pipeline(tmp_path):
    return ChatlogPipeline(
        config=CollectorConfig(talkers=["班级群"], enabled=True),
        client=FakeClient(),
        service=AgendaService(tmp_path / "agenda.db"),
        seen=SeenStore(tmp_path / "seen.db"),
        talkers=TalkerStore(tmp_path / "talkers.db"),
        confirmer=HeuristicConfirmer(now_factory=lambda: NOW),
    )


def blacklist_pipeline(tmp_path, *, client, blacklist=None, known=(), excluded=(), review_done=True):
    """Blacklist-mode pipeline with a pre-seeded admission ledger."""
    store = TalkerStore(tmp_path / "talkers.db")
    for talker in known:
        store.mark_known(talker)
    for talker in excluded:
        store.mark_excluded(talker)
    store.set_review_done(review_done)
    return ChatlogPipeline(
        config=CollectorConfig(mode="blacklist", blacklist=list(blacklist or []), enabled=True),
        client=client,
        service=AgendaService(tmp_path / "agenda.db"),
        seen=SeenStore(tmp_path / "seen.db"),
        talkers=store,
        confirmer=HeuristicConfirmer(now_factory=lambda: NOW),
    )


def test_non_whitelisted_talker_is_never_read(pipeline):
    report = IngestReport()
    pipeline.handle_message(message("明天下午三点开会", talker="私人聊天"), report)

    assert report.skipped_not_whitelisted == 1
    assert pipeline.service.list_pending() == []
    # It must not even enter the dedupe ledger — we never looked at it.
    assert pipeline.seen.already_seen("m1") is False


def test_blacklist_webhook_new_talker_goes_pending_not_ingested(tmp_path):
    """Single-message path (webhook) obeys the same fail-closed gate."""
    pipeline = blacklist_pipeline(
        tmp_path, client=FakeClient(), known=["班级群"], review_done=False
    )

    report = IngestReport()
    pipeline.handle_message(message("明天下午三点开组会", talker="新冒出来的群"), report)

    assert report.skipped_new_talker == 1
    assert pipeline.service.list_pending() == []
    assert "新冒出来的群" in pipeline.talkers.pending()
    assert pipeline.seen.already_seen("m1") is False


def test_blacklist_webhook_excluded_talker_never_ingested(tmp_path):
    pipeline = blacklist_pipeline(tmp_path, client=FakeClient(), excluded=["广告群"])

    report = IngestReport()
    pipeline.handle_message(message("明天下午三点开组会", talker="广告群"), report)

    assert report.skipped_excluded == 1
    assert pipeline.service.list_pending() == []
    assert pipeline.seen.already_seen("m1") is False


def test_duplicate_message_is_ingested_once(pipeline):
    report = IngestReport()
    pipeline.handle_message(message("明天下午三点开组会"), report)
    pipeline.handle_message(message("明天下午三点开组会"), report)

    assert report.skipped_duplicate == 1
    assert len(pipeline.service.list_pending()) == 1


def test_chatter_is_filtered_before_any_confirm(pipeline):
    report = IngestReport()
    pipeline.handle_message(message("好的收到"), report)

    assert report.filtered_out == 1
    assert pipeline.service.list_pending() == []


def test_schedule_message_lands_pending_with_snippet_evidence(pipeline):
    report = IngestReport()
    pipeline.handle_message(message("明天下午三点开组会，别迟到"), report)

    pending = pipeline.service.list_pending()
    assert len(pending) == 1
    event = pending[0]
    assert event.status == "pending"
    assert event.source == "wechat"
    assert event.start_at == "2026-08-26T15:00:00"
    assert event.kind == "meeting"
    assert event.confirm_seq == 1
    assert event.evidence["talker"] == "班级群"
    assert "组会" in event.evidence["snippet"]
    assert report.created == [event.id]


def test_evidence_carries_only_a_snippet_not_the_whole_thread(pipeline):
    long_message = "明天下午三点开组会 " + ("闲聊内容 " * 200)
    report = IngestReport()
    pipeline.handle_message(message(long_message), report)

    evidence = pipeline.service.list_pending()[0].evidence
    assert len(evidence["snippet"]) <= 400
    assert len(evidence["snippet"]) < len(long_message)


def test_ambiguous_time_is_left_unresolved(pipeline):
    report = IngestReport()
    # A topical keyword and a clock, but no day — too ambiguous to schedule.
    pipeline.handle_message(message("三点开会吧"), report)

    assert report.unresolved == 1
    assert pipeline.service.list_pending() == []


def test_reschedule_updates_the_matching_event(pipeline):
    original = pipeline.service.create_manual(
        title="组会", start_at="2026-08-26T15:00:00", kind="meeting"
    )

    report = IngestReport()
    pipeline.handle_message(message("明天的组会改到下午四点", msg_id="m2"), report)

    assert report.updated == [original.id]
    updated = pipeline.service.get(original.id)
    assert updated.status == "pending"
    assert updated.start_at == "2026-08-26T16:00:00"
    assert updated.prev_value["start_at"] == "2026-08-26T15:00:00"


def test_unrelated_change_does_not_hijack_an_event(pipeline):
    pipeline.service.create_manual(title="组会", start_at="2026-08-26T15:00:00", kind="meeting")

    report = IngestReport()
    pipeline.handle_message(message("明天的聚餐改到六点", msg_id="m3"), report)

    # A separate pending entry, not a rewrite of the 组会 row.
    assert report.created and not report.updated


def test_run_once_respects_enabled_flag(tmp_path):
    disabled = ChatlogPipeline(
        config=CollectorConfig(talkers=["班级群"], enabled=False),
        client=FakeClient({"班级群": [message("明天下午三点开组会")]}),
        service=AgendaService(tmp_path / "agenda.db"),
        seen=SeenStore(tmp_path / "seen.db"),
        confirmer=HeuristicConfirmer(now_factory=lambda: NOW),
    )

    report = disabled.run_once()
    assert report.scanned == 0
    assert disabled.client.calls == []


def test_run_once_sweeps_each_whitelisted_talker(tmp_path):
    client = FakeClient(
        {
            "班级群": [message("明天下午三点开组会", msg_id="a1")],
            "项目群": [message("周三上午十点答辩", msg_id="b1", talker="项目群")]
        }
    )
    pipeline = ChatlogPipeline(
        config=CollectorConfig(talkers=["班级群", "项目群"], enabled=True),
        client=client,
        service=AgendaService(tmp_path / "agenda.db"),
        seen=SeenStore(tmp_path / "seen.db"),
        confirmer=HeuristicConfirmer(now_factory=lambda: NOW),
    )

    report = pipeline.run_once()
    assert [call[0] for call in client.calls] == ["班级群", "项目群"]
    assert len(report.created) == 2


def test_run_once_survives_chatlog_being_down(tmp_path):
    pipeline = ChatlogPipeline(
        config=CollectorConfig(talkers=["班级群"], enabled=True),
        client=FakeClient(fail=True),
        service=AgendaService(tmp_path / "agenda.db"),
        seen=SeenStore(tmp_path / "seen.db"),
        confirmer=HeuristicConfirmer(now_factory=lambda: NOW),
    )

    report = pipeline.run_once()
    assert report.scanned == 0


def test_empty_whitelist_collects_nothing(tmp_path):
    pipeline = ChatlogPipeline(
        config=CollectorConfig(talkers=[], enabled=True),
        client=FakeClient({"班级群": [message("明天下午三点开组会")]}),
        service=AgendaService(tmp_path / "agenda.db"),
        seen=SeenStore(tmp_path / "seen.db"),
        confirmer=HeuristicConfirmer(now_factory=lambda: NOW),
    )

    assert pipeline.run_once().scanned == 0


def test_blacklist_mode_collects_all_known_when_blacklist_empty(tmp_path):
    client = FakeClient(
        {
            "班级群": [message("明天下午三点开组会", msg_id="a1")],
            "项目群": [message("周三上午十点答辩", msg_id="b1", talker="项目群")],
            # WP-EXTRACT-CONTEXT: a date without a clock is no longer defaulted
            # to 09:00 — give this one a clock so the test still exercises
            # "blacklist collects every known talker" rather than the removed
            # default-clock behaviour.
            "私人聊天": [message("周五晚上八点聚餐", msg_id="c1", talker="私人聊天")],
        }
    )
    pipeline = blacklist_pipeline(
        tmp_path, client=client, known=["班级群", "项目群", "私人聊天"]
    )

    report = pipeline.run_once()
    # Every reviewed conversation is swept; none are blacklisted.
    assert set(call[0] for call in client.calls) == {"班级群", "项目群", "私人聊天"}
    assert len(report.created) == 3


def test_blacklist_mode_drops_blacklisted_talkers(tmp_path):
    client = FakeClient(
        {
            "班级群": [message("明天下午三点开组会", msg_id="a1")],
            "项目群": [message("周三上午十点答辩", msg_id="b1", talker="项目群")],
        }
    )
    pipeline = blacklist_pipeline(
        tmp_path, client=client, blacklist=["项目群"], known=["班级群", "项目群"]
    )

    report = pipeline.run_once()
    assert [call[0] for call in client.calls] == ["班级群"]
    assert len(report.created) == 1


def test_blacklist_mode_skips_reviewed_excluded_talkers(tmp_path):
    client = FakeClient(
        {
            "班级群": [message("明天下午三点开组会", msg_id="a1")],
            "广告群": [message("周五晚上聚餐", msg_id="c1", talker="广告群")],
        }
    )
    pipeline = blacklist_pipeline(
        tmp_path, client=client, known=["班级群"], excluded=["广告群"]
    )

    report = pipeline.run_once()
    assert [call[0] for call in client.calls] == ["班级群"]
    assert len(report.created) == 1
    assert report.skipped_excluded == 1
    # An excluded talker must never land in the agenda.
    assert all(event.evidence["talker"] != "广告群" for event in pipeline.service.list_pending())


def test_blacklist_mode_new_talker_is_fail_closed(tmp_path):
    client = FakeClient(
        {
            "班级群": [message("明天下午三点开组会", msg_id="a1")],
            "新冒出来的群": [message("周五晚上聚餐", msg_id="c1", talker="新冒出来的群")],
        }
    )
    pipeline = blacklist_pipeline(tmp_path, client=client, known=["班级群"])

    report = pipeline.run_once()
    # The new talker is never fetched — not even its messages are read.
    assert [call[0] for call in client.calls] == ["班级群"]
    assert report.skipped_new_talker == 1
    # The only ingested event comes from a known talker; the new one is zero.
    assert all(e.evidence["talker"] != "新冒出来的群" for e in pipeline.service.list_pending())
    # …but it surfaced on the board as pending for a one-click decision.
    assert "新冒出来的群" in pipeline.talkers.pending()


def test_blacklist_mode_gated_until_review_completes(tmp_path):
    client = FakeClient(
        {
            "班级群": [message("明天下午三点开组会", msg_id="a1")],
            "项目群": [message("周三上午十点答辩", msg_id="b1", talker="项目群")],
        }
    )
    pipeline = blacklist_pipeline(
        tmp_path,
        client=client,
        known=["班级群", "项目群"],
        review_done=False,
    )

    report = pipeline.run_once()
    # Nothing is swept before the first-run review; candidates are counted.
    assert report.review_gated == 2
    assert client.calls == []
    assert pipeline.service.list_pending() == []


# --------------------------------------------------------------------------
# WP-M1-COLLECT-CLOSE: lookback sweeps (review-complete kick + sweep endpoint)
# --------------------------------------------------------------------------


def test_run_once_lookback_fetches_each_day_and_dedupes(tmp_path):
    """lookback=3 = today included, three days, oldest first — and the same
    msg_id delivered by every day's window must land only once."""
    client = FakeClient({"班级群": [message("明天下午三点开组会", msg_id="a1")]})
    pipeline = blacklist_pipeline(tmp_path, client=client, known=["班级群"])

    report = pipeline.run_once(lookback_days=3)

    days = [day for (_talker, day) in client.calls]
    assert len(days) == 3
    assert all(day is not None for day in days)
    assert len(set(days)) == 3
    assert days == sorted(days), "oldest day first"
    # Dedupe ledger keeps the repeated message to a single agenda entry.
    assert len(report.created) == 1
    assert report.skipped_duplicate == 2


def test_run_once_default_lookback_keeps_single_day(tmp_path):
    """The 10-minute watchdog path is untouched: day=None passes through."""
    client = FakeClient({"班级群": [message("明天下午三点开组会", msg_id="a1")]})
    pipeline = blacklist_pipeline(tmp_path, client=client, known=["班级群"])

    pipeline.run_once()

    assert [day for (_talker, day) in client.calls] == [None]


def test_run_once_lookback_honours_explicit_day_anchor(tmp_path):
    client = FakeClient({"班级群": []})
    pipeline = blacklist_pipeline(tmp_path, client=client, known=["班级群"])

    anchor = date(2026, 9, 10)
    pipeline.run_once(anchor, lookback_days=3)

    assert [day for (_talker, day) in client.calls] == [
        date(2026, 9, 8),
        date(2026, 9, 9),
        date(2026, 9, 10),
    ]


# --------------------------------------------------------------------------
# WP-M1-COLLECT-CLOSE: refresh_agenda widens only an empty reviewed board
# --------------------------------------------------------------------------


def test_refresh_agenda_widens_lookback_when_board_is_empty(tmp_path):
    """Reviewed + today/tomorrow both empty → one extra 3-day sweep; chatter
    already seen in the first sweep is deduped, not double-counted."""
    client = FakeClient({"班级群": [message("好的收到", msg_id="c1")]})
    pipeline = blacklist_pipeline(tmp_path, client=client, known=["班级群"])

    report, summary = refresh_agenda(pipeline=pipeline)

    assert len(client.calls) == 4  # 1-day routine sweep + 3-day widened sweep
    assert summary.get("lookback_widened") is True
    assert report.scanned == 3
    # The chatter seen in the routine sweep comes back in all 3 widened
    # fetches — deduped every time, never re-ingested.
    assert report.skipped_duplicate == 3
    assert summary["events"] == []  # honest zero, nothing invented


def test_refresh_agenda_does_not_widen_when_board_has_events(tmp_path):
    client = FakeClient({"班级群": []})
    pipeline = blacklist_pipeline(tmp_path, client=client, known=["班级群"])
    tomorrow = (datetime.now() + timedelta(days=1)).date().isoformat()
    pipeline.service.create_manual(
        title="组会", start_at=f"{tomorrow}T15:00:00", kind="meeting"
    )

    _report, summary = refresh_agenda(pipeline=pipeline)

    assert len(client.calls) == 1, "non-empty board pays only the routine sweep"
    assert not summary.get("lookback_widened", False)
    assert [event["title"] for event in summary["events"]] == ["组会"]


def test_refresh_agenda_does_not_widen_before_review(tmp_path):
    client = FakeClient({"班级群": []})
    pipeline = blacklist_pipeline(
        tmp_path, client=client, known=["班级群"], review_done=False
    )

    report, summary = refresh_agenda(pipeline=pipeline)

    assert report.review_gated == 1
    assert client.calls == [], "gated: no fetch at all, and no widening"
    assert not summary.get("lookback_widened", False)


def test_blacklist_mode_no_talkers_when_enum_empty(tmp_path):
    pipeline = blacklist_pipeline(tmp_path, client=FakeClient({}, sessions=[]))

    report = pipeline.run_once()
    assert report.scanned == 0
    assert report.review_gated == 0
    assert pipeline.client.calls == []


def test_config_blacklist_mode_load_save(tmp_path):
    path = tmp_path / "chatlog.json"
    CollectorConfig(mode="blacklist", blacklist=["项目群"], enabled=True).save(path)

    loaded = CollectorConfig.load(path)
    assert loaded.mode == "blacklist"
    assert loaded.blacklist == ["项目群"]
    assert loaded.allows("班级群") is True
    assert loaded.allows("项目群") is False


def test_config_blacklist_env_override(tmp_path, monkeypatch):
    path = tmp_path / "chatlog.json"
    CollectorConfig(mode="whitelist", talkers=["群A"], enabled=True).save(path)

    monkeypatch.setenv("PLOBI_CHATLOG_MODE", "blacklist")
    monkeypatch.setenv("PLOBI_CHATLOG_BLACKLIST", "群B, 群C")

    loaded = CollectorConfig.load(path)
    assert loaded.mode == "blacklist"
    assert loaded.blacklist == ["群B", "群C"]
    assert loaded.allows("群A") is True
    assert loaded.allows("群B") is False


def test_config_whitelist_and_env_override(tmp_path, monkeypatch):
    path = tmp_path / "chatlog.json"
    CollectorConfig(talkers=["群A"], enabled=True).save(path)

    loaded = CollectorConfig.load(path)
    assert loaded.talkers == ["群A"]
    assert loaded.allows("群A") is True
    assert loaded.allows("群B") is False

    monkeypatch.setenv("PLOBI_CHATLOG_TALKERS", "群B, 群C")
    assert CollectorConfig.load(path).talkers == ["群B", "群C"]


def test_config_defaults_to_collecting_nothing(tmp_path):
    empty = CollectorConfig.load(tmp_path / "missing.json")
    assert empty.enabled is False
    assert empty.talkers == []
    assert empty.allows("任何人") is False


@pytest.mark.parametrize(
    "raw",
    [
        {"content": "明天开会", "talker": "群", "time": "2026-08-25 10:00:00", "id": 7},
        {"Content": "明天开会", "Talker": "群", "Time": "2026-08-25 10:00:00", "Seq": 7},
        {"msg": "明天开会", "chatroom": "群", "createTime": "2026-08-25 10:00:00"},
    ],
)
def test_normalizer_tolerates_response_shapes(raw):
    parsed = normalize_message(raw)
    assert parsed is not None
    assert parsed.content == "明天开会"
    assert parsed.msg_id


def test_normalizer_rejects_unusable_records():
    assert normalize_message({"content": "   "}) is None
    assert normalize_message({"nothing": 1}) is None
    assert normalize_message("not a dict") is None


def test_normalizer_reads_is_self_and_never_guesses_when_absent():
    """WP-EXTRACT-CONTEXT: is_self is recorded when chatlog says so, else None."""
    # Absent -> unknown (None), never assumed to be the user or someone else.
    assert normalize_message({"content": "明天开会", "talker": "群"}).is_self is None
    # Present, in the several spellings chatlog uses.
    assert normalize_message({"content": "明天开会", "isSend": True}).is_self is True
    assert normalize_message({"content": "明天开会", "isSelf": 1}).is_self is True
    assert normalize_message({"content": "明天开会", "is_self": "true"}).is_self is True
    assert normalize_message({"content": "明天开会", "isSend": 0}).is_self is False
    assert normalize_message({"content": "明天开会", "isSend": "false"}).is_self is False
    # Present but not a boolean -> still unknown, never guessed.
    assert normalize_message({"content": "明天开会", "isSend": "maybe"}).is_self is None


def test_seen_store_prunes_old_rows(tmp_path):
    store = SeenStore(tmp_path / "seen.db")
    assert store.mark_seen("x") is True
    assert store.mark_seen("x") is False
    assert store.prune(retention_days=0) == 1
    assert store.already_seen("x") is False


def test_list_talker_sessions_carries_the_chatlog_display_name(monkeypatch):
    """The board shows real group/contact names — chatlog's `topicName`.

    `list_talkers` (ids only) stays intact for the blacklist sweep; the new
    `list_talker_sessions` is what the collection API reads names from. A
    conversation with no label falls back to its id so no row is blank.
    """
    client = ChatlogClient("http://127.0.0.1:5030")

    def fake_get(path: str, params: dict[str, str]) -> object:
        assert path == "/api/v1/session"
        return {
            "items": [
                {"isChatroom": True, "topicId": "123@chatroom", "topicName": "拓扑测试群"},
                {"isChatroom": False, "personID": "wxid_abc", "topicName": ""},
                # Duplicate id later in the list is dropped.
                {"isChatroom": True, "topicId": "123@chatroom", "topicName": "重复"},
                "not a dict",
            ]
        }

    monkeypatch.setattr(client, "_get", fake_get)

    sessions = client.list_talker_sessions()

    assert [(session.id, session.name) for session in sessions] == [
        ("123@chatroom", "拓扑测试群"),
        ("wxid_abc", "wxid_abc"),
    ]
    # ids-only view unchanged for blacklist / full-collection sweeps.
    assert client.list_talkers() == ["123@chatroom", "wxid_abc"]


# --------------------------------------------------------------------------
# WP-EXTRACT-CONTEXT: sent-at anchor, sender evidence, ±1 neighbour context
# --------------------------------------------------------------------------


def test_evidence_records_who_sent_the_message(pipeline):
    """The board shows "谁说的" — sender must ride into the evidence."""
    report = IngestReport()
    pipeline.handle_message(message("明天下午三点开组会"), report)

    evidence = pipeline.service.list_pending()[0].evidence
    assert evidence["sender"] == "导师"
    assert evidence["sent_at"] == "2026-08-25 10:00:00"
    assert evidence["talker"] == "班级群"


def test_message_sent_last_night_anchors_tomorrow_on_its_sent_time(tmp_path):
    """Sent 9/10 23:00, "明天下午三点" → 9/11 15:00, not the wall-clock day."""
    sent = ChatMessage(
        msg_id="s1",
        talker="班级群",
        sender="导师",
        sent_at="2026-09-10T23:00",
        content="明天下午三点开会",
    )
    pipeline = ChatlogPipeline(
        config=CollectorConfig(talkers=["班级群"], enabled=True),
        client=FakeClient({"班级群": [sent]}),
        service=AgendaService(tmp_path / "agenda.db"),
        seen=SeenStore(tmp_path / "seen.db"),
        # Wall clock has rolled past midnight; the sent time must still win.
        confirmer=HeuristicConfirmer(now_factory=lambda: datetime(2026, 9, 11, 0, 5)),
    )

    report = pipeline.run_once()

    assert report.created, "the anchored message should have been ingested"
    assert pipeline.service.list_pending()[0].start_at == "2026-09-11T15:00:00"


def test_neighbours_are_capped_at_one_each_side(tmp_path):
    """Only the ±1 lines reach a confirmer — never the 3rd message back."""
    captured: dict[str, object] = {}

    class ContextRecorder:
        def confirm(self, message, hit, context=None):
            captured[message.msg_id] = context
            return None

    msgs = [
        message("明天下午三点开会甲", msg_id="a1"),
        message("明天下午三点开会乙", msg_id="a2"),
        message("明天下午三点开会丙", msg_id="a3"),
        message("明天下午三点开会丁", msg_id="a4"),
    ]
    pipeline = ChatlogPipeline(
        config=CollectorConfig(talkers=["班级群"], enabled=True),
        client=FakeClient({"班级群": msgs}),
        service=AgendaService(tmp_path / "agenda.db"),
        seen=SeenStore(tmp_path / "seen.db"),
        confirmer=ContextRecorder(),
    )

    pipeline.run_once()

    first = captured["a1"]
    assert first.prev_snippet is None
    assert "乙" in first.next_snippet

    second = captured["a2"]
    assert "甲" in second.prev_snippet
    assert "丙" in second.next_snippet
    # Two lines away: the 4th must never appear in the 2nd's context.
    assert "丁" not in (second.prev_snippet or "")
    assert "丁" not in (second.next_snippet or "")

    last = captured["a4"]
    assert last.next_snippet is None
    assert "丙" in last.prev_snippet
