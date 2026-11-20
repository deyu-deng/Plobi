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
    watchdog = Watchdog(
        pipeline=pipeline,
        notifier=notifier,
        state_path=tmp_path / "watchdog_state.json",
        failure_threshold=3,
        # WP-DT-DIGEST 的早报补发会给 notifier 多加一条消息，把这里的告警计数搅乱；
        # 它自己有专测（tests/plobi/test_day_digest.py），本文件只测健康巡检。
    )
    return watchdog, client


@pytest.fixture(autouse=True)
def _wechat_enabled():
    """把微信这一条**打开**。

    11-08 起这一条默认关（用户 11-05 原话「用户没有配置微信。就不采集。」）；关着的时候
    `ChatlogPipeline.health()` 答的是第四态 `disabled`，采集那一格**不开跑**。

    2026-11-17（`WP-COLLECT-UNIFY`）：巡检那一趟**也**改走公共触发面了 ⇒ 这一族自己
    的 `collect()` 说了算 ⇒ 那道开关是**真的**开关（从前巡检直接调 `run_once`，绕过了它）。
    """
    from plobi.collectors import sources_config

    sources_config.set_enabled("wechat", True)
    yield


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

        # 2026-11-17（`WP-COLLECT-UNIFY`）：巡检那一趟走公共触发面了 ⇒ 这一族暴露的是
        # **采集器那一格**（`collect()`），不再是 `pipeline.run_once()`。窗口从哪来、
        # 走哪条路，都由公共触发面递进来。
        def collect(self, service=None, **params):
            seen["lookback_days"] = params.get("lookback_days")
            return {
                "state": "ready",
                "rows_seen": 0,
                "created": 0,
                "updated": 0,
                "unchanged": 0,
                "pending_ids": [],
                "report": {"scanned": 0},
            }

        client = None  # Watchdog 构造时会取 pipeline.client 兜底，这里不需要探活

        def health(self):
            return {"ok": True, "state": "ready", "detail": "ok", "binary": None}

    wd = Watchdog(pipeline=_Reporting(), state_path=tmp_path / "state.json")
    wd._sweep_and_check()
    assert seen["lookback_days"] == CollectorConfig().time_window_days
    assert CollectorConfig().time_window_days >= 7, "默认窗口太短就等于还是只看当天"


def test_healthy_tick_sweeps_and_keeps_counter_at_zero(tmp_path):
    notifier = RecordingNotifier()
    watchdog, client = _make(tmp_path, fail=False, notifier=notifier)

    result = watchdog.tick()

    assert result["chatlog_healthy"] is True
    assert result["consecutive_failures"] == 0
    # 一跑了就算：跑几趟由配置里那个时间窗决定（原来这里钉 ==1，等于把"只看当天"当契约）
    assert client.fetch_calls >= 1
    assert watchdog.state_path.read_text().count("consecutive_failures") == 1


def test_three_failures_trigger_one_alert(tmp_path):
    """连续失败三次 ⇒ 告警一条；第四轮仍坏 ⇒ 不再重复告警（限频）。

    2026-11-20（`WP-WECHAT-LEG-OWNS-PROCESS`）：**没有「自愈」了**——那一轮自带
    起停（腿自己把采集进程拉起来、采完收掉），没有「让它常驻」这回事。
    """
    notifier = RecordingNotifier()
    watchdog, _ = _make(tmp_path, fail=True, notifier=notifier)

    for _ in range(3):
        result = watchdog.tick()

    assert result["consecutive_failures"] == 3
    assert result["alerted"] is True
    assert len(notifier.sent) == 1
    assert "chatlog" in notifier.sent[0]

    # 4th tick: still down — no new alert (rate limited)
    result = watchdog.tick()
    assert result["alerted"] is False


def test_recovery_resets_counter_and_allows_next_episode(tmp_path):
    """恢复了就把计数清零；再断一次，重新计数（不是接着上一次数）。"""
    notifier = RecordingNotifier()
    watchdog, client = _make(tmp_path, fail=True, notifier=notifier)

    for _ in range(3):
        watchdog.tick()
    client.fail = False
    assert watchdog.tick()["chatlog_healthy"] is True
    assert watchdog.state["consecutive_failures"] == 0

    client.fail = True
    for _ in range(3):
        watchdog.tick()
    assert watchdog.state["consecutive_failures"] == 3, "新的断连周期重新计数"


def test_sweep_results_are_notified_through_dispatcher(tmp_path, _pool):
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
        # 见 _make()：早报补发另有专测，这里不该多出一条消息。
    )

    result = watchdog.tick()
    assert result["sweep"]["created"], "巡检扫到的那一条要落进池"
    rows = _pooled(_pool)
    assert len(rows) == 1
    assert service.list_pending() == [], "采集这一腿不写日程，所以也不推送"
    assert notifier.sent == []

    # 「待确认项必须推送」那条守卫还在，只是搬到了 Agent 那一跳之后：
    # 读池 → 走日程那条唯一的写路径 → 推送跟着「变成待确认」走。
    from plobi.collectors.base import Occurrence
    from plobi.collectors.ingest import ingest_occurrences

    context = dict(rows[0]["context"] or {})
    landed = ingest_occurrences(
        service,
        [Occurrence(source=rows[0]["source"], external_id=rows[0]["external_id"],
                    title=rows[0]["title"], start_at=context.get("event_at") or rows[0]["occurred_at"],
                    kind=rows[0]["kind"], evidence=context)],
        apply="pending",
    )
    assert landed["created"] == 1
    # 推送这一步在产品里跟着「有人把它变成待确认」走（webhook 的 _notify / 日程工具那条手）
    agenda_dispatch._DEFAULT.notify_pending([service.get(landed["ids"][0])])
    assert notifier.sent, "变成待确认之后必须推送"


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


# 2026-11-20（`WP-WECHAT-LEG-OWNS-PROCESS`）：**自愈那一族退役了**——那一轮自带
# 起停（腿自己把采集进程拉起来、采完收掉），没有「让它常驻」这回事，也就没有
# 「自愈」可做。从前那两档测的是「拉一把没成之后还拉不拉」（限速闩），
# 现在那个问题**不存在了**：采集那一轮每轮都自己起一次，起不来就把原因报出去。
# ⇒ 那两档随这一族一起删（不是「留着但是不用」）。