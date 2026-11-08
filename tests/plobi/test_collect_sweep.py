"""执行方那一层（`plobi/collectors/sweep.py`）的钉子：一条都不许静默省略。

跑法：`bash scripts/run_tests.sh tests/plobi/test_collect_sweep.py -q`（唯一入口，不传 `--basetemp`）。
全是关系断言：给定注册表那两格（开关 / 构造入口）与采集器的行为 ⇒ 读数必须怎么说。
零真网络、零真库。
"""

from __future__ import annotations

import io

import pytest

from plobi.collectors import registry, sweep
from plobi.collectors.base import Occurrence
from plobi.collectors.registry import SourceSpec

CAPS = {
    "history_backfill": False, "incremental_since": False, "supports_media": False,
    "requires_manual_setup": False, "authoritative_replace": False,
}


@pytest.fixture()
def fake_sources():
    """临时登记几条假信源，用完撤掉——注册表是进程级单例，不许留脏。"""
    registered: list[str] = []

    def _add(*specs: SourceSpec) -> None:
        for spec in specs:
            registry.register(spec, replace=True)
            registered.append(spec.source_id)

    yield _add
    for source_id in registered:
        registry.unregister(source_id)


class FakeCollector:
    def __init__(self, *, result=None, error=None):
        self._result, self._error = result, error

    def collect(self, service=None):
        if self._error is not None:
            raise self._error
        return self._result


def test_a_switched_on_source_with_no_builder_says_so(fake_sources):
    """注册表说在采、却没人构造得出它 —— 这就是 10-15 教务那一刀断的那一格，不许静默。"""
    fake_sources(SourceSpec("ghost", CAPS, collecting=True, builder=None, category="custom"))

    report = sweep.run_sweep(sources=("ghost",))

    row = report["sources"][0]
    assert row["ok"] is False and row["state"] == "no_builder"
    assert "构造得出" in row["detail"]
    assert report["ran"] == 0


class TestOneFailureDoesNotTakeTheOthers:
    def test_builder_crash_is_named_and_the_next_source_still_runs(self, fake_sources):
        boom = SourceSpec("boom", CAPS, collecting=True, builder=_raise, category="custom")
        good = SourceSpec("good", CAPS, collecting=True, category="custom",
                          builder=lambda: FakeCollector(result={"created": 2, "unchanged": 5}))
        fake_sources(boom, good)

        report = sweep.run_sweep(sources=("boom", "good"))
        rows = {r["source"]: r for r in report["sources"]}

        assert rows["boom"]["state"] == "build_failed" and RuntimeError.__name__ in rows["boom"]["detail"]
        assert rows["good"]["ok"] is True
        assert report["ran"] == 1 and report["total"] == 2

    def test_a_state_bearing_failure_keeps_its_own_words(self, fake_sources):
        """采集器自己带了 `state` / `detail` ⇒ 用它的，不在这儿编一句"没数据"。"""
        err = _NamedFailure("auth_failed", "站方说不认识这个口令")
        fake_sources(SourceSpec("z", CAPS, collecting=True, category="custom",
                                builder=lambda: FakeCollector(error=err)))

        row = sweep.run_sweep(sources=("z",))["sources"][0]

        assert row["state"] == "auth_failed" and "站方说不认识这个口令" in row["detail"]


class TestLandingSemantics:
    def test_pull_only_source_lands_in_the_pool_with_no_mode_to_ask_for(self, fake_sources, monkeypatch):
        """只有 `pull()` 的采集器**照落**，而且**没有档位可问**。

        WP-POOL（`specs/source-pool.md`，用户 10-19 原话「不要直接在日程库里面落
        东西」）：这一层只往信息池写。「落库档位」是**日程**的概念（ADR-0009 的
        待确认/已确认），池子不区分那两档——所以旧形状那道「没人给档位 ⇒ 不落库」
        的闸（`no_apply_mode`）**整条删了**：留着它就是还留着「日程落库需要一个人
        给档位」的形状，而池子不需要那个人。
        """
        calls: list = []
        import plobi.pool as pool

        monkeypatch.setattr(pool, "land", lambda items, **_kw: calls.append(len(items)) or {"created": len(items)})
        fake_sources(SourceSpec("p", CAPS, collecting=True, builder=lambda: _PullOnly([]), category="custom"))

        row = sweep.run_sweep(sources=("p",))["sources"][0]

        assert row["ok"] is True
        assert row["state"] == "ready"
        assert calls == [0]

    def test_pull_only_source_never_reaches_the_agenda_store(self, fake_sources, monkeypatch):
        """**这一格是整篇规格的中心**：这一层不认识 `agenda.db`，所以那条路是断的。"""
        import plobi.collectors.ingest as ingest
        import plobi.pool as pool

        def boom(*_args, **_kwargs):
            raise AssertionError("采集路径碰了日程库 —— 边界破了")

        monkeypatch.setattr(ingest, "ingest_occurrences", boom)
        monkeypatch.setattr(pool, "land", lambda items, **_kw: {"created": len(items)})
        fake_sources(SourceSpec("q", CAPS, collecting=True, category="custom", builder=lambda: _PullOnly([1, 2])))

        row = sweep.run_sweep(sources=("q",))["sources"][0]

        assert row["ok"] is True and row["state"] == "ready"

    def test_run_sweep_no_longer_takes_an_apply_mode(self):
        """那个参数**从签名里删了**，不是留着不用——留着就等于还留着一扇门。"""
        import inspect

        assert "apply_mode" not in inspect.signature(sweep.run_sweep).parameters


