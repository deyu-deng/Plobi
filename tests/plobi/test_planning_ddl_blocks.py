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
    paced_projects,
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


class TestCapAndEquality:
    """裁定 121 一（用户拍的两条）：作业每天合计 ≤4h；作业在场时项目块**不被清零**。"""

    def test_homework_minutes_are_capped_not_counted(self, bare_registry, db_path, conn) -> None:
        """六条作业、空窗够大 ⇒ 作业时长**合计**必须 ≤ 240 分钟，剩下的进 deferred。"""
        from datetime import datetime, timedelta

        base = datetime.fromisoformat(f"{DAY}T08:00:00")
        for index in range(6):
            _ddl(conn, f"作业{index}", (base + timedelta(days=index)).isoformat(), course=f"C{index}")

        plan = generate_evening_plan(DAY, db_path=db_path)
        conn2 = store.connect(db_path)
        items = store.list_plan_items(conn2, plan.id)
        conn2.close()
        ddl_items = [i for i in items if (i.evidence or {}).get("kind") == DDL_BLOCK_EVIDENCE_KIND]
        minutes = sum(
            (datetime.fromisoformat(i.end_at) - datetime.fromisoformat(i.start_at)).total_seconds() / 60.0
            for i in ddl_items
        )

        assert 0 < minutes <= 240, f"作业块当日合计必须 ≤4h，实得 {minutes}"
        assert len(ddl_items) < 6, "装不下的不该被硬塞进夜里空档"
        assert plan.evidence["ddl_minutes"] == round(minutes, 1), "判据要能从 evidence 读，不用解析中文"
        assert plan.evidence["ddl_deferred"] >= 1
        assert "4 小时" in plan.summary or "没排上" in plan.summary, "排不下的必须说得出，还带原因"

    def test_projects_are_not_zeroed_when_homework_exists(self, bare_registry, db_path, conn) -> None:
        """同池轮转：两边都有产出才算成（旧顺序会把 11 个项目清零）。"""
        bare_registry.add("Plobi", 14)  # 14h/周 = 每天 2h，够拿一块
        for index in range(4):
            _ddl(conn, f"堆满一天的作业{index}", f"{DAY}T{18 + index}:00:00", course="F")

        plan = generate_evening_plan(DAY, db_path=db_path)
        conn2 = store.connect(db_path)
        items = store.list_plan_items(conn2, plan.id)
        conn2.close()
        kinds = [(i.evidence or {}).get("kind") for i in items]

        assert kinds.count(DDL_BLOCK_EVIDENCE_KIND) >= 1, "作业要有块"
        assert kinds.count(PROJECT_BLOCK_EVIDENCE_KIND) >= 1, "有作业在场也不许把项目块清零"
        assert plan.evidence["ddl_block_count"] >= 1 and plan.evidence["project_block_count"] >= 1

    def test_budget_leftovers_are_reported_per_subject(self, bare_registry, db_path, conn) -> None:
        """轮转不会把项目的每周预算吞掉：还剩 ≥最小块的项目仍列进未能安排。"""
        bare_registry.add("Kit", 0.5)  # 日预算 4.3 分钟 < MIN ⇒ 一块都排不上
        _ddl(conn, "一条作业", f"{DAY}T19:00:00", course="G")

        plan = generate_evening_plan(DAY, db_path=db_path)

        assert "Kit" not in plan.summary, "预算不足最小块的项目不该被报成「欠好几个小时」"


