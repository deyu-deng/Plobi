"""课表这一条走池：注册表那条 builder 修好了、`ingest_occurrences` 调用点归零、失败有名字。

跑法：`bash scripts/run_tests.sh tests/plobi/test_collect_timetable_pool.py -q`（唯一入口，不传 `--basetemp`）。
零网络、零真库（日程库与信息池都钉在 `tmp_path`）。

这一档补的是 `test_timetable_ics.py` 量不到的三件事，都是这一刀的判据本身：

1. **调用点归零**用扫描钉住（判据①），不然下一个人"顺手加回待确认卡"没人拦；
2. **builder 那一格 10-20 是坏的**（`_TimetableCollector()` 少一个必填参数 ⇒ 每天那一轮报
   `build_failed: TypeError: missing 1 required positional argument: 'path'`），
   修法是读用户填的那格，**没填就抛有名状态**——不是给路径加默认值，那等于猜一个然后 0 行进账；
3. **`pull()` 一次都不许用空清单冒充"今天没课"**（判据 2 那颗雷，与 zju 同一形状）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from plobi.agenda.service import AgendaService
from plobi.collectors import registry, sources_config, sweep
from plobi.collectors.timetable import TimetableUnavailable
from plobi.collectors.timetable.collector import (
    STATE_FILE_MISSING,
    STATE_NO_PATH,
    STATE_PARSE_FAILED,
    TimetableCollector,
    note_for,
)

ICS_TWO_CLASSES = (
    "BEGIN:VCALENDAR\r\nX-WR-CALNAME:测试课表\r\nVERSION:2.0\r\n"
    "BEGIN:VEVENT\r\nUID:evt-1\r\nSUMMARY:电工电子学\r\n"
    "DTSTART:20260922T100000\r\nDTEND:20260922T114000\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nUID:evt-2\r\nSUMMARY:大学物理\r\n"
    "DTSTART:20260923T080000\r\nDTEND:20260923T094000\r\nEND:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)


@pytest.fixture()
def shared_root(tmp_path, monkeypatch):
    """把共享根钉进 tmp_path：`sources.yaml` 那一格与池都从它派生。"""
    monkeypatch.setattr("plobi_constants.get_shared_plobi_root", lambda: tmp_path)
    (tmp_path / "plobi").mkdir(parents=True, exist_ok=True)
    return tmp_path / "plobi"


@pytest.fixture()
def pool_db(tmp_path, monkeypatch):
    from plobi.pool import store as pool_store

    db = tmp_path / "pool.db"
    monkeypatch.setattr(pool_store, "path", lambda: db)
    return db


@pytest.fixture()
def ics(tmp_path) -> Path:
    path = tmp_path / "schedule.ics"
    path.write_text(ICS_TWO_CLASSES, encoding="utf-8", newline="")
    return path


class TestNoCollectorWritesTheAgenda:
    def test_timetable_package_calls_no_ingest(self) -> None:
        """判据①的钉子：`ingest_occurrences` 在本包里**一个调用点都不许有**。

        写成扫描而不是写成"我记得改完了"：这一条信源以前就是靠自觉守的，而自觉守不住
        （`sweep.py` 与 `pipeline.py` 那两处都是后来才发现一直直落日程）。
        注释与文档串里提到那个名字不算调用——所以判的是 `name(` 这一形。
        """
        package = Path(registry.spec("timetable").__module__.replace(".", "/"))
        target = Path(__file__).resolve().parents[2] / "plobi" / "collectors" / "timetable"
        assert target.is_dir(), f"找不到本包目录：{target}"

        offenders = []
        for file in sorted(target.glob("*.py")):
            for number, line in enumerate(file.read_text(encoding="utf-8").splitlines(), 1):
                if line.lstrip().startswith("#"):
                    continue
                if re.search(r"\bingest_occurrences\s*\(", line):
                    offenders.append(f"{file.name}:{number}")

        assert offenders == [], f"课表这一条又去直落日程了：{offenders}"
        assert package  # 只是把"这一族住在哪个包"说一句，不参与判定


class TestTheBuilderIsNotALie:
    def test_unconfigured_ics_path_says_why_instead_of_type_error(self, shared_root) -> None:
        """10-20 现读的坏形状：`build_failed: TypeError: missing 1 required positional argument: 'path'`。

        现在必须是**有名状态**——那句话要能在 cron 正文里被读出来，而不是一个类型错误。
        """
        with pytest.raises(TimetableUnavailable) as raised:
            registry.spec("timetable").builder()

        assert raised.value.state == STATE_NO_PATH
        assert "ics_path" in raised.value.detail

    def test_configured_path_builds_and_pulls(self, shared_root, ics) -> None:
        sources_config.set_setup_params("timetable", {"ics_path": str(ics)})

        collector = registry.spec("timetable").builder()

        assert collector.source_id == "timetable"
        assert len(collector.pull()) == 2

    def test_health_of_a_configured_but_missing_file_says_file_missing(self, tmp_path) -> None:
        health = TimetableCollector(tmp_path / "nope.ics").health()

        assert health["ok"] is False and health["state"] == STATE_FILE_MISSING


class TestPullRefusesToImpersonateAnEmptySemester:
    def test_unreadable_file_raises_named_instead_of_returning_nothing(self, tmp_path) -> None:
        with pytest.raises(TimetableUnavailable) as raised:
            TimetableCollector(tmp_path / "nope.ics").pull()

        assert raised.value.state == STATE_FILE_MISSING

    def test_a_non_calendar_raises_parse_failed_not_an_empty_list(self, tmp_path) -> None:
        bad = tmp_path / "bad.ics"
        bad.write_text("这不是日历\r\n", encoding="utf-8", newline="")

        with pytest.raises(TimetableUnavailable) as raised:
            TimetableCollector(bad).pull()

        assert raised.value.state == STATE_PARSE_FAILED

    def test_unknown_state_does_not_invent_a_sentence(self) -> None:
        assert "没记到" in note_for("made_up_state")


class TestTheRoundLandsThePool:
    def test_sweep_pulls_the_configured_file_into_the_pool_only(
        self, shared_root, pool_db, ics
    ) -> None:
        service = AgendaService(shared_root.parent / "agenda.db")
        sources_config.set_setup_params("timetable", {"ics_path": str(ics)})

        report = sweep.run_sweep(service=service, sources=("timetable",))

        row = report["sources"][0]
        assert row["ok"] is True, row["detail"]
        from plobi.pool import query as pool_query

        rows = pool_query(source="timetable", db_path=pool_db)
        assert len(rows) == 2 and {r["kind"] for r in rows} == {"class"}
        assert _agenda_events(service) == [], "一行都不许进日程库"

    def test_two_rounds_do_not_grow_the_pool(self, shared_root, pool_db, ics) -> None:
        """判据③：连着 land 两轮 `total` 不涨（幂等键是 `(source, external_id)`）。"""
        from plobi.collectors.timetable.collector import import_ics
        from plobi.pool import stats as pool_stats

        sources_config.set_setup_params("timetable", {"ics_path": str(ics)})

        first = import_ics(ics)
        second = import_ics(ics)

        assert first["created"] == 2 and second["created"] == 0
        assert second["updated"] == 0
        assert pool_stats()["total"] == 2


def _agenda_events(service: AgendaService) -> list:
    from plobi.agenda import store

    conn = store.connect(service.db_path)
    try:
        return store.list_events(conn, source="timetable", include_cancelled=True)
    finally:
        conn.close()
