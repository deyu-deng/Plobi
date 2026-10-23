"""课程是名册数据（裁定 121 二 / 153）：品类是代码，条目是数据。

计划层 10-22 退役时，从 `test_planning_ddl_blocks.py` 里把与均分/计划行无关的
几格搬到这里——它们钉的是课程归属与名册，不是已退役的那一层。

跑法：``bash scripts/run_tests.sh tests/plobi/test_course_roster.py``（唯一入口）。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from plobi.agenda import llm_planning, store
from plobi.agenda.planning import (
    _as_dt,
    _matches_project,
    _next_ddl,
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


def timedelta_hours(hhmm: str):
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


class TestCoursesCategoryIsRosterData:
    """裁定 121 二：新建 L2 品类 ``courses``；**品类是代码，条目是数据**。"""

    def test_courses_survives_validation_instead_of_folding_into_butler(self) -> None:
        from plobi.agents.registry import AGENT_CATEGORIES, MIND_PROJECT_CATEGORIES, _normalize_category

        assert "courses" in AGENT_CATEGORIES
        assert _normalize_category("Courses") == "courses", "拼大小写都要认，否则静默降级成 butler"
        assert "courses" not in MIND_PROJECT_CATEGORIES, (
            "我明判的那一格：课程条目不在脑仓 Vault/projects/ 立项（那是另一个仓、要单独动作）"
        )

    def test_courses_binding_is_per_entry_not_a_shared_default(self) -> None:
        """裁定 153 改判之后：课程**不再是「无绑定类别」**，但绑定是**条目级**的。"""
        from plobi.agents.registry import UNBOUND_CATEGORIES, build_default_project_paths

        assert "courses" not in UNBOUND_CATEGORIES, "153 改判：课程可以有本地绑定路径了"
        assert build_default_project_paths(is_windows=True)["courses"] == "", \
            "Windows 那张表里 courses 仍是空格（条目自己的 project_path 永远优先）"
        posix = build_default_project_paths(is_windows=False, home="/home/someone")
        assert "courses" in posix, "这一格必须在读数里出现，不许静默省略"

    def test_a_course_entry_makes_next_ddl_non_none(self, bare_registry, db_path, conn) -> None:
        """用户那句「把课程放进去」之后的效果：项目名只在 evidence 里也挂得上。"""
        bare_registry.add(SCHOOL_COURSE, 4)
        _ddl(conn, "示波器的使用+声速测定预习报告", f"{DAY}T23:55:00", course=SCHOOL_COURSE)

        moment = datetime.fromisoformat(f"{DAY}T00:00:00")
        assert _next_ddl(conn, SCHOOL_COURSE, window_from=moment) == moment + timedelta_hours("23:55")


def test_the_homework_source_reaches_the_surface_through_events(db_path, conn):
    """判据 2「证据可读」：秘书排出来的那条 events 必须指得回材料（哪门课的哪份作业）。"""
    due = "2026-10-18T23:55:00"
    ddl = _ddl(conn, "第5次课后作业-(2.3)", due, course="电工电子学")

    def proposal(_conn, day):
        return ([{
            "title": "写作业 · 第5次课后作业-(2.3)",
            "start_at": f"{day}T09:00:00",
            "end_at": f"{day}T10:00:00",
        }], "按材料排")

    result = llm_planning.plan_and_store_day(DAY, conn=conn, proposal_fn=proposal)
    assert result["ok"] is True, result
    block = next(block for block in result["blocks"] if block.get("source", "").startswith("ddl:"))
    assert block["source"] == f"ddl:{ddl.id}"
    assert store.get_event(conn, ddl.id).evidence["course"] == "电工电子学"


def test_a_paceless_course_entry_never_enters_the_pace_pool(bare_registry):
    """不带 pace 的课程进不了 ``paced_projects()``——回访那侧因此不会 nag 这门课。"""
    _CourseRoster.add_course(bare_registry, "电工电子学实验")

    paced_names = [entry.name for entry, _hours in paced_projects()]
    assert "电工电子学实验" not in paced_names


def test_a_short_course_name_must_not_steal_the_long_one_s_homework(bare_registry, conn):
    """按课归属不许串台（WP-COLLECT-SCHEDULE-VISIBLE 判据 1；2026-10-15 在这台真库抓到）。

    真库形状：`电工电子学`（2 条作业）与 `电工电子学实验`（2 条作业）是两门课，
    旧口径拿名册条目名对 `evidence.course` 做子串匹配 ⇒ 短名把长名的作业一起认领走。
    """
    own = _ddl(conn, "第5次课后作业-(2.3)", "2026-10-18T23:55:00", course="电工电子学")
    lab = _ddl(conn, "实验报告提交-实验三-叠加原理", "2026-10-20T23:59:00", course="电工电子学实验")
    _CourseRoster.add_course(bare_registry, "电工电子学")
    _CourseRoster.add_course(bare_registry, "电工电子学实验")
    names = [entry.name for entry in bare_registry.entries()]

    assert _matches_project(lab, "电工电子学实验", other_names=names) is True
    assert _matches_project(lab, "电工电子学", other_names=names) is False, (
        "名册里已经有人跟这条 `evidence.course` 全等，短名不许再靠子串把它抢走"
    )

    window = datetime.fromisoformat(f"{DAY}T00:00:00")
    nxt = _next_ddl(conn, "电工电子学", window_from=window, other_names=names)
    assert nxt is not None and nxt == _as_dt(own.start_at), (
        "短名的下一个 DDL 必须是它自己那条作业，不能是别门课的"
    )
    claimed = [name for name in ("电工电子学", "电工电子学实验")
               if _matches_project(lab, name, other_names=names)]
    assert claimed == ["电工电子学实验"], f"一条作业被两门课同时认领：{claimed}"


def test_an_abbreviated_project_name_still_reaches_a_longer_course_field(bare_registry, conn):
    """名册里没人全等那条归属字段时，简称仍然算命中——保住 C5 那条既有契约。"""
    event = _ddl(conn, "随便一个课件名", "2026-10-19T23:00:00", course="线性代数")
    names = [entry.name for entry in bare_registry.entries()]

    assert "线性代数" not in names, "名册里没有全等者，这条测的才是简称那一档"
    assert _matches_project(event, "线性", other_names=names) is True
