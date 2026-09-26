"""Phone protocol: message shape, reply parsing, and the expiry/miss paths."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from plobi.agenda import store
from plobi.agenda.dispatch import PendingDispatcher, format_pending, parse_reply
from plobi.agenda.service import AgendaService
from plobi.notify.base import RecordingNotifier


@pytest.fixture()
def svc(tmp_path):
    return AgendaService(tmp_path / "agenda.db")


@pytest.fixture()
def notifier():
    return RecordingNotifier()


@pytest.fixture()
def dispatcher(svc, notifier):
    return PendingDispatcher(service=svc, notifier=notifier)


@pytest.mark.parametrize(
    "text,seq,accept",
    [
        ("确认 3", 3, True),
        ("确认3", 3, True),
        ("  确定 12 ", 12, True),
        ("ok 4", 4, True),
        ("忽略 5", 5, False),
        ("忽略5", 5, False),
        ("no 7", 7, False),
    ],
)
def test_reply_parsing(text, seq, accept):
    command = parse_reply(text)
    assert command is not None
    assert command.seq == seq
    assert command.accept is accept


@pytest.mark.parametrize(
    "text",
    [
        "明天几点开会",
        "确认",           # no sequence
        "3",              # no verb
        "帮我确认一下这个方案 3 号文件",
        "",
    ],
)
def test_ordinary_chat_is_not_mistaken_for_a_command(text):
    assert parse_reply(text) is None


def test_message_shows_old_and_new_time(svc, dispatcher, notifier):
    original = svc.create_manual(title="组会", start_at="2026-08-26T15:00:00", kind="meeting")
    svc.ingest_candidate(
        title="组会",
        start_at="2026-08-26T16:00:00",
        kind="meeting",
        target_event_id=original.id,
        evidence={"snippet": "组会改到下午四点", "talker": "课程群", "sent_at": "2026-08-26 09:12:00"},
    )

    sent = dispatcher.notify_pending()
    assert sent == [original.id]

    body = notifier.sent[0]
    # ui-l1-console-spec §7 硬冻结格式
    assert body.startswith("【待确认 #1】组会")
    assert "原值: 08-26 15:00  →  新值: 08-26 16:00" in body
    assert "来源: 「课程群」09:12 消息（组会改到下午四点）" in body
    assert "回复: 确认1 / 忽略1" in body


def test_new_proposal_message_has_no_arrow(svc, dispatcher, notifier):
    svc.ingest_candidate(title="临时答辩", start_at="2026-08-26T10:00:00", kind="meeting")
    dispatcher.notify_pending()

    assert "→" not in notifier.sent[0]
    assert "时间: 08-26 10:00" in notifier.sent[0]
    assert "回复: 确认1 / 忽略1" in notifier.sent[0]


def test_human_clock_uses_relative_day(svc, dispatcher, notifier):
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%dT09:00:00")
    svc.ingest_candidate(title="晨会", start_at=tomorrow)
    dispatcher.notify_pending()
    assert "明日 09:00" in notifier.sent[0]


def test_nothing_pending_sends_nothing(dispatcher, notifier):
    assert dispatcher.notify_pending() == []
    assert notifier.sent == []


def test_unconfigured_notifier_leaves_events_pending(svc):
    from plobi.notify.base import NullNotifier

    svc.ingest_candidate(title="组会", start_at="2026-08-26T16:00:00")
    dispatcher = PendingDispatcher(service=svc, notifier=NullNotifier())

    assert dispatcher.notify_pending() == []
    # Still pending, so the next sweep can retry the push.
    assert len(svc.list_pending()) == 1


def test_confirm_reply_resolves_the_event(svc, dispatcher):
    result = svc.ingest_candidate(title="组会", start_at="2026-08-26T16:00:00")

    ack = dispatcher.handle_reply(f"确认 {result.event.confirm_seq}")
    assert ack is not None and "已确认" in ack
    assert svc.get(result.event.id).status == "confirmed"


def test_dismiss_reply_rolls_back(svc, dispatcher):
    original = svc.create_manual(title="组会", start_at="2026-08-26T15:00:00")
    result = svc.ingest_candidate(
        title="组会", start_at="2026-08-26T16:00:00", target_event_id=original.id
    )

    ack = dispatcher.handle_reply(f"忽略 {result.event.confirm_seq}")
    assert ack is not None and "已恢复" in ack
    assert svc.get(original.id).start_at == "2026-08-26T15:00:00"


def test_dismiss_of_new_proposal_says_it_was_dropped(svc, dispatcher):
    result = svc.ingest_candidate(title="临时会", start_at="2026-08-26T16:00:00")

    ack = dispatcher.handle_reply(f"忽略 {result.event.confirm_seq}")
    assert ack is not None and "未加入日程" in ack


def test_expired_sequence_is_refused(svc, dispatcher, tmp_path):
    result = svc.ingest_candidate(title="组会", start_at="2026-08-26T16:00:00")
    stale = (datetime.now() - timedelta(hours=25)).replace(microsecond=0).isoformat()
    conn = store.connect(tmp_path / "agenda.db")
    store.update_event(conn, result.event.id, confirm_seq_at=stale)
    conn.close()

    ack = dispatcher.handle_reply(f"确认 {result.event.confirm_seq}")
    assert ack is not None and "已过期" in ack
    assert svc.get(result.event.id).status == "pending"


def test_unknown_sequence_gets_a_friendly_answer(dispatcher):
    ack = dispatcher.handle_reply("确认 99")
    assert ack is not None and "没有待确认" in ack


def test_non_command_returns_none_so_chat_flows_through(dispatcher):
    assert dispatcher.handle_reply("明天几点开会？") is None
