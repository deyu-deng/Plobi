"""无主 DDL 的作业块（WP-SCHEDULE-REAL-PLAN 的断点修法）。

治理线 23:5x 报的断点是数据侧对、下游挂不上：48 条学在浙大作业的标题是课件名，
项目名册里 11 个全是代码项目 ⇒ C5 那条"标题含项目名"的关联口径永远命中 0，
用户那句"按未提交作业排写作业时间"就不成立。这一档测的是**修好之后的关系**：
计划里出现一块**指回那条作业**的时间，而不是"库里多了行"。

跑法：``bash scripts/run_tests.sh tests/plobi/test_planning_ddl_blocks.py -q``（唯一入口，不传 ``--basetemp``）。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from plobi.agenda import store
from plobi.agenda.planning import (
    DDL_BLOCK_EVIDENCE_KIND,
    PROJECT_BLOCK_EVIDENCE_KIND,
    _matches_project,
    _next_ddl,
    generate_evening_plan,
)

DAY = "2026-10-11"
SCHOOL_COURSE = "大学物理"


@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "agenda.db"


@pytest.fixture()
def conn(db_path):
    connection = store.connect(db_path)
    yield connection
    connection.close()


@pytest.fixture()
def bare_registry(tmp_path, monkeypatch):
    """一个 pace 项目都没有的名册（这台的真名册 11 条全是代码项目，正是断点的现场）。"""
    from plobi.agents.registry import AgentEntry, AgentRegistry

    registry = AgentRegistry(path=tmp_path / "projects.yaml")
    registry.upsert(AgentEntry.from_dict("Plobi", {"role": "l2_project", "description": "代码项目"}))
    monkeypatch.setattr("plobi.agents.registry.load_registry", lambda path=None: registry)

    def add(name, weekly_hours, role="l2_project"):
        registry.upsert(
            AgentEntry.from_dict(
                name, {"role": role, "description": name, "pace": {"weekly_hours": weekly_hours}}
            )
        )

    registry.add = add  # type: ignore[attr-defined]
    return registry


def _ddl(conn, title, due_at, *, course=None, source="zju", evidence_extra=None):
    evidence = {"uid": f"t-{title}"}
    if course:
        evidence["course"] = course
    evidence.update(evidence_extra or {})
    return store.create_event(
        conn,
        title=title,
        start_at=due_at,
        kind="ddl",
        status="pending",
        source=source,
        evidence=evidence,
    )


def _ddl_of(plan_item) -> str:
    return (plan_item.evidence or {}).get("ddl_event_id") or ""


def _ddl_blocks(db_path, plan_id):
    return [
        item
        for item in _items(db_path, plan_id)
        if (item.evidence or {}).get("kind") == DDL_BLOCK_EVIDENCE_KIND
    ]


def _items(db_path, plan_id):
    connection = store.connect(db_path)
    try:
        return store.list_plan_items(connection, plan_id)
    finally:
        connection.close()


class TestHomeworkGetsABlock:
    def test_unowned_ddl_lands_a_block_that_points_back_to_itself(self, bare_registry, db_path, conn) -> None:
        """治理线要的那一条：证明"作业挂上了块"，不是证明"库里多了行"。"""
        homework = _ddl(
            conn,
            "示波器的使用+声速测定预习报告",
            f"{DAY}T23:55:00",
            course=SCHOOL_COURSE,
        )
        plan = generate_evening_plan(DAY, db_path=db_path)
        blocks = _ddl_blocks(db_path, plan.id)

        assert len(blocks) == 1
        block = blocks[0]
        assert _ddl_of(block) == homework.id, "这块必须指回那条作业本体"
        assert block.kind == "task", "events 词表冻结（meeting/ddl/class/task），块用 task"
        assert block.title.startswith("写作业 · ")
        assert SCHOOL_COURSE in block.title, "课程名从 evidence 进标题，面上才认得出是哪门课"
        assert block.evidence["due_at"] == homework.start_at
        assert block.start_at < block.end_at

    def test_block_appears_even_with_zero_paced_projects(self, bare_registry, db_path, conn) -> None:
        """``if not paced: return [], []`` 那个提前返回就是断点的一半：没有 pace 项目时一块都不排。"""
        _ddl(conn, "实验报告二", f"{DAY}T20:00:00", course="线性代数")

        plan = generate_evening_plan(DAY, db_path=db_path)

        assert _ddl_blocks(db_path, plan.id), "名册里没有 pace 项目也要给作业排块"

    def test_nearest_due_takes_the_earliest_gap(self, bare_registry, db_path, conn) -> None:
        near = _ddl(conn, "近的作业", f"{DAY}T18:00:00", course="B")
        far = _ddl(conn, "远的作业", (datetime.fromisoformat(f"{DAY}T23:00:00") + timedelta(days=6)).isoformat(), course="A")

        plan = generate_evening_plan(DAY, db_path=db_path)
        blocks = _ddl_blocks(db_path, plan.id)

        assert [_ddl_of(b) for b in blocks] == [near.id, far.id][: len(blocks)]
        assert blocks[0].start_at < blocks[1].start_at, "先近后远，且不重叠"

    def test_cap_and_floor_reuse_the_c5_constants(self, bare_registry, db_path, conn) -> None:
        for index in range(3):
            _ddl(conn, f"作业{index}", f"{DAY}T{10 + index:02d}:00:00", course="C")

        blocks = _ddl_blocks(db_path, generate_evening_plan(DAY, db_path=db_path).id)
        durations = [
            (datetime.fromisoformat(b.end_at) - datetime.fromisoformat(b.start_at)).total_seconds() / 60.0
            for b in blocks
        ]

        assert durations, "空窗足够时至少该排出一块"
        assert all(30 <= minutes <= 120 for minutes in durations), (
            "作业块与项目块共用同一对上下界，不新造魔法数字"
        )

    def test_ddl_outside_the_lookahead_gets_no_block(self, bare_registry, db_path, conn) -> None:
        _ddl(conn, "下个月才到期", "2026-12-25T10:00:00", course="D")

        plan = generate_evening_plan(DAY, db_path=db_path)

        assert _ddl_blocks(db_path, plan.id) == [], "超出 DDL_LOOKAHEAD_DAYS 的视同无 DDL（C5 同一把尺）"

    def test_ddl_matching_a_paced_project_is_not_blocked_twice(self, bare_registry, db_path, conn) -> None:
        """已经驱动某个 pace 项目优先级的 DDL，不再单独排一块——同一件事不占两段时间。"""
        bare_registry.add("Simulation", 6)
        _ddl(conn, "Simulation 评审 DDL", f"{DAY}T16:00:00", source="manual")

        plan = generate_evening_plan(DAY, db_path=db_path)
        items = _items(db_path, plan.id)
        kinds = [(item.evidence or {}).get("kind") for item in items]

        assert DDL_BLOCK_EVIDENCE_KIND not in kinds
        assert PROJECT_BLOCK_EVIDENCE_KIND in kinds


class TestAssociationWidenedToEvidence:
    def test_next_ddl_is_not_none_when_the_project_name_is_only_in_evidence(
        self, bare_registry, conn
    ) -> None:
        """方案 B 那一半：项目名可以只在 evidence 里（用户真把课程建成项目时立刻挂得上）。"""
        bare_registry.add(SCHOOL_COURSE, 4)
        _ddl(conn, "示波器的使用+声速测定预习报告", f"{DAY}T23:55:00", course=SCHOOL_COURSE)

        window = datetime.fromisoformat(f"{DAY}T00:00:00")
        assert _next_ddl(conn, SCHOOL_COURSE, window_from=window) is not None
        assert _next_ddl(conn, "Plobi", window_from=window) is None, "别的project 不许蹭到这条 DDL"

    def test_matching_never_branches_on_the_source_name(self, conn) -> None:
        """判据 4：关联只看字段与标题，不看这是哪个信源采来的。"""
        event = store.create_event(
            conn,
            title="随便一个标题",
            start_at=f"{DAY}T09:00:00",
            kind="ddl",
            status="pending",
            source="timetable",
            evidence={"uid": "x", "course": "线性代数"},
        )

        assert _matches_project(event, "线性代数") is True
        assert _matches_project(event, "线性") is True, "子串也算（名册里的项目名常是简称）"
        assert _matches_project(event, "大学物理") is False
        assert _matches_project(event, "") is False

class TestSummaryDoesNotLie:
    def test_homework_blocks_are_named_as_homework_not_project_blocks(self, bare_registry, db_path, conn) -> None:
        _ddl(conn, "作业甲", f"{DAY}T21:00:00", course="E")

        plan = generate_evening_plan(DAY, db_path=db_path)

        assert "作业块" in plan.summary, "把作业块报成「项目推进块」是谎"
        assert "项目推进块" not in plan.summary, "这一轮没有任何 pace 项目"