class TestCoursesCategoryIsRosterData:
    """裁定 121 二：新建 L2 品类 ``courses``；**品类是代码，条目是数据**。"""

    def test_courses_survives_validation_instead_of_folding_into_butler(self) -> None:
        from plobi.agents.registry import AGENT_CATEGORIES, MIND_PROJECT_CATEGORIES, _normalize_category

        assert "courses" in AGENT_CATEGORIES
        assert _normalize_category("Courses") == "courses", "拼大小写都要认，否则静默降级成 butler"
        assert "courses" not in MIND_PROJECT_CATEGORIES, (
            "我明判的那一格：课程条目不在脑仓 Vault/projects/ 立项（那是另一个仓、要单独动作）"
        )

    def test_courses_has_no_default_folder_binding_on_either_platform(self) -> None:
        from plobi.agents.registry import build_default_project_paths

        assert build_default_project_paths(is_windows=True)["courses"] == ""
        assert build_default_project_paths(is_windows=False, home="/tmp/whatever")["courses"] == ""

    def test_a_course_entry_makes_next_ddl_non_none(self, bare_registry, db_path, conn) -> None:
        """用户那句「把课程放进去」之后的效果：项目名只在 evidence 里也挂得上。"""
        from datetime import datetime

        bare_registry.add(SCHOOL_COURSE, 4)
        _ddl(conn, "示波器的使用+声速测定预习报告", f"{DAY}T23:55:00", course=SCHOOL_COURSE)

        moment = datetime.fromisoformat(f"{DAY}T00:00:00")
        assert _next_ddl(conn, SCHOOL_COURSE, window_from=moment) == moment + timedelta_hours("23:55")

    def test_course_owned_ddl_gets_no_second_homework_block(self, bare_registry, db_path, conn) -> None:
        """挂上课程项目的作业走项目块那一侧，不许同一件事占两段时间。"""
        bare_registry.add(SCHOOL_COURSE, 6)
        _ddl(conn, "示波器的使用", f"{DAY}T23:55:00", course=SCHOOL_COURSE)

        plan = generate_evening_plan(DAY, db_path=db_path)
        conn2 = store.connect(db_path)
        kinds = [(i.evidence or {}).get("kind") for i in store.list_plan_items(conn2, plan.id)]
        conn2.close()

        assert DDL_BLOCK_EVIDENCE_KIND not in kinds
        assert PROJECT_BLOCK_EVIDENCE_KIND in kinds


def timedelta_hours(hhmm: str):
    from datetime import timedelta

    hour, minute = hhmm.split(":")
    return timedelta(hours=int(hour), minutes=int(minute))


class _CourseRoster:
    """把课程建成名册条目这件事本身——用户 10-11 拍的是「五门课要进、不带 pace」。"""

    @staticmethod
    def add_course(registry, name: str):
        from plobi.agents.registry import AgentEntry

        return registry.upsert(
            AgentEntry.from_dict(
                name,
                {"role": "l2_project", "category": "courses", "description": f"课程 {name}"},
            )
        )


def test_the_course_name_reaches_the_surface_the_desktop_reads(db_path, conn):
    """判据 2「证据可读」这一格的真读数：桌面走 ``/api/agenda/day`` = ``day_surface``。

    不是只看 ``plan_items`` 多了一行——要看见**界面上那一条**带着课程名，
    否则"挂上了"只成立在库里。
    """
    from plobi.agenda.planning import day_surface

    _ddl(conn, "第5次课后作业-(2.3)", "2026-10-18T23:55:00", course="电工电子学")
    plan = generate_evening_plan(DAY, db_path=db_path)
    blocks = _ddl_blocks(db_path, plan.id)
    assert blocks, "先要有一块作业，否则下面那句面读数没东西可读"

    connection = store.connect(db_path)
    try:
        events = store.list_events(
            connection,
            start_from=datetime.fromisoformat(f"{DAY}T00:00:00"),
            start_to=datetime.fromisoformat(f"{DAY}T00:00:00") + timedelta(days=1),
        )
        snap = day_surface(connection, DAY, events)
    finally:
        connection.close()

    titles = [row["title"] for row in snap["plan_items"]]
    assert any("电工电子学" in t for t in titles), titles
    assert any((row.get("evidence") or {}).get("course") == "电工电子学" for row in snap["plan_items"])


def test_a_paceless_course_entry_changes_nothing_in_the_schedule(bare_registry, db_path, conn):
    """名册里多了这门课 ⇒ 排程数学一字不动（块还是那块，仍归"无主作业"）。

    钉两件事：① 不带 pace 的课程**进不了** ``paced_projects()``（那是"每周推进"的入口）；
    ② 所以它也不会把作业从"无主"里摘走。回访那侧因此不会 nag 这门课。
    """
    _ddl(conn, "实验报告提交-实验三", "2026-10-20T23:59:00", course="电工电子学实验")

    before = generate_evening_plan(DAY, db_path=db_path)
    before_blocks = [(b.title, b.start_at, b.end_at) for b in _ddl_blocks(db_path, before.id)]

    _CourseRoster.add_course(bare_registry, "电工电子学实验")
    after = generate_evening_plan(DAY, db_path=db_path)
    after_blocks = [(b.title, b.start_at, b.end_at) for b in _ddl_blocks(db_path, after.id)]

    assert before_blocks == after_blocks and before_blocks, "加条目不该动排程，也不该测出个空集"
    paced_names = [entry.name for entry, _hours in paced_projects()]
    assert "电工电子学实验" not in paced_names, (
        "不带 pace 的课程不该出现在推进池里——它一出现，作业就会从「无主」被摘走"
    )
