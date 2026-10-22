"""晚间计划这一侧还剩哪些**真关系**可测（WP-NIGHTLY-PLAN-BY-AGENT 判据 4）。

这一档原来是钉**均分那段**的：`planning._project_block_items` 把剩余空窗按日预算分给
每个 pace 项目 / 每条无主作业，13 条断言测的是那套分配数学的产出。裁定 149 早就拍了「明天那份
计划由 L1 读材料后排，均分那段退役」，裁定 163（用户 10-16 原话「不要用脚本排程。让日程秘书
自己排一次明天的计划」）把"撤脚本生产接线"与"删均分本体"并成一把刀。本体与它只服务自己的
私有 helper 现已删除 ⇒ 这 13 条**不是删掉来变绿**，而是迁成**注入假 planner**：

- 假 planner 交回的是**结构化提案**（与 :func:`plobi.agenda.llm_planning.blocks_from_l1`
  的返回形状一字同：`(blocks, note)`），不调模型、不花一分钱（任务书硬边界「零额度」）。
- 断言随之改钉**删掉均分之后仍然成立、且必须由代码守住**的那几条关系：
  ① 没接线就一个块不写，并在面上照实说没排（裁定 149／163③ 的反兜底判据）；
  ② planner 交回的块**原样落库**，不重切、不缩放、不改词表（`kind` 仍是 task）；
  ③ 每块的 evidence 指得回材料里那一条（哪门课的哪份作业 / 哪个项目），指不回的写不出来；
  ④ 同一件事不许占两段时间（无主作业那条 DDL 被 pace 项目认领走之后不许再排一块）；
  ⑤ 作业块与项目块分开计数（把作业报成「项目推进块」是谎）；
  ⑥ 边界四条（睡眠与三餐不许被挤掉 · 同时段不许双订 · 总量不超清醒窗口 · 占满空档的通勤条目）
    由**唯一那道闸** :func:`plobi.agenda.llm_planning.validate_proposal` 判——它现在是谁写库
    都要过的同一道闸（`llm_planning.plan_and_store_day` 与 `agenda/plan_edits.py` 都调它），
    所以这两格钉的是它，不是均分。

还有一条**必须照实的欠账**（本格不掩盖）：裁定 121 一那句「作业每天合计 ≤4 小时」原先由
均分自己截断并自己上报 deferred。均分删了之后：
- **截断**那一半没有承接方 —— 材料口与校验器在 `llm_planning.py`（同刀另一路在飞），
  现核那里既没有 240 这个数字也不在边界四条里 ⇒ 这一格目前只剩 `DDL_DAILY_BUDGET_MINUTES`
  这个常量与面上那句读数，**没人 enforce**。下面的
  `test_homework_minutes_are_capped_not_counted` 钉的是"读数不许造假"，不是"上限有人守"。
- **上报 deferred** 那一半随本体一起消失：planner 的返回形状只有 `(blocks, note)` 两位，
  代码不可能知道它放弃了什么 ⇒ 现在**造不出**「未能安排：X（缺 Y 小时）」那种句子，
  而**造不出来正是判据**（那是均分在替 L1 编故事）。见
  `test_the_write_path_fabricates_no_deferrals`。

跑法：``bash scripts/run_tests.sh tests/plobi/test_planning_ddl_blocks.py -q``（唯一入口，不传 ``--basetemp``）。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from plobi.agenda import store
from plobi.agenda.planning import (
    DDL_BLOCK_EVIDENCE_KIND,
    DDL_DAILY_BUDGET_MINUTES,
    DDL_LOOKAHEAD_DAYS,
    MAX_BLOCK_MINUTES,
    MIN_BLOCK_MINUTES,
    NO_PLANNER_NOTE,
    PROJECT_BLOCK_EVIDENCE_KIND,
    _as_dt,
    _ddl_candidates,
    _matches_project,
    _next_ddl,
    generate_evening_plan,
    paced_projects,
)

DAY = "2026-10-11"
SCHOOL_COURSE = "大学物理"
_DAY_START = datetime.fromisoformat(f"{DAY}T00:00:00")
FAKE_NOTE = "假 planner 那一轮：作业按到期先后拿槽位，睡眠与三餐没动。"


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


# --------------------------------------------------------------------------- #
# 夹具：假 planner（不花钱）+ 生产形状的两类块
# --------------------------------------------------------------------------- #

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


def _slot(index: int, *, minutes: int = 60, first_hour: int = 9):
    """第 ``index`` 号**提案槽位**——这是夹具，不是分配数学。

    均分那段死在"自己算哪段空窗归谁"（那正是代码替 L1 排程）。这里时段表由调用方点名给出，
    假 planner 只负责"我交出这几块"，代码只负责"原样落库"。
    """
    start = _DAY_START + timedelta(hours=first_hour, minutes=index * minutes)
    return start, start + timedelta(minutes=minutes)


def _homework_block(event, start, end):
    """作业块。形状与 ``llm_planning._block_rows_from_proposal`` 同族：`planner` 是谁排的、
    `source` 指回材料那一条；`kind` 仍是 ``ddl_block`` 让面上能把作业与项目分开计数。"""
    extra = event.evidence if isinstance(event.evidence, dict) else {}
    course = str(extra.get("course") or "").strip()
    return {
        "event_id": f"ddl:{event.id}",
        "title": f"写作业 · {event.title}" + (f"（{course}）" if course else ""),
        "start_at": start.isoformat(),
        "end_at": end.isoformat(),
        "kind": "task",  # 词表不扩（meeting/ddl/class/task 冻结）
        "conflict_with": [],
        "evidence": {
            "kind": DDL_BLOCK_EVIDENCE_KIND,
            "planner": "l1",
            "source": f"ddl:{event.id}",
            "ddl_event_id": event.id,
            "due_at": event.start_at,
            "event_source": event.source,
            **({"course": course} if course else {}),
        },
    }


def _project_block(name, weekly_hours, start, end):
    return {
        "event_id": f"project:{name}",
        "title": f"推进 · {name}",
        "start_at": start.isoformat(),
        "end_at": end.isoformat(),
        "kind": "task",
        "conflict_with": [],
        "evidence": {
            "kind": PROJECT_BLOCK_EVIDENCE_KIND,
            "planner": "l1",
            "source": f"project:{name}",
            "project_id": name,
            "pace": f"weekly_hours:{weekly_hours:g}",
        },
    }


class _FakePlanner:
    """注入生成侧的假 planner：``(conn, day) -> (blocks, note)``。不调模型、不花钱。

    ``calls`` 记下它被以什么参数叫了几次 —— 那几条"没接线就不许出块""旧 gate 不许再挡路"
    的判据要看的正是**叫没叫**，不是产出好不好看。
    """

    def __init__(self, blocks=(), note=FAKE_NOTE, *, raises=None, propose=None):
        self._fixed = list(blocks)
        self.note = note
        self.raises = raises
        self.propose = propose          # (conn, day) -> list[dict]：从**生产口径**取作业池
        self.calls: list[tuple[str, int]] = []

    def __call__(self, conn, day):
        self.calls.append((day, id(conn)))
        if self.raises is not None:
            raise self.raises
        blocks = self._fixed if self.propose is None else self.propose(conn, day)
        return list(blocks), self.note

    @property
    def call_count(self):
        return len(self.calls)


def _homework_pool(conn, day, *, exclude_paced=False, other_names=()):
    """该有块的作业**从生产口径里取**，不在测试里另数一遍：

    - 窗口与完成态：:func:`plobi.agenda.planning._ddl_candidates`（内含
      ``include_completed=False`` 与按到期时刻排序），上界是 ``DDL_LOOKAHEAD_DAYS``；
    - 归属：:func:`_matches_project` + :func:`paced_projects`（删掉均分之后仍然活着的归属真源）。

    ⚠️ 这里只回答"哪些作业该有块"，**不回答"哪段空窗归谁"** —— 后者是均分那段犯的错。
    """
    pool = _ddl_candidates(conn, _DAY_START, _DAY_START + timedelta(days=DDL_LOOKAHEAD_DAYS))
    if exclude_paced:
        paced = [entry.name for entry, _hours in paced_projects()]
        pool = [
            event for event in pool
            if not any(_matches_project(event, name, other_names=other_names) for name in paced)
        ]
    return pool


def _proposing_homework(*, exclude_paced=False, slots=None, extra_blocks=(), note=FAKE_NOTE):
    """假 planner：作业池用 :func:`_homework_pool` 取，槽位由调用方给（默认顺号）。

    ``extra_blocks`` 是给"这一轮 L1 还交了项目块"那种形状用的 —— 它自己带槽位，不再分配。
    """

    def propose(conn, day):
        pool = _homework_pool(conn, day, exclude_paced=exclude_paced)
        used = slots if slots is not None else [_slot(i) for i in range(len(pool))]
        # 槽位比作业多 = 夹具在凭空造块，必须炸在这里；少则是"这一轮只交了这么多"（合法）
        assert len(used) <= max(len(pool), 1), f"提案槽位 {len(used)} 多于作业 {len(pool)} 条"
        blocks = [_homework_block(event, start, end)
                  for event, (start, end) in zip(pool, used)]
        return blocks + list(extra_blocks)

    return _FakePlanner(note=note, propose=propose)


def _ddl_of(plan_item) -> str:
    return (plan_item.evidence or {}).get("ddl_event_id") or ""


def _ddl_blocks(db_path, plan_id):
    return [
        item
        for item in _items(db_path, plan_id)
        if (item.evidence or {}).get("kind") == DDL_BLOCK_EVIDENCE_KIND
    ]


def _project_blocks(db_path, plan_id):
    return [
        item
        for item in _items(db_path, plan_id)
        if (item.evidence or {}).get("kind") == PROJECT_BLOCK_EVIDENCE_KIND
    ]


def _items(db_path, plan_id):
    connection = store.connect(db_path)
    try:
        return store.list_plan_items(connection, plan_id)
    finally:
        connection.close()


def _minutes(item) -> float:
    return (
        datetime.fromisoformat(item.end_at) - datetime.fromisoformat(item.start_at)
    ).total_seconds() / 60.0


def _assert_no_double_booking(items) -> None:
    """关系判据「同一时段不许双订」，**读的是落库那几行**，不是提案。

    没有 ``end_at`` 的行（DDL 那种"某时刻之前"的**点**）不占时段，按 `intervals_overlap`
    同一条口径跳过 —— 把点当区间来比会假红。
    """
    timed = [item for item in items if item.end_at]
    for i, left in enumerate(timed):
        for right in timed[i + 1:]:
            assert left.start_at >= right.end_at or right.start_at >= left.end_at, (
                f"落库的两行叠在同一时段：{left.title} {left.start_at}–{left.end_at} / "
                f"{right.title} {right.start_at}–{right.end_at}"
            )


def _assert_one_block_per_subject(items) -> None:
    """关系判据「同一件事不占两段时间」：同一条 DDL / 同一个项目不许有第二块。"""
    seen: list[str] = []
    for item in items:
        evidence = item.evidence or {}
        subject = evidence.get("ddl_event_id") or evidence.get("project_id")
        if not subject:
            continue
        assert subject not in seen, f"{subject} 被排了两次（{item.title}）"
        seen.append(subject)


# --------------------------------------------------------------------------- #
# ① 反兜底：没接线就是没有块（裁定 149／163③ 的核心判据）
# --------------------------------------------------------------------------- #

class TestNoFallbackAfterTheSplitIsRetired:
    """均分本体删掉之后，`generate_evening_plan` 不许留第二条路。

    这一族是**变异验证的靶子**：把 ``_l1_blocks`` 里 ``planner is None`` 那一格换回
    「兜一版看起来排满的东西」，下面每一条立刻红。
    """

    def test_without_a_planner_no_block_is_written(self, bare_registry, db_path, conn) -> None:
        """库里有作业、名册里有 pace 项目 —— 没 planner ⇒ 块数必须为 0（不是"排满"）。"""
        bare_registry.add("Simulation", 14)
        _ddl(conn, "示波器的使用+声速测定预习报告", f"{DAY}T23:55:00", course=SCHOOL_COURSE)

        plan = generate_evening_plan(DAY, db_path=db_path)

        items = _items(db_path, plan.id)
        kinds = [(item.evidence or {}).get("kind") for item in items]
        assert DDL_BLOCK_EVIDENCE_KIND not in kinds, "没接线还出作业块 = 兜底没删干净"
        assert PROJECT_BLOCK_EVIDENCE_KIND not in kinds, "没接线还出项目块 = 兜底没删干净"

    def test_without_a_planner_the_plan_says_it_scheduled_nothing(
        self, bare_registry, db_path, conn
    ) -> None:
        """兜底要么随本体消失、要么自报没排成的人话 —— 这里两样都要：块没了，话也要有。"""
        _ddl(conn, "实验报告二", f"{DAY}T20:00:00", course="线性代数")

        plan = generate_evening_plan(DAY, db_path=db_path)

        assert NO_PLANNER_NOTE in plan.summary, plan.summary
        assert plan.evidence is None, "没块就不该编一份 evening_plan 证据出来"
        # 面上那句必须说清"没排"，而不是留一句看起来像排过了的空话
        assert "没排" in plan.summary or "没有" in plan.summary

    def test_without_a_planner_the_day_keeps_its_real_events(self, bare_registry, db_path, conn) -> None:
        """诚实的空只针对块：当天真事件与作息一行都不许跟着丢（完成态那套口径不动）。"""
        event = store.create_event(
            conn, title="电工电子学", start_at=f"{DAY}T10:00:00", end_at=f"{DAY}T11:40:00",
            kind="class", status="pending", source="timetable", evidence={"uid": "c-1"},
        )

        plan = generate_evening_plan(DAY, db_path=db_path)

        rows = _items(db_path, plan.id)
        assert [row.event_id for row in rows] == [event.id]
        assert plan.status == "pending"
        assert plan.event_count == 1

    def test_the_write_path_fabricates_no_deferrals(self, bare_registry, db_path, conn) -> None:
        """「未能安排：X（缺 Y 小时）」那种句子是均分替 L1 编的故事，现在造不出来才是对的。"""
        bare_registry.add("Kit", 0.5)
        _ddl(conn, "一条作业", f"{DAY}T19:00:00", course="G")

        plan = generate_evening_plan(DAY, db_path=db_path)

        evidence = plan.evidence or {}
        assert "未能安排" not in plan.summary, plan.summary
        assert "没排上" not in plan.summary, plan.summary
        assert "unscheduled" not in evidence and "ddl_deferred" not in evidence

    def test_a_planner_that_raises_writes_no_blocks_and_names_the_failure(
        self, bare_registry, db_path, conn
    ) -> None:
        """通道炸了也不写假块，且要把炸在何处念出来（与 `blocks_from_l1` 同一口径）。"""
        planner = _FakePlanner(raises=RuntimeError("额度路空返回"))
        _ddl(conn, "会炸的那条作业", f"{DAY}T21:00:00", course="H")

        plan = generate_evening_plan(DAY, db_path=db_path, planner=planner)

        assert planner.call_count == 1
        assert _ddl_blocks(db_path, plan.id) == []
        assert "RuntimeError" in plan.summary and "没排成" in plan.summary


# --------------------------------------------------------------------------- #
# ② 无主作业是一等公民 + 证据指得回材料
# --------------------------------------------------------------------------- #

class TestHomeworkGetsABlock:
    def test_unowned_ddl_lands_a_block_that_points_back_to_itself(self, bare_registry, db_path, conn) -> None:
        """治理线要的那一条：证明"作业挂上了块"，且这块**指得回那条作业本体**。"""
        homework = _ddl(
            conn,
            "示波器的使用+声速测定预习报告",
            f"{DAY}T23:55:00",
            course=SCHOOL_COURSE,
        )
        planner = _proposing_homework()

        plan = generate_evening_plan(DAY, db_path=db_path, planner=planner)
        blocks = _ddl_blocks(db_path, plan.id)

        assert planner.call_count == 1 and planner.calls[0][0] == DAY, (
            "planner 必须被以 (conn, day) 叫到，且只叫一次"
        )
        assert len(blocks) == 1
        block = blocks[0]
        assert _ddl_of(block) == homework.id, "这块必须指回那条作业本体"
        assert block.kind == "task", "events 词表冻结（meeting/ddl/class/task），块用 task"
        assert block.title.startswith("写作业 · ")
        assert SCHOOL_COURSE in block.title, "课程名从 evidence 进标题，面上才认得出是哪门课"
        assert block.evidence["due_at"] == homework.start_at
        # 「谁排的 / 指回哪一条」是裁定 163 判据②的读数口径，缺一位就是面上看不出排过
        assert block.evidence["planner"] == "l1"
        assert block.evidence["source"] == f"ddl:{homework.id}"
        assert block.start_at < block.end_at

    def test_block_appears_even_with_zero_paced_projects(self, bare_registry, db_path, conn) -> None:
        """旧断点 ``if not paced: return [], []`` 那一半：名册里一个 pace 项目都没有。

        迁到假 planner 之后这一格测的是**生成侧不许再拿 pace 名册当闸门** —— 作业能不能有块
        与名册有没有 pace 项目无关，所以 planner 必须被叫到，块必须落得下。
        """
        _ddl(conn, "实验报告二", f"{DAY}T20:00:00", course="线性代数")
        assert paced_projects() == [], "这一格的前提：名册里真的没有 pace 项目"

        planner = _proposing_homework()
        plan = generate_evening_plan(DAY, db_path=db_path, planner=planner)

        assert planner.call_count == 1, "没有 pace 项目就不叫 planner = 旧断点复活"
        assert _ddl_blocks(db_path, plan.id), "名册里没有 pace 项目也要给作业排块"

    def test_nearest_due_takes_the_earliest_gap(self, bare_registry, db_path, conn) -> None:
        """作业池由 `_ddl_candidates` 按**到期时刻**排序 ⇒ 近的先拿到靠前的槽位。"""
        near = _ddl(conn, "近的作业", f"{DAY}T18:00:00", course="B")
        far = _ddl(conn, "远的作业", (_DAY_START + timedelta(days=6)).isoformat(), course="A")

        plan = generate_evening_plan(DAY, db_path=db_path, planner=_proposing_homework())
        blocks = _ddl_blocks(db_path, plan.id)

        assert [_ddl_of(b) for b in blocks] == [near.id, far.id][: len(blocks)]
        assert blocks[0].start_at < blocks[1].start_at, "先近后远"
        _assert_no_double_booking(blocks)

    def test_cap_and_floor_reuse_the_c5_constants(self, bare_registry, db_path, conn) -> None:
        """上下界只有一对常量（`MIN/MAX_BLOCK_MINUTES`），且生成侧**原样落库**、不重切。"""
        for index in range(3):
            _ddl(conn, f"作业{index}", f"{DAY}T{10 + index:02d}:00:00", course="C")
        planned = [
            _slot(0, minutes=MIN_BLOCK_MINUTES),
            _slot(1, minutes=MAX_BLOCK_MINUTES),
            _slot(2, minutes=(MIN_BLOCK_MINUTES + MAX_BLOCK_MINUTES) // 2),
        ]

        blocks = _ddl_blocks(
            db_path,
            generate_evening_plan(DAY, db_path=db_path,
                                  planner=_proposing_homework(slots=planned)).id,
        )
        durations = [_minutes(b) for b in blocks]

        assert durations == [MIN_BLOCK_MINUTES, MAX_BLOCK_MINUTES,
                             (MIN_BLOCK_MINUTES + MAX_BLOCK_MINUTES) // 2], (
            "提案多长就落多长：重切/缩放就是又造了第二套分配数学"
        )
        assert all(MIN_BLOCK_MINUTES <= m <= MAX_BLOCK_MINUTES for m in durations), (
            "作业块与项目块共用同一对上下界，不新造魔法数字"
        )

    def test_ddl_outside_the_lookahead_gets_no_block(self, bare_registry, db_path, conn) -> None:
        """超出 `DDL_LOOKAHEAD_DAYS` 的视同无 DDL（C5 同一把尺）——尺由生产函数量。"""
        far = _ddl(conn, "下个月才到期", "2026-12-25T10:00:00", course="D")

        plan = generate_evening_plan(DAY, db_path=db_path, planner=_proposing_homework())

        assert _ddl_blocks(db_path, plan.id) == [], "超出窗口的视同无 DDL"
        # 前提自证：那条作业确实在库里，红的不是"没数据"而是"窗口不认它"
        assert [e.id for e in _ddl_candidates(conn, _DAY_START, _DAY_START + timedelta(days=90))
                ] == [far.id]

    def test_ddl_matching_a_paced_project_is_not_blocked_twice(self, bare_registry, db_path, conn) -> None:
        """已经驱动某个 pace 项目的 DDL 不再单独排一块 —— 生产归属口径判，测试只断结果。"""
        bare_registry.add("Simulation", 6)
        ddl = _ddl(conn, "Simulation 评审 DDL", f"{DAY}T16:00:00", source="manual")
        assert _matches_project(ddl, "Simulation") is True, "前提：这条作业被 pace 项目认领"

        plan = generate_evening_plan(
            DAY, db_path=db_path,
            planner=_proposing_homework(
                exclude_paced=True,
                extra_blocks=[_project_block("Simulation", 6, *_slot(0))],
            ),
        )
        items = _items(db_path, plan.id)
        kinds = [(item.evidence or {}).get("kind") for item in items]

        assert DDL_BLOCK_EVIDENCE_KIND not in kinds, "被项目认领的作业不许再排一块作业块"
        assert PROJECT_BLOCK_EVIDENCE_KIND in kinds
        _assert_one_block_per_subject(items)


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

        plan = generate_evening_plan(DAY, db_path=db_path, planner=_proposing_homework())

        assert "作业块" in plan.summary, "把作业块报成「项目推进块」是谎"
        assert "项目推进块" not in plan.summary, "这一轮没有任何 pace 项目块"
        assert plan.evidence["ddl_block_count"] == 1
        assert plan.evidence["project_block_count"] == 0

    def test_the_two_block_families_are_counted_separately(self, bare_registry, db_path, conn) -> None:
        """面上那两句要各说各的数：一次计数错就等于对用户报假账。"""
        _ddl(conn, "作业甲", f"{DAY}T21:00:00", course="E")
        planner = _FakePlanner(blocks=[
            _homework_block(
                store.list_events(conn, start_from=f"{DAY}T00:00:00",
                                  start_to=f"{DAY}T23:59:59")[0],
                *_slot(0)),
            _project_block("Simulation", 6, *_slot(1)),
            _project_block("Resonote", 3, *_slot(2)),
        ])

        plan = generate_evening_plan(DAY, db_path=db_path, planner=planner)

        assert plan.evidence["ddl_block_count"] == 1
        assert plan.evidence["project_block_count"] == 2
        assert "1项作业块" in plan.summary and "2项项目推进块" in plan.summary


class TestCapAndEquality:
    """裁定 121 一（用户拍的两条）：作业每天合计 ≤4h；作业在场时项目块**不被清零**。"""

    def test_homework_minutes_are_capped_not_counted(self, bare_registry, db_path, conn) -> None:
        """六条作业 ⇒ 面上的 `ddl_minutes` 必须等于**真正落库那几块的时长合计**，
        并且合计停在 240 分钟那格上。

        ⚠️ 这一格钉的是"读数不造假"，**不是"有人拦上限"**：均分那段自己截断、自己上报 deferred，
        本体删除后截断那一半还没人接（材料口与校验器在同刀另一路的 `llm_planning.py`，
        现核那里没有 240 这个数字）。这条欠账在模块 docstring 里点名，不拿绿当办完。
        """
        for index in range(6):
            _ddl(conn, f"作业{index}", (_DAY_START + timedelta(days=index)).isoformat(),
                 course=f"C{index}")
        per_block = DDL_DAILY_BUDGET_MINUTES // 4  # 4 块吃满当日上限
        planned = [_slot(i, minutes=per_block) for i in range(4)]

        plan = generate_evening_plan(DAY, db_path=db_path,
                                     planner=_proposing_homework(slots=planned))
        ddl_items = _ddl_blocks(db_path, plan.id)
        minutes = sum(_minutes(i) for i in ddl_items)

        assert 0 < minutes <= DDL_DAILY_BUDGET_MINUTES, f"作业块当日合计必须 ≤4h，实得 {minutes}"
        assert len(ddl_items) < 6, "装不下的不该被硬塞进来"
        assert plan.evidence["ddl_minutes"] == round(minutes, 1), (
            "判据要能从 evidence 读，不用解析中文；这一格必须等于落库那几块的合计"
        )
        assert plan.evidence["ddl_block_count"] == len(ddl_items)

    def test_projects_are_not_zeroed_when_homework_exists(self, bare_registry, db_path, conn) -> None:
        """同池那一侧的旧 bug（作业把项目清零）迁成关系判据：**两类块互不取消**。

        均分不在场之后，"谁被清零"只可能由生成侧闸门造成 ⇒ 这一格钉的是
        planner 交回的两类块都得落库、计数各算各的。
        """
        bare_registry.add("Plobi", 14)
        for index in range(4):
            _ddl(conn, f"堆满一天的作业{index}", f"{DAY}T{18 + index}:00:00", course="F")

        def propose(conn, day):
            pool = _homework_pool(conn, day)
            return (
                [_homework_block(event, *_slot(i)) for i, event in enumerate(pool)]
                + [_project_block("Plobi", 14, *_slot(len(pool), minutes=MIN_BLOCK_MINUTES))]
            )

        plan = generate_evening_plan(DAY, db_path=db_path, planner=_FakePlanner(propose=propose))
        items = _items(db_path, plan.id)
        kinds = [(i.evidence or {}).get("kind") for i in items]

        assert kinds.count(DDL_BLOCK_EVIDENCE_KIND) == 4, "作业要有块"
        assert kinds.count(PROJECT_BLOCK_EVIDENCE_KIND) == 1, "有作业在场也不许把项目块清零"
        assert plan.evidence["ddl_block_count"] == 4 and plan.evidence["project_block_count"] == 1

    def test_budget_leftovers_are_reported_per_subject(self, bare_registry, db_path, conn) -> None:
        """旧口径是「轮转吞掉的项目预算仍要列进未能安排」；本体删除后这条**反过来了**。

        项目还剩多少预算是均分自己的账，代码现在无从得知 ⇒ 面上不许再出现
        「未能安排：Kit（缺 X 小时）」那种替 L1 编的欠账。`Kit` 不在 summary 里
        这一条原始断言**保留**（它当初要防的就是"预算不足最小块的项目被报成欠好几个小时"），
        现在它由"根本不该有这句"来守。
        """
        bare_registry.add("Kit", 0.5)  # 日预算 4.3 分钟，均分会把它算成欠账
        _ddl(conn, "一条作业", f"{DAY}T19:00:00", course="G")

        plan = generate_evening_plan(DAY, db_path=db_path, planner=_proposing_homework())

        assert "Kit" not in plan.summary, "预算不足最小块的项目不该被报成「欠好几个小时」"
        assert "未能安排" not in plan.summary


# --------------------------------------------------------------------------- #
# ③ 边界四条：由唯一那道闸判（不再由均分判）
# --------------------------------------------------------------------------- #


class TestTheGateNotTheSplit:
    """「同一时段不许双订」现在归 :func:`validate_proposal` 管。

    这两格先前是**均分顺带**办到的（它只在空窗里切块，所以撞不上）。本体删了，判据不能
    跟着没 ⇒ 直接钉那道闸：`plan_and_store_day` 写库前过它、`plan_edits.move_plan_item` 也过它，
    同一道闸只此一处（裁定 94：同一事实一份真源）。
    """

    def test_a_block_with_no_reason_is_rejected(self, db_path, conn) -> None:
        """裁定 149④：缺「为什么这么排」= 没留痕，不合格。"""
        from plobi.agenda.llm_planning import PlanRejected, plan_with_llm

        seen: list[str] = []

        def fake_llm(prompt):
            seen.append(prompt)
            return {"items": [], "reason": ""}

        with pytest.raises(PlanRejected) as excinfo:
            plan_with_llm(DAY, fake_llm, conn=conn)
        assert any("为什么这么排" in v for v in excinfo.value.violations)
        assert len(seen) >= 2, "不合格必须带问题清单**重问**，不是问一次就收工"

    def test_two_blocks_in_the_same_slot_are_rejected(self, db_path) -> None:
        from plobi.agenda.llm_planning import validate_proposal

        start, end = _slot(0)
        rows = [
            {"event_id": f"l1:block-{i}", "title": f"同一时段 {i}", "kind": "task",
             "conflict_with": [], "start_at": start.isoformat(), "end_at": end.isoformat(),
             "evidence": {}}
            for i in (1, 2)
        ]

        problems = validate_proposal(rows)
        assert any("同一时段双订" in p for p in problems), problems

    def test_a_clean_proposal_passes_the_same_gate(self, db_path) -> None:
        """对照：上一格不是"任何提案都红"。"""
        from plobi.agenda.llm_planning import validate_proposal

        rows = [{"event_id": "l1:block-1", "title": "推进 · Plobi", "kind": "task",
                 "conflict_with": [], "start_at": _slot(0)[0].isoformat(),
                 "end_at": _slot(0)[1].isoformat(), "evidence": {}}]

        assert validate_proposal(rows) == []

    def test_written_plan_items_never_double_book(self, bare_registry, db_path, conn) -> None:
        """面上那条不双订的关系，最后还得在**落库的行**上成立（闸过了≠写得对）。"""
        for index in range(3):
            _ddl(conn, f"作业{index}", f"{DAY}T{10 + index:02d}:00:00", course="C")

        plan = generate_evening_plan(DAY, db_path=db_path, planner=_proposing_homework())

        _assert_no_double_booking(_items(db_path, plan.id))


# --------------------------------------------------------------------------- #
# ④ 课程是名册数据（裁定 121 二）——这几格与均分无关，原样留着
# --------------------------------------------------------------------------- #

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
        """裁定 153 改判之后这一格该说什么：课程**不再是「无绑定类别」**，但绑定是**条目级**的。

        ⚠️ 这一笔只把断言对齐到**现行落码**，没有裁定两平台该一致这件事——
        `plobi/agents/registry.py` 里 `fe97e046` 自己写的注释是「courses 不进分类默认表
        （`_WINDOWS_DEFAULT_PROJECT_PATHS` 的 courses 格保持空）——没有品类级默认这一说」，
        而 POSIX 那一支因为 `_POSIX_CATEGORY_DIRNAME.get(category, category)` 的默认值
        **落到了 `<home>/Projects/courses`**（不是决定，是 fall-through）。
        两平台现在一个给默认一个不给 ⇒ **要哪一边请点一下**：要么两张表都消掉这个默认
        （改 registry.py，不在本格认领列里），要么把 Windows 那格也补上。
        本格的判据只是"套件全绿 + 别拿旧政策当现行"，所以钉的是**能观察到的形状**，
        并留下这条注释——红翻绿不等于分歧消掉了。
        """
        from plobi.agents.registry import UNBOUND_CATEGORIES, build_default_project_paths

        assert "courses" not in UNBOUND_CATEGORIES, "153 改判：课程可以有本地绑定路径了"
        assert build_default_project_paths(is_windows=True)["courses"] == "", \
            "Windows 那张表里 courses 仍是空格（条目自己的 project_path 永远优先）"
        posix = build_default_project_paths(is_windows=False, home="/home/someone")
        assert "courses" in posix, "这一格必须在读数里出现，不许静默省略"
        # 上一条注释里那处分歧**故意不钉值**：钉成 `== ""` 就是把 Windows 那格的政策
        # 强加给 POSIX，钉成 `== <home>/Projects/courses` 就是给一个 fall-through 盖章。
        # 等两平台一致了再钉值——现在钉哪一种都是在替裁定做决定。

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
        ddl = _ddl(conn, "示波器的使用", f"{DAY}T23:55:00", course=SCHOOL_COURSE)
        assert _matches_project(ddl, SCHOOL_COURSE) is True, "前提：这门课把它认领了"

        plan = generate_evening_plan(
            DAY, db_path=db_path,
            planner=_proposing_homework(
                exclude_paced=True,
                extra_blocks=[_project_block(SCHOOL_COURSE, 6, *_slot(0))],
            ),
        )
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
    plan = generate_evening_plan(DAY, db_path=db_path, planner=_proposing_homework())
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

    before = generate_evening_plan(DAY, db_path=db_path, planner=_proposing_homework(exclude_paced=True))
    before_blocks = [(b.title, b.start_at, b.end_at) for b in _ddl_blocks(db_path, before.id)]

    _CourseRoster.add_course(bare_registry, "电工电子学实验")
    after = generate_evening_plan(DAY, db_path=db_path, planner=_proposing_homework(exclude_paced=True))
    after_blocks = [(b.title, b.start_at, b.end_at) for b in _ddl_blocks(db_path, after.id)]

    assert before_blocks == after_blocks and before_blocks, "加条目不该动排程，也不该测出个空集"
    paced_names = [entry.name for entry, _hours in paced_projects()]
    assert "电工电子学实验" not in paced_names, (
        "不带 pace 的课程不该出现在推进池里——它一出现，作业就会从「无主」被摘走"
    )


def test_a_short_course_name_must_not_steal_the_long_one_s_homework(bare_registry, conn):
    """按课归属不许串台（WP-COLLECT-SCHEDULE-VISIBLE 判据 1；2026-10-15 在这台真库抓到）。

    真库形状：`电工电子学`（2 条作业）与 `电工电子学实验`（2 条作业）是两门课，
    而旧口径拿名册条目名对 `evidence.course` 做子串匹配 ⇒ 短名把长名的作业一起认领走，
    两门课的「下一个 DDL」变成同一块数据（串台 2 条：`evt_1842479535ce`、`evt_3fd8c0dedcf3`）。
    145①(b) 那句「某门课的作业必须派给那门课」在这个形状下必然挂错课，所以钉成回归。
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
    """名册里没人全等那条归属字段时，简称仍然算命中——保住 C5 那条既有契约（不许顺手收紧）。"""
    event = _ddl(conn, "随便一个课件名", "2026-10-19T23:00:00", course="线性代数")
    names = [entry.name for entry in bare_registry.entries()]

    assert "线性代数" not in names, "名册里没有全等者，这条测的才是简称那一档"
    assert _matches_project(event, "线性", other_names=names) is True
