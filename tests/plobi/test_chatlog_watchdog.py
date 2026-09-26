"""chatlog 看门狗：失败计数、自愈一次、告警限频、恢复即重置（切片 A2）。"""

from __future__ import annotations

import pytest

from plobi.agenda import dispatch as agenda_dispatch
from plobi.agenda.dispatch import PendingDispatcher
from plobi.agenda.service import AgendaService
from plobi.collectors.chatlog.client import ChatlogUnavailable, ChatMessage
from plobi.collectors.chatlog.config import CollectorConfig
from plobi.collectors.chatlog.confirm import HeuristicConfirmer
from plobi.collectors.chatlog.pipeline import ChatlogPipeline
from plobi.collectors.chatlog.state import SeenStore
from plobi.collectors.chatlog.watchdog import Watchdog
from plobi.notify.base import RecordingNotifier


class FakeClient:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.fetch_calls = 0

    def fetch(self, talker, day=None):
        self.fetch_calls += 1
        if self.fail:
            raise ChatlogUnavailable("boom")
        return []

    def healthy(self):
        return not self.fail


def _make(tmp_path, fail: bool, notifier: RecordingNotifier):
    service = AgendaService(tmp_path / "agenda.db")
    client = FakeClient(fail=fail)
    pipeline = ChatlogPipeline(
        config=CollectorConfig(talkers=["班级群"], enabled=True),
        client=client,
        service=service,
        seen=SeenStore(tmp_path / "seen.db"),
        confirmer=HeuristicConfirmer(),
    )
    agenda_dispatch._DEFAULT = PendingDispatcher(service=service, notifier=notifier)
    heal_calls: list[int] = []
    watchdog = Watchdog(
        pipeline=pipeline,
        notifier=notifier,
        state_path=tmp_path / "watchdog_state.json",
        heal_command=lambda: heal_calls.append(1),
        failure_threshold=3,
        # WP-DT-DIGEST 的早报补发会给 notifier 多加一条消息，把这里的告警计数搅乱；
        # 它自己有专测（tests/plobi/test_day_digest.py），本文件只测健康巡检。
        morning_catchup=False,
    )
    return watchdog, client, heal_calls


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    agenda_dispatch._DEFAULT = None


def test_healthy_tick_sweeps_and_keeps_counter_at_zero(tmp_path):
    notifier = RecordingNotifier()
    watchdog, client, heal_calls = _make(tmp_path, fail=False, notifier=notifier)

    result = watchdog.tick()

    assert result["chatlog_healthy"] is True
    assert result["consecutive_failures"] == 0
    assert client.fetch_calls == 1  # sweep 跑了
    assert heal_calls == []
    assert watchdog.state_path.read_text().count("consecutive_failures") == 1


def test_three_failures_trigger_one_heal_and_one_alert(tmp_path):
    notifier = RecordingNotifier()
    watchdog, _, heal_calls = _make(tmp_path, fail=True, notifier=notifier)

    for _ in range(3):
        result = watchdog.tick()

    assert result["consecutive_failures"] == 3
    assert result["alerted"] is True
    assert len(notifier.sent) == 1
    assert "chatlog" in notifier.sent[0]
    assert len(heal_calls) == 1, "每个断连周期只自愈一次"

    # 4th tick: still down — no new alert (rate limited), no second heal
    result = watchdog.tick()
    assert result["alerted"] is False
    assert len(heal_calls) == 1


def test_recovery_resets_counter_and_allows_next_episode(tmp_path):
    notifier = RecordingNotifier()
    watchdog, client, heal_calls = _make(tmp_path, fail=True, notifier=notifier)

    for _ in range(3):
        watchdog.tick()
    client.fail = False
    assert watchdog.tick()["chatlog_healthy"] is True
    assert watchdog.state["consecutive_failures"] == 0

    client.fail = True
    for _ in range(3):
        watchdog.tick()
    assert len(heal_calls) == 2, "新断连周期允许再次自愈"


def test_sweep_results_are_notified_through_dispatcher(tmp_path):
    notifier = RecordingNotifier()
    service = AgendaService(tmp_path / "agenda.db")
    client = FakeClient(fail=False)

    class SeededClient(FakeClient):
        def fetch(self, talker, day=None):
            return [
                ChatMessage(
                    msg_id="m1",
                    talker=talker,
                    sender="课代表",
                    sent_at="2026-08-30 12:00:00",
                    content="明天下午三点开组会",
                )
            ]

    client = SeededClient()
    pipeline = ChatlogPipeline(
        config=CollectorConfig(talkers=["班级群"], enabled=True),
        client=client,
        service=service,
        seen=SeenStore(tmp_path / "seen.db"),
        confirmer=HeuristicConfirmer(),
    )
    agenda_dispatch._DEFAULT = PendingDispatcher(service=service, notifier=notifier)
    watchdog = Watchdog(
        pipeline=pipeline,
        state_path=tmp_path / "watchdog_state.json",
        heal_command=lambda: None,
        # 见 _make()：早报补发另有专测，这里不该多出一条消息。
        morning_catchup=False,
    )

    result = watchdog.tick()
    assert result["sweep"]["created"]
    assert notifier.sent, "巡检发现的待确认项必须推送"
