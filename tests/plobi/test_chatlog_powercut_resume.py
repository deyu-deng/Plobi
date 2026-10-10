"""判据 6：断电不坏库 —— 采集写库路径要能**中断续跑**，重启后不许出现半条脏记录。

跑法：``bash scripts/run_tests.sh tests/plobi/test_chatlog_powercut_resume.py -q -j 4``
（唯一入口、不传 ``--basetemp``，裁定 87）。

"被杀"用真异常模拟（在写入那一层抛，等于进程在那一步没活下来），不靠 sleep 也不靠真断电。
钉的是三件事：① 杀在**写之前**不许把消息记成已处理（那是永久丢一条的形状）；
② 杀在**写之后**重跑不许多一条（落点按 (source, uid/msg_id) 幂等）；
③ 重开库以后**每行都是完整行**，且 `PRAGMA integrity_check` 说 ok。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from plobi.agenda.service import AgendaService
from plobi.agenda.store import AgendaValidationError
from plobi.collectors.chatlog import pipeline as pipeline_module
from plobi.collectors.chatlog.client import ChatMessage
from plobi.collectors.chatlog.config import CollectorConfig
from plobi.collectors.chatlog.confirm import HeuristicConfirmer
from plobi.collectors.chatlog.pipeline import ChatlogPipeline, IngestReport
from plobi.collectors.chatlog.state import SeenStore, TalkerStore

NOW = datetime(2026, 10, 12, 9, 0)


class PowerCut(BaseException):
    """进程在那一步没活下来——比 Exception 更狠一层，防止被顺手吞掉。"""


def message(content: str = "明天上午十点在新大楼答辩", *, msg_id: str = "m1") -> ChatMessage:
    return ChatMessage(
        msg_id=msg_id,
        talker="班级群",
        sender="导师",
        sent_at="2026-10-11 20:00:00",
        content=content,
    )


def make_pipeline(tmp_path, *, real_ingest=None):
    service = AgendaService(tmp_path / "agenda.db")
    pipeline = ChatlogPipeline(
        config=CollectorConfig(mode="whitelist", talkers=["班级群"], enabled=True),
        client=type("NoClient", (), {"list_talkers": lambda *a, **k: [], "fetch": lambda *a, **k: []})(),
        service=service,
        seen=SeenStore(tmp_path / "seen.db"),
        talkers=TalkerStore(tmp_path / "talkers.db"),
        confirmer=HeuristicConfirmer(now_factory=lambda: NOW),
    )
    return pipeline, service


def run_ingest_step(pipeline, *, die_after: bool):
    """在写入那一层"断电"：die_after=False 是还没写就死，True 是写完才死。"""
    real = pipeline_module.ingest_occurrences

    def fake(service, items, **kwargs):
        if not die_after:
            raise PowerCut("killed before anything was written")
        result = real(service, items, **kwargs)
        raise PowerCut("killed right after the write committed")

    return fake


class TestKilledBeforeTheWrite:
    def test_message_is_not_burned_as_seen(self, tmp_path, monkeypatch) -> None:
        pipeline, service = make_pipeline(tmp_path)
        monkeypatch.setattr(
            pipeline_module,
            "ingest_occurrences",
            run_ingest_step(pipeline, die_after=False),
        )

        with pytest.raises(PowerCut):
            pipeline.handle_message(message(), IngestReport())

        # 旧形状在这里已经把 msg_id 记进 seen 了 ⇒ 重启后当重复跳过，整条永久丢。
        assert pipeline.seen.already_seen("m1") is False
        assert service.list_pending() == []

    def test_the_same_pull_lands_after_restart(self, tmp_path, monkeypatch) -> None:
        pipeline, service = make_pipeline(tmp_path)
        monkeypatch.setattr(
            pipeline_module,
            "ingest_occurrences",
            run_ingest_step(pipeline, die_after=False),
        )

        with pytest.raises(PowerCut):
            pipeline.handle_message(message(), IngestReport())
        monkeypatch.undo()
        report = IngestReport()
        pipeline.handle_message(message(), report)

        assert len(report.created) == 1, "中断续跑：重启后同一条消息必须真能落库"
        assert pipeline.seen.already_seen("m1") is True


class TestKilledAfterTheWrite:
    def test_rerun_does_not_duplicate_the_event(self, tmp_path, monkeypatch) -> None:
        pipeline, service = make_pipeline(tmp_path)
        monkeypatch.setattr(
            pipeline_module,
            "ingest_occurrences",
            run_ingest_step(pipeline, die_after=True),
        )

        with pytest.raises(PowerCut):
            pipeline.handle_message(message(), IngestReport())

        # 写成功了但 seen 没记上 —— 这是 at-least-once 的窗口，重跑必须幂等。
        assert len(service.list_pending()) == 1
        monkeypatch.undo()
        report = IngestReport()
        pipeline.handle_message(message(), report)

        assert len(service.list_pending()) == 1, "重跑多出一条 = 落点不按 (source, uid) 幂等"
        assert report.created == [], "已存在的那条不该再算 created"
        assert pipeline.seen.already_seen("m1") is True


class TestLibraryStaysWhole:
    def test_no_half_row_and_the_db_is_consistent(self, tmp_path, monkeypatch) -> None:
        pipeline, service = make_pipeline(tmp_path)

        pipeline.handle_message(message("明天上午十点在新大楼答辩", msg_id="m0"), IngestReport())
        pipeline.handle_message(message("后天下午三点组会", msg_id="m1"), IngestReport())
        monkeypatch.setattr(
            pipeline_module,
            "ingest_occurrences",
            run_ingest_step(pipeline, die_after=False),
        )
        with pytest.raises(PowerCut):
            pipeline.handle_message(message("周五晚上七点聚餐", msg_id="m2"), IngestReport())

        rows = service.list_pending()
        assert [row.evidence["msg_id"] for row in rows] == ["m0", "m1"]
        for row in rows:
            assert row.title and row.start_at and row.evidence["snippet"], "半条脏记录"
            assert row.source == "wechat"

        import sqlite3

        conn = sqlite3.connect(str(tmp_path / "agenda.db"))
        try:
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            conn.close()
        # 被杀那一条既不在库里、也不在 seen 里 —— 状态一致，可以续跑。
        assert pipeline.seen.already_seen("m2") is False


class TestTheOldDedupeGateStillWorks:
    def test_already_processed_message_is_skipped(self, tmp_path) -> None:
        pipeline, service = make_pipeline(tmp_path)
        pipeline.handle_message(message(), IngestReport())
        report = IngestReport()
        pipeline.handle_message(message(), report)

        assert report.skipped_duplicate == 1
        assert len(service.list_pending()) == 1


def test_rejects_an_unknown_source_id_as_before() -> None:
    """词表换成注册表派生之后，非法信源仍然要被拒（判据 3 的另一半：不是放开成自由串）。"""
    from plobi.agenda import store as agenda_store

    connection = agenda_store.connect(":memory:")
    agenda_store.init_db(connection)
    with pytest.raises(AgendaValidationError):
        agenda_store.create_event(
            connection,
            title="不该存在的信源",
            start_at="2026-10-12T10:00:00",
            kind="task",
            status="pending",
            source="totally-unknown-source",
        )
