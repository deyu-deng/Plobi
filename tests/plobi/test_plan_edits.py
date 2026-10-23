"""R-105 / `WP-PLAN-CHAT-ADJUST`：日程要能用**对话**改，而且只改被点名的那一条。

计划层 10-22 退役后，被点名的那块就是 `events` 里真实的一行。两条测试钉的是判据①②③
的形状，都从**秘书那张嘴**进（`run_secretary_ask` + `action=move_block`），不从库函数进
——判据①写的是「在同一趟对话里说…之后再读那天」，只测库等于没测那条嘴。

1. 说「晚上有课，把七点那块挪到九点」⇒ **再读那天**那条真在 21:00；
   `evidence` 说得出谁改的、从哪儿挪来的；**其余那些行逐字节没动**（不许顺手重排整份）。
2. 挪到会把午饭挤掉的位置 ⇒ 一句人话原因 + **原日程一字未改**；指不到那条也是问回去，
   不猜（假成功比失败难查）。

零额度：全程写 tmp 的 agenda.db，不打模型、不碰真库。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from plobi.agenda import store
from plobi.agenda.service import AgendaService
from plobi.agents.registry import run_secretary_ask
from plobi.collectors.chatlog.client import ChatlogUnavailable
from plobi.collectors.chatlog.config import CollectorConfig
from plobi.collectors.chatlog.confirm import HeuristicConfirmer
from plobi.collectors.chatlog.pipeline import ChatlogPipeline
from plobi.collectors.chatlog.state import SeenStore, TalkerStore

DAY = (datetime.now().date() + timedelta(days=1)).isoformat()


@pytest.fixture()
def secretary_home(tmp_path, monkeypatch):
    """把 PLOBI_HOME 钉到 tmp：秘书那一趟的记录也写在 tmp 里，不碰真家。"""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    plobi = tmp_path / ".plobi"
    plobi.mkdir()
    monkeypatch.setenv("PLOBI_HOME", str(plobi))
    monkeypatch.setenv("PLOBI_PROJECTS_CONFIG", str(plobi / "plobi" / "projects.yaml"))
    monkeypatch.delenv("PLOBI_MODELS_CONFIG", raising=False)
    return tmp_path


class _DeadChatlog:
    """采集死了也不许挡住一次对话改计划（写口一律不碰 chatlog）。"""

    def healthy(self) -> bool:
        return False

    def fetch(self, talker, day=None):
        raise ChatlogUnavailable("boom")

    def list_talkers(self, keyword="", limit=10000):
        raise ChatlogUnavailable("boom")


@pytest.fixture()
def pipeline(secretary_home, tmp_path):
    service = AgendaService(tmp_path / "agenda.db")
    return ChatlogPipeline(
        config=CollectorConfig(talkers=["班级群"], enabled=True),
        client=_DeadChatlog(),
        service=service,
        seen=SeenStore(tmp_path / "seen.db"),
        talkers=TalkerStore(tmp_path / "talkers.db"),
        confirmer=HeuristicConfirmer(),
    )


def _seed(conn):
    """三条互不重叠的日程（白天 07–15 点），就是夜间秘书落的那种行。"""
    rows = [
        ("evt_plan_edit_1", "推进 · Resonote", f"{DAY}T07:00:00", f"{DAY}T08:00:00",
         {"kind": "project_block", "source": "project:Resonote", "created_by": "planner"}),
        ("evt_plan_edit_2", "写作业 · 第5次课后作业", f"{DAY}T09:00:00", f"{DAY}T10:00:00",
         {"kind": "ddl_block", "source": "ddl:evt_hw5", "created_by": "planner"}),
        ("evt_plan_edit_3", "推进 · Wealth-Lab", f"{DAY}T14:00:00", f"{DAY}T15:00:00",
         {"kind": "project_block", "source": "project:Wealth-Lab", "created_by": "planner"}),
    ]
    for event_id, title, start, end, evidence in rows:
        store.create_event(conn, event_id=event_id, title=title, start_at=start,
                           end_at=end, kind="task", source="manual", evidence=evidence)
    return {event_id for event_id, *_rest in rows}


def _rows(conn):
    """整行逐字节（含 evidence）——「其余行没动」只能是这个口径，不是只比钟点。"""
    events = store.list_events(
        conn, start_from=f"{DAY}T00:00:00", start_to=f"{DAY}T23:59:59")
    return {event.id: event.to_dict() for event in events}


def _move(pipeline, **kwargs):
    return run_secretary_ask("mutate_agenda", kwargs.pop("user_text", "晚上有课，把七点那块挪到九点"),
                             pipeline=pipeline, action="move_block", **kwargs)


def test_a_spoken_move_lands_on_the_plan_and_touches_nothing_else(pipeline):
    """判据①②：对话说完，**再读那份计划**那块真在九点；其余行逐字节不变；说得出谁改的。"""
    conn = store.connect(pipeline.service.db_path)
    try:
        store.init_db(conn)
        _seed(conn)
        before = _rows(conn)

        out = _move(pipeline, title="推进 · Resonote", start_at=f"{DAY}T21:00:00")

        assert out["ok"] is True, out
        after = _rows(conn)
        assert after["evt_plan_edit_1"]["start_at"] == f"{DAY}T21:00:00"
        assert after["evt_plan_edit_1"]["end_at"] == f"{DAY}T22:00:00", "不给 end_at ⇒ 保留原时长，不编"
        # 其余两块一个字节都没动（没有"顺手重排整份"）
        assert after["evt_plan_edit_2"] == before["evt_plan_edit_2"] and after["evt_plan_edit_3"] == before["evt_plan_edit_3"]
        # 面上说得出「改了哪一块、从几点到几点、谁改的」
        assert out["changed"] == {
            "title": "推进 · Resonote", "from": "07:00-08:00", "to": "21:00-22:00",
            "start_at": f"{DAY}T21:00:00", "end_at": f"{DAY}T22:00:00",
            "by": "l1:secretary", "reason": "晚上有课，把七点那块挪到九点",
        }
        assert "推进 · Resonote" in out["say"] and "21:00-22:00" in out["say"]
        assert "其余 2 条一个字没动" in out["say"]
        # 留痕写进了那一行自己（day_surface 把 evidence 带出去，界面上读的就是它）
        assert after["evt_plan_edit_1"]["evidence"]["edited_by"] == "l1:secretary"
        assert after["evt_plan_edit_1"]["evidence"]["moved_from"] == "07:00-08:00"
        assert after["evt_plan_edit_1"]["evidence"]["source"] == "project:Resonote", "原有证据不许被改没"
    finally:
        conn.close()


def test_a_move_that_double_books_is_refused_in_plain_words_and_writes_nothing(pipeline):
    """判据③ + 「不许假成功」：与已排块双订 / 指不到那块 ⇒ 一句人话 + 原计划一字未改。"""
    conn = store.connect(pipeline.service.db_path)
    try:
        store.init_db(conn)
        _seed(conn)
        before = _rows(conn)

        squeezed = _move(pipeline, user_text="下午那块挪到九点半",
                         title="推进 · Wealth-Lab",
                         start_at=f"{DAY}T09:30:00", end_at=f"{DAY}T10:30:00")
        assert squeezed["ok"] is False, squeezed
        assert "双订" in squeezed["error"], squeezed
        assert _rows(conn) == before, "改不动的时候一个字节都不许写"

        missing = _move(pipeline, user_text="把没排过的那块挪走", title="不存在的块",
                        start_at=f"{DAY}T21:00:00")
        assert missing["ok"] is False and "找不到" in missing["error"]
        assert _rows(conn) == before

        no_time = _move(pipeline, user_text="那块往后挪挪", title="推进 · Resonote")
        assert no_time["ok"] is False and "start_at" in no_time["error"], "缺新钟点问回去，不编"
        assert _rows(conn) == before
    finally:
        conn.close()
