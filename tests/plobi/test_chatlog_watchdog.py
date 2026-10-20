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
        config=CollectorConfig(mode="whitelist", talkers=["班级群"], enabled=True),
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


def test_the_daily_sweep_uses_the_configured_window(tmp_path, monkeypatch) -> None:
    """日采那一轮必须真往回扫，且窗口只由配置决定——调用处不许自己拍大数。"""

    from plobi.collectors.chatlog.config import CollectorConfig
    from plobi.collectors.chatlog.watchdog import Watchdog

    seen: dict = {}

    class _Reporting:
        config = CollectorConfig()

        def run_once(self, day=None, lookback_days=1):
            seen["lookback_days"] = lookback_days

            class _R:
                pending_ids: list = []

                @staticmethod
                def as_dict():
                    return {"scanned": 0}
            return _R()

        client = None  # Watchdog 构造时会取 pipeline.client 兜底，这里不需要探活

        def health(self):
            return {"ok": True, "state": "ready", "detail": "ok", "binary": None}

    wd = Watchdog(pipeline=_Reporting(), state_path=tmp_path / "state.json")
    wd._sweep_and_check()
    assert seen["lookback_days"] == CollectorConfig().time_window_days
    assert CollectorConfig().time_window_days >= 7, "默认窗口太短就等于还是只看当天"


def test_healthy_tick_sweeps_and_keeps_counter_at_zero(tmp_path):
    notifier = RecordingNotifier()
    watchdog, client, heal_calls = _make(tmp_path, fail=False, notifier=notifier)

    result = watchdog.tick()

    assert result["chatlog_healthy"] is True
    assert result["consecutive_failures"] == 0
    # 一跑了就算：跑几趟由配置里那个时间窗决定（原来这里钉 ==1，等于把"只看当天"当契约）
    assert client.fetch_calls >= 1
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
        config=CollectorConfig(mode="whitelist", talkers=["班级群"], enabled=True),
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
    """池子里那些行。不是 `service.list_pending()` -- 那是日程，采集器不再写它。"""
    return pool.query(**filters)