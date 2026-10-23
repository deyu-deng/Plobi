"""三节课各被排了两遍——`WP-PLAN-BY-LLM` 那格的**反例件**（真库现读 2026-10-20）。

图上「一个时段并排三张卡、其中两张是空壳」的成因：秘书那一发把课表里的课**原样复述**成 `task` 块
（`evt_eccec2fa3243` class 与 `evt_2d1d0af1c4d6` task 同时段同题＝理论力学（甲）；工程训练、
电工电子学实验同样一对）。旧的边界二只看**提案内部**的两两，看不见当天已经会落成条目的那些行，
而落库前的摘除只认作息锚点（午餐／晚餐）——课这一侧没人管。

这一档钉三件事，全程假 planner、假 llm、`tmp_path` 里的 `agenda.db`：**没有一发真模型**。

1. 复述当天已有日程的那几块**不落库**（13 块 → 10 块）；
2. 骸架与秘书排的块在**数据里**分得开（`kind`／`evidence` 带得出来自课表、作息还是秘书）——
   显示面怎么分开是 `WP-PLAN-ITEM-DETAIL-FE`（Desktop 线）的事；
3. 同一枚判据也进闸（`validate_proposal` 点名给模型看，让它下一发别再交）。

跑法：``bash scripts/run_tests.sh tests/plobi/test_plan_restate_guard.py``（唯一入口，禁直调 pytest）。
"""

from __future__ import annotations

import pytest

from plobi.agenda import llm_planning, store

DAY = "2026-10-20"

#: 真库现读的骨架那一半：三门课（浙大课表，`source=timetable`）＋ 今晚那份截止。
CLASSES = [
    ("理论力学（甲）", "08:00:00", "09:35:00"),
    ("工程训练", "10:00:00", "12:25:00"),
    ("电工电子学实验", "13:25:00", "15:50:00"),
]
DDL_TITLE = "实验报告提交-实验三-叠加原理和等效电源定理验证"

#: 秘书那一发交回的 7 块（真库现读）。前三块就是把上面那三节课各复述了一遍。
PROPOSED = [
    {"title": "理论力学（甲）", "start_at": f"{DAY}T08:00:00", "end_at": f"{DAY}T09:35:00"},
    {"title": "工程训练", "start_at": f"{DAY}T10:00:00", "end_at": f"{DAY}T12:25:00"},
    {"title": "电工电子学实验", "start_at": f"{DAY}T13:25:00", "end_at": f"{DAY}T15:50:00"},
    {"title": "实验三报告：撰写并提交（今晚23:59截止）",
     "start_at": f"{DAY}T15:50:00", "end_at": f"{DAY}T18:30:00"},
    {"title": "焊接作业一（明日13:13截止）", "start_at": f"{DAY}T19:10:00", "end_at": f"{DAY}T20:50:00"},
    {"title": "实验4报告：单相交流电路与MWORKS仿真（明日23:59截止）",
     "start_at": f"{DAY}T20:50:00", "end_at": f"{DAY}T22:10:00"},
    {"title": "焊接作业二（22日08:46截止）", "start_at": f"{DAY}T22:10:00", "end_at": f"{DAY}T23:30:00"},
]
#: 那四块是真活，必须留下。
LEGIT = [item["title"] for item in PROPOSED[3:]]

REASON = "三节课是不可动的骨架原样保留，其余空白全部填实。"


@pytest.fixture()
def conn(tmp_path):
    connection = store.connect(tmp_path / "agenda.db")
    yield connection
    connection.close()


def _seed_the_real_day(conn) -> None:
    """照今天那面真库摆：三门课、一份今晚的截止。"""
    for title, start, end in CLASSES:
        store.create_event(conn, title=title, start_at=f"{DAY}T{start}", end_at=f"{DAY}T{end}",
                           kind="class", source="timetable",
                           evidence={"uid": title, "calendar_name": "浙大课程表-2026-2027秋冬"})
    store.create_event(conn, title=DDL_TITLE, start_at=f"{DAY}T23:59:00", kind="ddl",
                       status="pending", source="zju", evidence={"course": "电工电子学实验"})


def _proposal(items, reason=REASON):
    def _fn(_conn, _day):
        return items, reason
    return _fn


