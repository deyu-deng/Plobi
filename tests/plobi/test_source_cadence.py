"""每源一行采集频率（裁定 191 二）：声明、回落、到点判定，和"没到点也要说得出"。

跑法：`bash scripts/run_tests.sh tests/plobi/test_source_cadence.py -q`（唯一入口，不传 `--basetemp`）。
零真网络、零真库：配置与台账都钉在 `tmp_path` 里。

这一档要钉住的不是"能不能算 cron"（那是 `cron/jobs.py` 早就有的手），是四件事：
① 没声明 ⇒ **今天的行为一个字不变**（每轮都打），别把新格子做成一次静默的改版；
② 用户那一句**赢**代码里那一句，而且面上要说得出是谁赢（`cadence_origin`）；
③ 写坏的频率**不打**，而不是"回落成每轮都打"——后者会让一个拼错的 `0 3 * *` 每天稳定碰一次学校；
④ 没到点的那一条**留在读数里**，判据 1 要的就是这一行证据：那一轮确实没打它，且说得出下一次。
"""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from plobi.collectors import cadence, registry
from plobi.collectors.registry import SourceSpec

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts" / "plobi"


def load_cron_script():
    """那一轮 `plobi-source-collect` 的本体（`scripts/plobi/cron_collect_sweep.py`）。

    按文件路径加载而不是 import：脚本不在包里，而**判据 1 要交的正是那一轮的读数**，
    拿函数当替身去测就等于没测触发方。
    """
    spec = importlib.util.spec_from_file_location("cron_collect_sweep",
                                                  SCRIPTS / "cron_collect_sweep.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

CAPS = {
    "history_backfill": False, "incremental_since": False, "supports_media": False,
    "requires_manual_setup": False, "authoritative_replace": False,
}

#: 昨天那一轮全局作业打过的时刻（带偏移写死：`run_tests.sh` 把 TZ 钉成 UTC，
#: 不带偏移的话这两句会被解析成 UTC，`0 3 * * *` 的"下一次"就会差 8 小时）。
LAST_MORNING_ROUND = "2026-10-20T08:15:00+08:00"
JUST_AFTER = datetime.fromisoformat("2026-10-20T08:16:00+08:00")


@pytest.fixture()
def shared_root(tmp_path, monkeypatch):
    """把共享根钉进 tmp_path：`sources.yaml` 与频率台账都从它派生。

    非钉不可 —— 这两个读侧函数**会写库**（`land` / `save` 那一路），跑测试碰到真家目录
    就是拿用户的真配置当测试台。
    """
    monkeypatch.setattr("plobi_constants.get_shared_plobi_root", lambda: tmp_path)
    (tmp_path / "plobi").mkdir(parents=True, exist_ok=True)
    return tmp_path / "plobi"


@pytest.fixture()
def fake_sources():
    registered: list[str] = []

    def _add(*specs: SourceSpec) -> None:
        for spec in specs:
            registry.register(spec, replace=True)
            registered.append(spec.source_id)

    yield _add
    for source_id in registered:
        registry.unregister(source_id)


def write_cadence(shared_root: Path, source_id: str, value: str) -> None:
    """把用户那一格写进 `sources.yaml`（等价于设置面上按那一下 Save）。"""
    import yaml

    path = shared_root / "sources.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else None
    data = data or {}
    sources = data.setdefault("sources", {})
    sources.setdefault(source_id, {})["cadence"] = value
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


class TestTheDefaultIsTodayBehaviour:
    def test_an_undeclared_source_is_due_every_round(self, shared_root, fake_sources) -> None:
        fake_sources(SourceSpec("perround", CAPS, category="custom"))

        item = cadence.reading("perround", now=JUST_AFTER)

        assert item["due"] is True and item["state"] == "per_round"
        assert item["cadence"] == "" and item["cadence_origin"] == "per_round"

    def test_a_declared_default_wins_over_the_per_round_fallback(
        self, shared_root, fake_sources
    ) -> None:
        fake_sources(SourceSpec("declared", CAPS, category="custom", cadence="every 6h"))

        item = cadence.reading("declared", now=JUST_AFTER)

        assert item["cadence_origin"] == "spec", "代码里的声明要有名字，不然查不到是谁给的"
        # 没有台账 = 这台还没打过它 ⇒ 先打一次，之后才按频率。
        assert item["due"] is True and item["state"] == "first_run"


class TestTheUserSentenceWins:
    def test_config_beats_the_declaration(self, shared_root, fake_sources) -> None:
        fake_sources(SourceSpec("both", CAPS, category="custom", cadence="every 6h"))
        write_cadence(shared_root, "both", "0 3 * * *")

        item = cadence.reading("both", now=JUST_AFTER, ledger={"both": {"last_run_at": LAST_MORNING_ROUND}})

        assert item["cadence"] == "0 3 * * *" and item["cadence_origin"] == "config"

    def test_daily_3am_is_skipped_by_the_morning_round(
        self, shared_root, fake_sources
    ) -> None:
        """判据 1 的正半句：设成"每天 03:00"的那一条，08:15 那一轮**不许碰它**。"""
        fake_sources(SourceSpec("nightly", CAPS, category="custom"))
        write_cadence(shared_root, "nightly", "0 3 * * *")

        item = cadence.reading("nightly", now=JUST_AFTER,
                               ledger={"nightly": {"last_run_at": LAST_MORNING_ROUND}})

        assert item["due"] is False and item["state"] == "not_due"
        nxt = datetime.fromisoformat(item["next_run_at"])
        assert nxt > JUST_AFTER, f"下一次必须排在now之后，读到 {item['next_run_at']}"
        assert nxt - JUST_AFTER <= __import__("datetime").timedelta(hours=24), "每天一格 ⇒ 不会推到明天之后"

    def test_the_skipped_one_still_gets_a_row_in_the_round(
        self, shared_root, fake_sources
    ) -> None:
        """没到点**不是失败**，但也不许消失：那一轮的正文里要有它自己一行。

        判据 1 交的就是这两行：设成 03:00 的那条本轮没被打（有它自己的读数与下一次），
        没设的那条照样被打到。
        """
        fake_sources(SourceSpec("nightly", CAPS, category="custom"),
                     SourceSpec("always", CAPS, category="custom"))
        write_cadence(shared_root, "nightly", "0 3 * * *")
        cadence.mark_run("nightly", state="ready", now=JUST_AFTER)

        due, skipped = cadence.partition(("nightly", "always"), now=JUST_AFTER)

        assert due == ("always",), "另一条没设频率 ⇒ 这一轮该打的是它"
        assert [row["source"] for row in skipped] == ["nightly"]
        row = skipped[0]
        assert row["ok"] is True and row["state"] == "not_due"
        assert row["cadence"] == "0 3 * * *"
        assert datetime.fromisoformat(row["next_run_at"]) > JUST_AFTER


class TestABrokenSentenceDoesNotQuietlyHammer:
    def test_declaration_layer_refuses_an_unparseable_cadence(self) -> None:
        with pytest.raises(registry.UnknownCadence):
            SourceSpec("typo", CAPS, category="custom", cadence="0 3 *")

    def test_a_bad_value_in_the_config_layer_stops_that_source(
        self, shared_root, fake_sources
    ) -> None:
        """设置面写进去的东西**不经过**声明层那颗构造检查，所以运行时也必须有牙。

        这里要的形状是"不打 + 说得出为什么"，而不是"当没设 ⇒ 每轮都打"：后者等于把一个
        拼错的字符变成一次每天一次的稳定外部调用，而且面上什么都看不到。
        """
        fake_sources(SourceSpec("typo", CAPS, category="custom"))
        write_cadence(shared_root, "typo", "0 3 *")

        item = cadence.reading("typo", now=JUST_AFTER, ledger={"typo": {"last_run_at": LAST_MORNING_ROUND}})

        assert item["due"] is False and item["state"] == "invalid_cadence"
        assert "不认识" in item["note"]

    def test_a_one_shot_cadence_says_it_will_never_fire_again(
        self, shared_root, fake_sources
    ) -> None:
        fake_sources(SourceSpec("oneshot", CAPS, category="custom"))
        write_cadence(shared_root, "oneshot", "30m")

        item = cadence.reading("oneshot", now=JUST_AFTER, ledger={"oneshot": {"last_run_at": LAST_MORNING_ROUND}})

        assert item["due"] is False and item["state"] == "exhausted"
        assert "不会再触发" in item["note"], "这不是故障，是一句说清楚的话"


class TestTheLedgerItself:
    def test_mark_run_is_what_makes_the_next_round_respect_the_cadence(
        self, shared_root, fake_sources
    ) -> None:
        fake_sources(SourceSpec("hourly", CAPS, category="custom"))
        write_cadence(shared_root, "hourly", "every 6h")

        cadence.mark_run("hourly", state="ready", now=JUST_AFTER)

        item = cadence.reading("hourly", now=JUST_AFTER)
        assert item["due"] is False and item["state"] == "not_due"

        later = datetime.fromisoformat("2026-10-20T14:16:00+08:00")
        assert cadence.reading("hourly", now=later)["due"] is True, "6 小时之后该再碰它一次"

    def test_a_failed_round_still_counts_as_a_round(
        self, shared_root, fake_sources
    ) -> None:
        """频率问的是"多久碰它一次"，不是"碰成功没"。

        失败也记账，才有"学校挂了不会变成每 15 分钟重试一次"这一条；碰没碰成在每一轮
        自己的读数里，不该由频率这一格重新决定。
        """
        fake_sources(SourceSpec("broken", CAPS, category="custom"))
        write_cadence(shared_root, "broken", "every 6h")

        cadence.mark_run("broken", state="no_credential", now=JUST_AFTER)

        assert cadence.reading("broken", now=JUST_AFTER)["due"] is False

    def test_a_corrupt_ledger_collects_anyway(self, shared_root, fake_sources) -> None:
        """台账坏了 ⇒ 按"没打过"处理，而不是把整条采集链停掉。"""
        fake_sources(SourceSpec("weasy", CAPS, category="custom"))
        write_cadence(shared_root, "weasy", "0 3 * * *")
        cadence.ledger_path().write_text("{ 这不是 json", encoding="utf-8")

        item = cadence.reading("weasy", now=JUST_AFTER)

        assert item["due"] is True and item["state"] == "ledger_unreadable"

    def test_the_settings_reading_does_not_step_on_the_liveness_state(
        self, shared_root, fake_sources
    ) -> None:
        """`cadence_env()` 的键**不叫 `state`**：那一格是活性探针的（live / stale / never）。

        同名覆盖一次，设置面那句"这条信源还活着吗"就变成"到点没"——两个不同的问题共用
        一个键，是这一层最容易犯也最难查的错。
        """
        fake_sources(SourceSpec("wechat", CAPS, category="social"))

        env = cadence.cadence_env("wechat")

        assert "state" not in env and env["cadence_state"] == "per_round"


class TestThrottleAndCadenceAreTwoDifferentThings:
    def test_the_throttle_slot_is_still_there(self) -> None:
        """裁定 191 写得明明白白：节流那一格**保留不动**。

        这一条不是给现在的人看的，是给下一个"顺手简化一下"的人看的：把
        `min_minutes_between_pulls` 删掉去"用频率统一管"，等于把"最多多快碰学校一次"
        那把闸拆了——而频率是用户设的，用户设成每分钟一次就会每分钟打一遍学校。
        """
        from plobi.collectors.zju.config import ZjuConfig

        config = ZjuConfig()

        assert config.min_minutes_between_pulls > 0
        assert "cadence" in SourceSpec.__dataclass_fields__, "两把闸各自有一格，谁也不在原位上代替谁"

    def test_a_source_that_writes_no_cadence_code_still_gets_the_slot(self) -> None:
        """通用性那一格（用户 10-21 问的就是这个）：**以后加信源不许再写一遍频率代码**。

        判据不是"四条现存信源都声明了"（那正是要求新来的人复刻样板的形状），而是
        **一条什么都没写的包也有那一格**；顺带钉两条反向的：自己声明过的不会被加两遍、
        真源只有一处（label 与 help 在注册表那一个常量上）。
        """
        caps = {k: False for k in registry.CAPABILITY_KEYS}
        third_party = SourceSpec("someone_elses_package", caps, category="custom")

        keys = [field.key for field in third_party.setup_fields]
        assert keys == ["cadence"], f"新包什么都没写就该白得一格，读到 {keys}"
        assert third_party.setup_fields[0] is registry.CADENCE_FIELD, "label/help 只有一份真源"

        declared = SourceSpec("declared_once", caps, category="custom",
                              setup_fields=(registry.CADENCE_FIELD,))
        assert [f.key for f in declared.setup_fields] == ["cadence"], "不许加两遍"

        for source_id in registry.source_ids():
            assert "cadence" in [f.key for f in registry.spec(source_id).setup_fields]


class TestThatIsWhatTheDailyRoundDoes:
    """判据 1 的机器那一半：**跑一轮 `plobi-source-collect`**，看它打谁、不打谁。

    这三条钉的不是 `cadence` 那一层会不会算 cron（上面钉过了），是**触发方真的读了那一层**——
    否则频率就只是设置里摆着好看的一格，而那正是裁定 191 要防的形状。
    """

    def _stub(self, monkeypatch, *, rows, calls):
        """替身只换**执行方**那一层；`cadence` 与分流都是真的在跑。

        必须同时钉住 `cron/jobs.py::_plobi_now`：那一轮问"到点了没"用的是**配置时钟**，
        而 `run_tests.sh` 把 TZ 钉成 UTC ⇒ 不钉的话"每天 03:00"会被算成 `03:00+00:00`，
        这台真实时刻一过它就真的到点了（红过一次的正是这个，不是守卫错）。
        守卫那条「没有一条信源被点亮 ⇒ 出声」保持原样，测试自己把源点亮。
        """
        def stub(*, service=None, sources=None):
            calls.append(tuple(sources or ()))
            return {"sources": rows, "ran": len([r for r in rows if r["ok"]]),
                    "total": len(rows), "verdict": "1 / 1 条真跑了一轮：always"}

        monkeypatch.setattr("plobi.collectors.sweep.run_sweep", stub)
        monkeypatch.setattr("cron.jobs._plobi_now", lambda: JUST_AFTER)

    def test_the_morning_round_hits_only_the_due_one(
        self, shared_root, fake_sources, monkeypatch, capsys
    ) -> None:
        module = load_cron_script()
        fake_sources(SourceSpec("nightly", CAPS, category="custom"),
                     SourceSpec("always", CAPS, category="custom"))
        write_cadence(shared_root, "nightly", "0 3 * * *")
        cadence.mark_run("nightly", state="ready", now=JUST_AFTER)
        monkeypatch.setattr(registry, "collecting_sources", lambda: ("nightly", "always"))
        calls: list[tuple[str, ...]] = []
        self._stub(monkeypatch, calls=calls,
                   rows=[{"source": "always", "ok": True, "state": "ready", "detail": "进去 1 条新"}])

        rc = module.main()

        assert rc == 0
        assert calls == [("always",)], "执行方只拿到该打的那一条 —— 频率被读进去了，不是摆设"
        out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert [row["source"] for row in out["sources"]] == ["always", "nightly"], "没打的那条也在正文里"
        skipped = out["sources"][1]
        assert skipped["ok"] is True and skipped["state"] == "not_due"
        assert skipped["cadence"] == "0 3 * * *"
        assert datetime.fromisoformat(skipped["next_run_at"]) > JUST_AFTER
        assert timedelta(0) < datetime.fromisoformat(skipped["next_run_at"]) - JUST_AFTER <= timedelta(hours=24)
        assert out["attempted"] == 1 and out["ran"] == 1

    def test_a_round_where_nobody_is_due_is_not_a_failure(
        self, shared_root, fake_sources, monkeypatch, capsys
    ) -> None:
        """全没到点 ⇒ 退出码 0。把它报成红灯就是每天给用户发一次假事故。"""
        module = load_cron_script()
        fake_sources(SourceSpec("nightly", CAPS, category="custom"))
        write_cadence(shared_root, "nightly", "0 3 * * *")
        cadence.mark_run("nightly", state="ready", now=JUST_AFTER)
        monkeypatch.setattr(registry, "collecting_sources", lambda: ("nightly",))
        calls: list[tuple[str, ...]] = []
        self._stub(monkeypatch, calls=calls, rows=[])

        assert module.main() == 0
        assert calls == [], "一条都没到点 ⇒ 连执行方都不该被叫"
        out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert out["ran"] == 0 and out["attempted"] == 0 and out["total"] == 1
        assert "没到点" in out["verdict"] and "nightly" in out["verdict"]

    def test_a_failed_round_still_counts_as_a_round(
        self, shared_root, fake_sources, monkeypatch, capsys
    ) -> None:
        """打失败 ⇒ 退出码照旧是 1，但**这一轮碰过它了要记账**：否则学校一挂，
        下一次醒来的还会立刻再打一遍，频率那一格就白设了。"""
        module = load_cron_script()
        fake_sources(SourceSpec("broken", CAPS, category="custom"))
        write_cadence(shared_root, "broken", "every 6h")
        monkeypatch.setattr(registry, "collecting_sources", lambda: ("broken",))
        self._stub(monkeypatch, calls=[],
                   rows=[{"source": "broken", "ok": False, "state": "no_credential",
                          "detail": "没配凭据"}])

        assert module.main() == 1
        assert cadence.reading("broken", now=JUST_AFTER)["due"] is False
        captured = capsys.readouterr()
        assert '"no_source_ran"' in captured.out and '"no_source_ran"' in captured.err