class TestVerdict:
    def test_nothing_switched_on_says_so_instead_of_faking_a_run(self, fake_sources):
        report = sweep.run_sweep(sources=())

        assert report["ran"] == 0 and "没有任何一条信源被点亮" in report["verdict"]

    def test_a_single_real_run_is_counted_and_named(self, fake_sources):
        fake_sources(SourceSpec("ok1", CAPS, collecting=True, category="custom",
                                builder=lambda: FakeCollector(result={"created": 0, "unchanged": 48})))

        report = sweep.run_sweep(sources=("ok1",))

        assert "1 / 1 条真跑了一轮" in report["verdict"] and "ok1" in report["verdict"]


class TestRealRegistry:
    def test_zju_is_switched_on_with_a_builder_now(self):
        """用户 2026-10-15 13:0x 拍的「每天自动采，新条目进待确认」⇒ 开关是真读数不是默认值。"""
        spec = registry.spec("zju")

        assert spec.collecting is True and callable(spec.builder)

    def test_the_builder_produces_a_usable_collector_without_touching_the_network(self):
        """构造本身不许打网络，但**也不许漏必填位**。

        10-15 13:07 真跑抓到的就是这一格：`ZjuCollector()` 少了 `transport` ⇒ 每天那一轮会稳定
        报 `build_failed`，而 `transport` 是判据 7 钉死的必填位（默认回退真网络调用＝那颗雷）。
        """
        collector = registry.spec("zju").builder()

        assert getattr(collector, "transport", None) is not None
        assert getattr(collector, "config", None) is not None

    def test_wechat_has_a_builder_and_starts_switched_off(self):
        """10-16 补的那格：`wechat` 以前只有开关没有构造入口 ⇒ 每轮报 `no_builder`，
        而真原因（没产物 / 没配置 / 没密钥 / 没在听）全被这一句挡在下面问不到。

        11-08 改判那一半：**默认关**。用户 11-05 原话「用户没有配置微信。就不采集。
        相关的采集腿也不要反复报错了」——从前这一条走 dataclass 的默认 `collecting=True`，
        于是全新装机上没人配过它也照样每轮被打、每轮写一条 `build_failed`、屏上再画一块
        红牌（用户 11-08 现场：「用户明明什么都没有配置……甚至微信还一直在报错」）。
        构造入口那一半**不变**：它仍要有 `builder`，不然真原因又被 `no_builder` 挡着问不到。
        """
        spec = registry.spec("wechat")

        assert spec.collecting is False, "默认关：没配好就不采（11-05 / 11-08）"
        assert callable(spec.builder), "但构造入口要在——不然真原因又问不到"

    def test_the_wechat_builder_hands_the_executor_a_usable_collector(self):
        """构造本身零网络（探活发生在 `collect()` 里，那之前不花一次调用）。"""
        collector = registry.spec("wechat").builder()

        assert collector.source_id == "wechat"
        assert callable(getattr(collector, "collect", None)), "sweep 只认 collect/pull 两种形状"

    def test_collect_names_the_diagnosed_state_instead_of_one_generic_sentence(self, monkeypatch):
        """判据「说人话」：不健康时抛出去的东西必须带状态名，不能是 `raised`。

        自愈那一跳显式接管：单测不许去探真 5030、更不许真 spawn 进程。
        """
        from plobi.collectors.chatlog import pipeline as chatlog_pipeline
        from plobi.collectors.chatlog.sidecar import STATE_KEY_MISSING

        pipe = registry.spec("wechat").builder()
        monkeypatch.setattr(
            chatlog_pipeline, "chatlog_is_healthy", lambda client: False
        )
        bad = {"ok": False, "detail": "少一把数据密钥", "state": STATE_KEY_MISSING, "binary": ""}
        monkeypatch.setattr(pipe.__class__, "health", lambda self: bad)
        monkeypatch.setattr(
            pipe, "_heal_and_reprobe", lambda health: (health, "（这个断连周期里已经试过一回了）")
        )

        with pytest.raises(chatlog_pipeline.ChatlogDead) as caught:
            pipe.collect()

        assert caught.value.state == STATE_KEY_MISSING
        assert "密钥" in caught.value.detail

    def test_collect_tries_exactly_one_heal_before_giving_up(self, monkeypatch):
        """常驻的驱动器就是日采这一轮：探到没人听先拉一把，而不是等 30 分钟巡检。"""
        from plobi.collectors.chatlog import pipeline as chatlog_pipeline

        pipe = registry.spec("wechat").builder()
        bad = {"ok": False, "detail": "5030 上没有 chatlog 服务", "state": "not_listening", "binary": ""}
        attempts: list = []
        monkeypatch.setattr(pipe.__class__, "health", lambda self: bad)
        monkeypatch.setattr(
            pipe, "_heal_and_reprobe",
            lambda health: (attempts.append(1) or (health, "（试过一次拉起，没成功）")),
        )

        with pytest.raises(chatlog_pipeline.ChatlogDead):
            pipe.collect()

        assert len(attempts) == 1

    def test_heal_gate_spawns_once_per_down_episode(self, tmp_path):
        """`attempt_heal_once` 与 `tick()` 共用同一个限速位：一个断连周期最多一次 spawn。

        传一个假 pipeline 是有意的：``Watchdog(pipeline=None)`` 会去构造真
        ``ChatlogPipeline``，那会在单测里读真家目录、建真客户端。
        """
        from plobi.collectors.chatlog.watchdog import Watchdog

        class _Client:
            def __init__(self) -> None:
                self.up = False

            def healthy(self) -> bool:
                return self.up

        class _NoPipeline:
            client = None
            config = None

        client = _Client()
        spawns: list = []
        dog = Watchdog(
            pipeline=_NoPipeline(), client=client,
            state_path=tmp_path / "watchdog_state.json",
            heal_command=lambda: spawns.append(1) or object(),
        )

        assert dog.attempt_heal_once()["healed"] is True
        second = dog.attempt_heal_once()
        assert second["healed"] is False and second["already_attempted"] is True
        assert len(spawns) == 1, "第二次还拉 ⇒ 那就成了 spawn 循环"

        client.up = True                                  # 恢复了健康
        assert dog.attempt_heal_once()["healthy"] is True
        client.up = False                                 # 再断一次
        assert dog.attempt_heal_once()["healed"] is True
        assert len(spawns) == 2, "限速位没随健康重置 ⇒ 以后真断了永远拉不起来"

    def test_sweep_row_for_wechat_carries_the_reason_not_no_builder(self, monkeypatch):
        """整条链的钉子：那一轮给面上的读数，必须是真原因，不再是"没人构造它"。"""
        from plobi.collectors.chatlog import pipeline as chatlog_pipeline
        from plobi.collectors.chatlog.sidecar import STATE_NOT_LISTENING

        bad = {"ok": False, "detail": "5030 上没有 chatlog 服务",
               "state": STATE_NOT_LISTENING, "binary": ""}
        monkeypatch.setattr(chatlog_pipeline.ChatlogPipeline, "health", lambda self: bad)
        monkeypatch.setattr(
            chatlog_pipeline.ChatlogPipeline, "_heal_and_reprobe",
            lambda self, health: (health, ""),
        )

        row = next(r for r in sweep.run_sweep(sources=("wechat",))["sources"]
                   if r["source"] == "wechat")

        assert row["ok"] is False
        assert row["state"] == STATE_NOT_LISTENING
        assert "no_builder" not in row["detail"] and "构造得出" not in row["detail"]

    def test_collect_reports_counts_the_executor_can_add_up(self, monkeypatch):
        """一轮真采回来的四个数要能进 verdict 那句"N/M 条真跑了一轮"。"""
        from plobi.collectors.chatlog.pipeline import IngestReport

        pipe = registry.spec("wechat").builder()
        monkeypatch.setattr(
            pipe.__class__, "health",
            lambda self: {"ok": True, "detail": "chatlog /health ok", "state": "ready",
                          "binary": ""},
        )
        monkeypatch.setattr(
            pipe, "run_once",
            lambda day=None, lookback_days=1: IngestReport(
                scanned=9, skipped_duplicate=7, created=["a", "b"], updated=["c"]
            ),
        )

        result = pipe.collect()

        assert result["state"] == "ready"
        assert (result["rows_seen"], result["unchanged"]) == (9, 7)
        assert (result["created"], result["updated"]) == (2, 1)

    def test_the_sweep_does_not_branch_on_source_names(self):
        """判据 3：这一层只读注册表那两格。出现按信源名字的分支就是把它 fork 了。"""
        text = io.open(sweep.__file__, encoding="utf-8").read()

        for needle in ('if source ==', 'if sid ==', '== "zju"', '== "wechat"', '== "timetable"'):
            assert needle not in text, needle


def _raise():
    raise RuntimeError("构造不起来")


class _NamedFailure(Exception):
    def __init__(self, state: str, detail: str) -> None:
        super().__init__(detail)
        self.state = state
        self.detail = detail


class _PullOnly:
    def __init__(self, rows):
        self._rows = rows

    def pull(self, *, since=None):
        # WP-POOL：`pull()` 现在落的是**池**，而池要的是 `external_id` / `title` /
        # `evidence` 那几格。旧形状只回原始行（`[1, 2]`），因为 `ingest_occurrences`
        # 会去读 `occurrence.*`——现在换了个更挑的落点，所以这一档得给真形状。
        return [
            occurrence if isinstance(occurrence, Occurrence) else Occurrence(
                source="q",
                external_id=f"x{index}",
                title=str(occurrence),
                start_at="2026-10-19T08:00:00",
            )
            for index, occurrence in enumerate(self._rows)
        ]