def _items_of(conn):
    events = store.list_events(
        conn, start_from=f"{DAY}T00:00:00", start_to=f"{DAY}T23:59:59")
    assert events, "秘书这一发没落到 events"
    return [
        {"title": event.title, "kind": event.kind, "start_at": str(event.start_at),
         "end_at": str(event.end_at), "evidence": dict(event.evidence or {})}
        for event in events
        if str(event.start_at)[:10] == DAY
    ]


class TestTheCounterExample:
    def test_the_three_repeated_classes_are_not_written_twice(self, conn):
        """真今天那一发原样重放（假模型，零花费）：13 块变 10 块，复述那三块不落库。"""
        _seed_the_real_day(conn)

        result = llm_planning.plan_and_store_day(DAY, conn=conn, proposal_fn=_proposal(PROPOSED))

        assert result["ok"] is True, result
        rows = _items_of(conn)
        assert len(rows) == 8, f"课 3＋截止 1＋秘书 4 应为 8，现读 {len(rows)}"
        for title, _s, _e in CLASSES:
            assert sum(1 for r in rows if r["title"] == title) == 1, f"{title} 还是排了两遍"
        assert llm_planning.duplicate_slot_titles(rows) == []

    def test_the_four_real_blocks_survive_and_keep_their_provenance(self, conn):
        _seed_the_real_day(conn)

        llm_planning.plan_and_store_day(DAY, conn=conn, proposal_fn=_proposal(PROPOSED))

        rows = _items_of(conn)
        for title in LEGIT:
            block = [r for r in rows if r["title"] == title]
            assert len(block) == 1, title
            assert block[0]["evidence"].get("planner") == "l1", "秘书排的块要认得出是它排的"

    def test_dropping_is_loud_not_silent(self, conn, caplog):
        _seed_the_real_day(conn)

        with caplog.at_level("WARNING", logger="plobi.agenda.llm_planning"):
            llm_planning.plan_and_store_day(DAY, conn=conn, proposal_fn=_proposal(PROPOSED))

        logged = " ".join(record.getMessage() for record in caplog.records)
        assert "复述当天已有日程" in logged, "静默摘行会把「模型交错了」伪装成「今天就这么排」"
        assert "理论力学（甲）" in logged


class TestTheEventRowsStaySeparate:
    """课表行与秘书排的块在**数据里**分得开（显示面那一半归 `WP-PLAN-ITEM-DETAIL-FE`）。"""

    def test_each_origin_is_readable_from_kind_and_evidence(self, conn):
        _seed_the_real_day(conn)

        llm_planning.plan_and_store_day(DAY, conn=conn, proposal_fn=_proposal(PROPOSED))

        rows = _items_of(conn)
        classes = [r for r in rows if r["kind"] == "class"]
        blocks = [r for r in rows if r["evidence"].get("planner") == "l1"]

        assert len(classes) == 3 and len(blocks) == 4
        # 课表行来自材料，不是秘书排的（不许"交给 L1 自己想"）：它不带 planner=l1 那种署名
        assert all("planner" not in r["evidence"] for r in classes)


class TestTheGateNamesItForTheModel:
    def test_validate_proposal_flags_the_restatement(self, conn):
        """同一枚判据要能让模型下一发别再交——只摘不说是把错吞进库里。"""
        _seed_the_real_day(conn)
        materials = llm_planning.plan_materials(conn, DAY)

        problems = llm_planning.validate_proposal(
            PROPOSED, scheduled=materials["today_items"])

        named = [p for p in problems if "又排了一遍" in p]
        assert len(named) == 3, problems

    def test_a_wider_span_with_the_same_title_is_not_an_echo(self, conn):
        """全等才算复述。比课宽一段那是**占了别的时间**，归边界二/重叠那一族管，不许在这里悄悄吞掉。"""
        _seed_the_real_day(conn)
        scheduled = llm_planning.plan_materials(conn, DAY)["today_items"]

        wide = {"title": "理论力学（甲）", "start_at": f"{DAY}T08:00:00", "end_at": f"{DAY}T10:00:00"}

        assert not any(llm_planning.restates(wide, row) for row in scheduled)

    def test_a_clean_proposal_still_passes(self, conn):
        """闸没被调严到拒一切：不复述的那一版照常过，四块全落。"""
        _seed_the_real_day(conn)

        result = llm_planning.plan_and_store_day(DAY, conn=conn, proposal_fn=_proposal(PROPOSED[3:]))

        assert result["ok"] is True, result
        rows = _items_of(conn)
        assert len([r for r in rows if r["evidence"].get("planner") == "l1"]) == 4
