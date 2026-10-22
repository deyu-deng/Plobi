"""裁 175：计划行只许引用**真在 ``events`` 里**的那一行（排程先落 entry，再进计划）。

现核铁证（10-18 只读真库副本，真库一个字节没动）：``plan_items`` 全库 94 行里 **74 行查无此行**，
形状是排程自己造的三种假身份——

    project:*     27 行    routine:*   26 行    ddl:evt_*  12 行    l1:block-N  8 行

而 ``evt_ac46920dc0c1`` 那条被反复排的作业在 ``events`` 里 **0 行**。 Desktop 那一半
（``WP-PLAN-ACK-NO-FAKE-CHECK``，Code ``4a2a00ae``）已经把界面改成"查无此行就直说这条我记不了"——
比画个假勾诚实，但用户那一屏还是**点不动**。所以这一族钉的是后一半：**写库那一道门**
（:func:`plobi.agenda.store.replace_plan_items`，四个生产方都从它过）先把每一行的身份落成真的
``events`` 行，再写 ``plan_items``。

三条必须一起成立的性质，各一条测试，全走临时库（零额度，不碰模型）：

① **归零**：过了门之后那一天的孤儿数 = 0（判据原文那条 ``not exists`` 读数）。
② **不重复造身份**：材料侧真有其事（``ddl:evt_x``／采集器 uid／作息模板 id）就**复用那条真事件**，
   不新建第二条——否则同一门课的 ddl 会因为排程写了个前缀变成两条事。
③ **幂等且不抹勾**：同一晚重排拿到的是**同一行** ``events``（id 由「那天 + kind + 标题」推出），
   只跟新钟点走，``completed_at`` 一个字不动（裁 148：完成态是排程输入，不许被重排抹掉）。

外加两颗本刀最容易自己造出来的雷，各钉一条：
④ 排程补出来的那一行**不许变成明晚的素材**（不然"昨晚补的 ddl"今晚被再排一次 = 自我复排，
   正是这条改动最像修好、其实换了个形态的病）；
⑤ 对话改计划（``plan_edits``）整批重写时**不许把已落好的行再复制一份**。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from plobi.agenda import llm_planning, planning, store

DAY = "2026-10-19"
ORPHANS_SQL = """
    SELECT COUNT(*) FROM plan_items i
      JOIN daily_plans p ON p.id = i.plan_id
     WHERE p.for_date = ?
       AND NOT EXISTS (SELECT 1 FROM events e WHERE e.id = i.event_id)
"""
EVENTS_SQL = "SELECT COUNT(*) FROM events"


@pytest.fixture()
def conn(tmp_path):
    connection = store.connect(tmp_path / "agenda.db")
    yield connection
    connection.close()


def _row(title, start_at, event_id, *, kind="task", end_at=None):
    return {
        "title": title,
        "start_at": start_at,
        "end_at": end_at or "",
        "kind": kind,
        # evidence 是 plan_items 的必填格；``planned_from`` 那一格由门自己补。
        "evidence": {"kind": "plan_item", "source": event_id},
        "event_id": event_id,
    }


def _write(conn, rows, *, day=DAY, status="pending"):
    plan = store.upsert_daily_plan(
        conn,
        for_date=day,
        status=status,
        summary="测试用的一份计划",
        event_count=len(rows),
        evidence={"kind": "test"},
    )
    return plan, store.replace_plan_items(conn, plan.id, rows)


def _orphans(conn, day=DAY):
    return int(conn.execute(ORPHANS_SQL, (day,)).fetchone()[0])


def _events(conn):
    return int(conn.execute(EVENTS_SQL).fetchone()[0])


def _invented_rows():
    """今天真在库里的那个形状：三种排程自造身份 + 一条指已被删掉的 ddl。"""
    return [
        _row("第5次课后作业", f"{DAY}T09:00:00", "l1:block-1"),
        _row("午餐", f"{DAY}T12:00:00", "l1:block-2"),
        _row("电工电子学 项目推进", f"{DAY}T14:00:00", "project:ee"),
        _row("第6次课后作业", f"{DAY}T23:55:00", "ddl:evt_ac46920dc0c1", kind="ddl"),
    ]


# ---------------------------------------------------------------- ① 归零


def test_a_the_door_leaves_no_plan_row_pointing_at_nothing(conn):
    """判据原文那条读数：过门之后这一天**孤儿 = 0**，并且每一行都有真身份可勾。

    负例先行：门之前那一步（生产方交进来的形状）确实全是假身份——不然这条只是在测一个空库。
    """
    rows = _invented_rows()
    assert {r["event_id"] for r in rows} == {
        "l1:block-1", "l1:block-2", "project:ee", "ddl:evt_ac46920dc0c1",
    }
    assert _orphans(conn) == 0, "起点库里本来就没有计划行，下面的归零才有意义"
    before = _events(conn)

    plan, written = _write(conn, rows)

    assert _orphans(conn) == 0, "还有计划行指不到 events ⇒ 界面那一屏仍是一片「记不了」"
    assert {w.event_id for w in written} == {w.event_id for w in store.list_plan_items(conn, plan.id)}
    for item in store.list_plan_items(conn, plan.id):
        event = store.get_event(conn, item.event_id)
        assert event is not None, item.event_id
        assert event.title == item.title  # 那一行说的就是这块事，不是随手补的空壳
    # 四行都落了新事件（材料侧一条都没有），并且都说得清是谁造的
    assert _events(conn) == before + 4
    created = store.get_event(conn, store.list_plan_items(conn, plan.id)[0].event_id)
    assert created.evidence["created_by"] == "plan"
    assert created.evidence["planned_from"] == "l1:block-1"
    assert created.evidence["plan_date"] == DAY


# ---------------------------------------------------------------- ② 复用真身份


def test_b_a_real_ddl_is_reused_not_copied(conn):
    """``ddl:evt_x`` 里那个 x 真在 events 里 ⇒ 换成**那条的 id**，不新建、也不留前缀。

    这一条同时钉住另一件：采集器身份存在 ``evidence.uid`` 里的那种（学在浙大那一族）也算同一条事。
    """
    real = store.create_event(
        conn, title="第5次课后作业-(2.3)", start_at=f"{DAY}T23:55:00", kind="ddl",
        status="pending", source="zju",
        evidence={"uid": "evt_ac46920dc0c1", "course": "电工电子学"},
    )
    other = store.create_event(
        conn, title="实验报告", start_at=f"{DAY}T20:00:00", kind="ddl", status="pending",
        source="zju", evidence={"uid": "evt_other_uid"},
    )
    before = _events(conn)

    plan, _written = _write(conn, [
        _row("第5次课后作业-(2.3)", f"{DAY}T23:55:00", f"ddl:{real.id}", kind="ddl"),
        _row("实验报告", f"{DAY}T20:00:00", "ddl:evt_other_uid", kind="ddl"),
    ])

    stored = store.list_plan_items(conn, plan.id)
    assert [s.event_id for s in stored] == [real.id, other.id], (
        "id 直指的与 evidence.uid 对得上的都要复用真事件，一条都不许另起一行"
    )
    assert _events(conn) == before, "复用就不该多出行来"
    assert _orphans(conn) == 0


def test_c_a_deleted_material_gets_a_row_of_its_own_not_a_dead_pointer(conn):
    """材料那条事件**已经不在**（采集器 resync 删掉了）⇒ 补一行真事件，指针不许悬空。

    补出来的那行是"排程结果"的身份（``created_by=plan``），不是采集材料——下一测试就钉这一句。
    """
    _plan, written = _write(conn, [
        _row("第6次课后作业", f"{DAY}T23:55:00", "ddl:evt_ac46920dc0c1", kind="ddl"),
    ])
    event = store.get_event(conn, written[0].event_id)
    assert event is not None and event.kind == "ddl"
    assert event.evidence["planned_from"] == "ddl:evt_ac46920dc0c1"
    assert _orphans(conn) == 0
    # 判据原文要的是"那条作业在 events 里有行"，不是"有一行别的什么"：
    # 自造 id 里那把采集器身份就是它的原身份，补行要按它落。
    assert written[0].event_id == "evt_ac46920dc0c1", written[0].event_id


# ---------------------------------------------------------------- ③ 幂等与勾


def test_d_replanning_the_same_day_reuses_the_row_and_keeps_the_check(conn):
    """重排同一天的同一件事 ⇒ **同一行**，钟点跟着走，``completed_at`` 不许被抹。

    这是裁 148/175 的接缝：完成态住在 events 那一行上，而 ``replace_plan_items`` 先删后插。
    要是身份每次重排都换一行，"勾了就别再排给我"就又变成一次性的了。
    """
    first_plan, first = _write(conn, _invented_rows())
    ids_first = {item.title: item.event_id for item in first}
    target = ids_first["午餐"]
    store.mark_event_completed(conn, target)
    assert store.get_event(conn, target).completed_at

    second_plan, second = _write(
        conn,
        [_row(r["title"], "2026-10-19T13:00:00", r["event_id"], kind=r["kind"]) for r in _invented_rows()],
    )
    ids_second = {item.title: item.event_id for item in second}

    assert ids_second == ids_first, "同一天的同一件事换了钟点就该还是那一条事，不是新建一条"
    assert _events(conn) == len(ids_first), "两次写库不该多出第二套行"
    moved = store.get_event(conn, target)
    assert moved.completed_at, "人承认过的那一刻被重排抹掉了"
    assert moved.start_at.startswith("2026-10-19T13:00"), "钟点要跟着新排的那一版走"
    assert second_plan.id == first_plan.id


# ---------------------------------------------------------------- ④ 不再自我复排


def test_e_a_plan_created_ddl_never_becomes_next_nights_material(conn):
    """排程补出来的那一行 ddl **不许**再进今晚的作业池——否则"补身份"变成"自我复排"。

    负例先行：真材料（采集来的）照样进池，否则这条测试只是在测"池子永远是空的"。
    """
    material = store.create_event(
        conn, title="真的作业", start_at=f"{DAY}T23:00:00", kind="ddl", status="pending",
        source="zju", evidence={"uid": "evt_real_material"},
    )
    _plan, written = _write(conn, [
        _row("补出来的作业", f"{DAY}T23:55:00", "ddl:evt_gone", kind="ddl"),
    ])
    created = store.get_event(conn, written[0].event_id)
    assert created.kind == "ddl" and created.evidence["created_by"] == "plan"

    window_from = datetime.fromisoformat(f"{DAY}T00:00:00")
    window_to = datetime.fromisoformat(f"{DAY}T23:59:59")
    pool = [e.id for e in planning._ddl_candidates(conn, window_from, window_to)]

    assert material.id in pool, "真材料被一起挡掉了——那不是本条要的形状"
    assert created.id not in pool, "计划自己补的行又变成素材 = 每晚自我复排"


# ---------------------------------------------------------------- ⑤ 对话改计划那一路


def test_f_a_rewrite_of_an_already_landed_plan_duplicates_nothing(conn):
    """``plan_edits`` 那种"整批读回来再整批写回去"不许造出第二行事件（门的第一分支）。"""
    _plan, written = _write(conn, _invented_rows()[:2])
    before_events = _events(conn)
    again = [
        {"title": item.title, "start_at": item.start_at, "end_at": item.end_at or "",
         "kind": item.kind, "evidence": item.evidence or {"kind": "plan_item"},
         "event_id": item.event_id}
        for item in written
    ]

    _second_plan, second = _write(conn, again)

    assert [i.event_id for i in second] == [i.event_id for i in written]
    assert _events(conn) == before_events, "已经落成真的行就该原样过，不再补第二份"
    assert _orphans(conn) == 0


def test_h_two_days_of_the_same_title_get_two_rows(conn):
    """同一晚重排=同一行（test_d），**不同天必须两行**。

    这条是写第一版时被顶出来的形状：自造身份若不带日子（同一句 ``l1:block-1`` 两天复用），
    10-19 的"睡觉"和 10-20 的那一条就会撞成同一行——勾一次，两天一起消失。
    身份种子里那一天是必填项（:func:`plobi.agenda.store._plan_event_seed`），这一条钉它真的在。
    """
    other_day = "2026-10-20"
    row_a = _row("睡眠", f"{DAY}T23:30:00", "l1:block-1")
    row_b = _row("睡眠", f"{other_day}T23:30:00", "l1:block-1")

    _plan_a, written_a = _write(conn, [row_a])
    _plan_b, written_b = _write(conn, [row_b], day=other_day)

    assert written_a[0].event_id != written_b[0].event_id, "两天共用一行事件 = 勾一天抹一天"
    assert _events(conn) == 2
    assert _orphans(conn) == 0 and _orphans(conn, other_day) == 0
    # 同一晚再排一次仍是那一行（幂等那半边不许被这条带坏）
    _again, written_again = _write(conn, [row_a])
    assert written_again[0].event_id == written_a[0].event_id


def test_g_the_producer_still_hands_in_invented_ids(conn):
    """钉住"门在生产方下游"这件事：构造口交进来的仍是 ``l1:block-N``。

    没有这一条，上面那六条可以靠"生产方已经不造假 id 了"这种说法悄悄失真——
    现读装配口（:func:`plobi.agenda.llm_planning._block_rows_from_proposal`）仍按位置编号，
    所以归零靠的是那道门，不是靠上游哪天顺手改了写法。
    """
    rows = llm_planning._block_rows_from_proposal(
        [{"title": "写论文", "start_at": f"{DAY}T09:00:00", "end_at": f"{DAY}T11:00:00"}],
        "上午脑子好。",
        sources=[],
    )
    assert [r["event_id"] for r in rows] == ["l1:block-1"], rows
    assert _orphans(conn) == 0  # 还没过门：这一天没有计划行，别把这条当"已经好了"的证据

    _plan, written = _write(conn, rows)
    assert written[0].event_id != "l1:block-1"
    assert store.get_event(conn, written[0].event_id).evidence["planned_from"] == "l1:block-1"
