"""裁定 163（10-16 22:5x）：「不要用脚本排程。让日程秘书自己排一次明天的计划。要落到真实的 entry 里面。」

这一档钉的是**秘书嘴上那一发**（:func:`plobi.agenda.llm_planning.plan_and_store_day`）的三条口径：

1. **过闸才落库**——排出来的东西必须真进 `daily_plans` + `plan_items`，每块 `evidence` 带
   `planner`（谁排的）与 `source`（指回材料里哪门课的哪份作业 / 哪个项目）。判据是**读库**，
   不是截图、不是返回值。
2. **不过闸就照实拒**：同时段双订 / 挤掉作息锚点 / 没交「为什么这么排」⇒ 一句人话点名 +
   **零写入**（库里的计划一个字节不动），并且**不许降级回均分**（裁定 149／161③）。
3. **这条路不经过脚本**：`planning.generate_evening_plan` 一次都不许被叫到（脚本排程已由
   另一路退役；本档只保证秘书这张嘴自己不走那条线）。

全程假 planner / 假 llm + `tmp_path` 里的 `agenda.db`，**没有一发真模型、没有一处写真家目录**。

跑法：``bash scripts/run_tests.sh tests/plobi/test_nightly_plan_by_agent.py``（唯一入口，禁直调 pytest）。
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

from plobi.agenda import llm_planning, planning, store

TOMORROW = (date.today() + timedelta(days=1)).isoformat()
COURSE = "高等数学"
PROJECT = "Plobi"


def _load_master_tools():
    """插件目录带连字符，按文件路径加载 master_tools.py（同 test_l1_agenda_mutate）。"""
    import importlib.util

    path = (
        Path(__file__).resolve().parents[2]
        / "plugins" / "plobi-north-star" / "master_tools.py"
    )
    spec = importlib.util.spec_from_file_location("plobi_mouth_master_tools", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def conn(tmp_path):
    connection = store.connect(tmp_path / "agenda.db")
    yield connection
    connection.close()


@pytest.fixture()
def bare_registry(tmp_path, monkeypatch):
    """名册上只有一个带 pace 的项目——`plan_materials` 的 `project_pace` 那一维要有真东西。"""
    from plobi.agents.registry import AgentEntry, AgentRegistry

    registry = AgentRegistry(path=tmp_path / "projects.yaml")
    registry.upsert(
        AgentEntry.from_dict(
            PROJECT, {"role": "l2_project", "description": PROJECT, "pace": {"weekly_hours": 6}}
        )
    )
    monkeypatch.setattr("plobi.agents.registry.load_registry", lambda path=None: registry)
    return registry


def _seed_a_real_day(conn) -> str:
    """明天的真材料：一节有结束的课 + 一份没结束的作业 + 两条作息锚点。

    作息锚点必须 enable：清醒窗（边界三）与「不许挤掉吃饭睡觉」（边界一）都是从这些模板算的，
    没有它们就只能拿整点当天当窗口，那道闸就只验了一半。
    """
    klass = store.create_event(
        conn, title=COURSE, start_at=f"{TOMORROW}T08:00:00",
        end_at=f"{TOMORROW}T08:40:00", kind="class", source="timetable",
        evidence={"uid": "t-math", "course": COURSE},
    )
    ddl = store.create_event(
        conn, title=f"{COURSE} 第三次作业", start_at=f"{TOMORROW}T20:00:00", kind="ddl",
        status="pending", source="zju", evidence={"course": COURSE},
    )
    store.upsert_routine_template(
        conn, template_id="seed-sleep", title="睡眠",
        start_time="23:30", end_time="07:30", enabled=True,
    )
    store.upsert_routine_template(
        conn, template_id="seed-lunch", title="午餐",
        start_time="12:00", end_time="12:40", enabled=True,
    )
    return str(ddl.id), str(klass.id)


def _good_proposal(_conn, day=TOMORROW):
    """一份该过闸的提案：两块都在清醒窗内、都不压午餐、都指得回材料。"""
    return (
        [
            {"title": f"{COURSE} 第三次作业", "start_at": f"{day}T09:00:00",
             "end_at": f"{day}T11:00:00"},
            {"title": f"{PROJECT} 桌面端推进", "start_at": f"{day}T14:00:00",
             "end_at": f"{day}T16:00:00"},
        ],
        "上午脑子最好，先把要交的作业清掉；下午留整段给项目，晚饭前收工。",
    )


def _double_booked(_conn, day=TOMORROW):
    """同时段双订——边界二点名要用的那一版。"""
    return (
        [
            {"title": "作业块", "start_at": f"{day}T14:00:00", "end_at": f"{day}T16:00:00"},
            {"title": "项目块", "start_at": f"{day}T15:00:00", "end_at": f"{day}T17:00:00"},
        ],
        "两块都想要下午，先写上再说。",
    )


def _squeezes_lunch(_conn, day=TOMORROW):
    """挤掉作息锚点——边界一（同一条闸的另一条边，证明闸不是只查双订）。"""
    return (
        [{"title": "长会", "start_at": f"{day}T11:00:00", "end_at": f"{day}T13:00:00"}],
        "午饭那点地方正好拿来开会。",
    )


def _row_count(conn, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _tomorrow_items(conn):
    plan = store.get_daily_plan(conn, TOMORROW)
    assert plan is not None, "说明秘书这一发根本没落到 daily_plans"
    items = store.list_plan_items(conn, plan.id)
    return plan, [item for item in items if str(item.start_at)[:10] == TOMORROW]


# ---------------------------------------------------------------- ① 落到真实的 entry


def test_a_the_round_lands_in_the_database(conn, bare_registry):
    """走通那一轮之后**读库**：明天那天的 plan_items 有行，排的每块都署名。"""
    _seed_a_real_day(conn)
    assert _row_count(conn, "plan_items") == 0, "起点必须是干净的，否则下面的断言没有信息量"

    result = llm_planning.plan_and_store_day(TOMORROW, conn=conn, proposal_fn=_good_proposal)

    assert result["ok"] is True, result
    plan, tomorrow = _tomorrow_items(conn)
    assert len(tomorrow) > 0, "说明「落到真实 entry」这句没有兑现"

    scheduled = [item for item in tomorrow if (item.evidence or {}).get("planner")]
    assert len(scheduled) == 2, [dict(item.evidence) for item in tomorrow]
    for item in scheduled:
        assert item.evidence["planner"] == "l1", item.evidence
        assert item.evidence.get("source"), f"这块没人知道指回哪一条材料：{item.evidence}"
        assert item.evidence["reason"], "裁定 149④：为什么这么排要留在块上"
    sources = {item.evidence["source"] for item in scheduled}
    assert any(s.startswith("ddl:") for s in sources), f"作业块要指回那份作业：{sources}"
    assert f"project:{PROJECT}" in sources, f"项目块要指回名册上那个项目：{sources}"
    assert not any("对不上" in s for s in sources), f"每块都要指得回材料：{sources}"

    # 表头那两位也照样署名（治理线与面上「是谁排的」读的就是这一格）。
    assert plan.evidence["planner"] == "l1"
    assert plan.evidence["source"] == "l1:secretary_mouth"
    assert plan.evidence["kind"] == llm_planning.MOUTH_PLAN_EVIDENCE_KIND
    assert plan.evidence["block_count"] == 2
    # 作业块与项目块**分开计数**：来源指回作业的那块必须进 ddl_block_count，否则面上与治理线
    # 读到的永远是「0 个作业块」——那正是 10-16 那份 block_count=0 读数的另一种假。
    assert plan.evidence["ddl_block_count"] == 1, plan.evidence
    assert plan.evidence["project_block_count"] == 1, plan.evidence
    by_kind = {(item.evidence or {}).get("kind") for item in scheduled}
    assert {planning.DDL_BLOCK_EVIDENCE_KIND, planning.PROJECT_BLOCK_EVIDENCE_KIND} <= by_kind, by_kind
    assert plan.status == "pending"

    # 嘴没有把真日程抹掉：课与作业、作息锚点仍在同一份计划里。
    assert any(str(item.event_id).startswith("evt_") for item in tomorrow), "课表行没了"
    assert any((item.evidence or {}).get("kind") == "routine" for item in tomorrow), "锚点行没了"


def test_b_the_default_route_uses_the_injected_llm_not_a_real_one(conn, bare_registry, monkeypatch):
    """生产形状（`plan_with_llm` 那一发）用**假 llm** 也能落库；`default_llm_call` 一碰就炸。"""
    calls: list[str] = []

    def fake_llm(prompt: str) -> str:
        calls.append(prompt)
        items, reason = _good_proposal(None)
        return json.dumps({"items": items, "reason": reason}, ensure_ascii=False)

    def _no_real_client(*args, **kwargs):
        raise AssertionError("测试里绝不许构造真模型客户端")

    monkeypatch.setattr(llm_planning, "default_llm_call", _no_real_client)
    _seed_a_real_day(conn)

    result = llm_planning.plan_and_store_day(TOMORROW, conn=conn, llm_call=fake_llm)

    assert result["ok"] is True, result
    assert len(calls) == 1, "一轮就该一发；打第二发说明没接过闸的提案又被重问"
    assert calls[0].startswith("你来排明天"), "材料口与提示没走同一处"
    _plan, tomorrow = _tomorrow_items(conn)
    assert len([item for item in tomorrow if (item.evidence or {}).get("planner") == "l1"]) == 2


# ---------------------------------------------------------------- ② 不过闸 ⇒ 点名 + 零写入


def test_c_double_booking_is_named_and_writes_nothing(conn, bare_registry):
    """同时段双订 ⇒ 照实拒 + 一个字节都不写（不许写一版"差不多"的）。"""
    _seed_a_real_day(conn)
    plans_before = _row_count(conn, "daily_plans")
    items_before = _row_count(conn, "plan_items")

    result = llm_planning.plan_and_store_day(TOMORROW, conn=conn, proposal_fn=_double_booked)

    assert result["ok"] is False, result
    assert result["written"] == 0
    assert result["status"] == "rejected"
    assert _row_count(conn, "daily_plans") == plans_before
    assert _row_count(conn, "plan_items") == items_before
    assert store.get_daily_plan(conn, TOMORROW) is None
    # 那句人话要能点名：哪两块、什么毛病、以及「没写」这个事实。
    reason = result["reason"]
    assert "作业块" in reason and "项目块" in reason, reason
    assert "同一时段双订" in reason, reason
    assert "一个字没写" in reason, reason
    assert "均分" not in reason or "也不换回均分" in reason, reason


def test_d_squeezing_the_lunch_anchor_is_refused_with_zero_writes(conn, bare_registry):
    """同一条闸的第二条边：挤掉作息锚点也是整笔不写（闸不是只查双订）。"""
    _seed_a_real_day(conn)

    result = llm_planning.plan_and_store_day(TOMORROW, conn=conn, proposal_fn=_squeezes_lunch)

    assert result["ok"] is False and result["written"] == 0, result
    assert store.get_daily_plan(conn, TOMORROW) is None
    assert "午餐" in result["reason"], result["reason"]
    assert "挤掉了作息锚点" in result["reason"], result["reason"]


def test_e_a_proposal_without_reason_is_refused_with_zero_writes(conn, bare_registry):
    """裁定 149④ 的留痕：没交「为什么这么排」= 不合格，同样一个字不写。"""
    _seed_a_real_day(conn)
    items, _reason = _good_proposal(None)

    result = llm_planning.plan_and_store_day(
        TOMORROW, conn=conn,
        proposal_fn=lambda _c, _d: (items, "   "),
    )

    assert result["ok"] is False and result["status"] == "no_reason", result
    assert result["written"] == 0
    assert store.get_daily_plan(conn, TOMORROW) is None


def test_f_a_channel_that_answers_nothing_writes_nothing(conn, bare_registry, monkeypatch):
    """通道挂了 / 解析不出来：照实说没排成，**不降级回均分**，也不留半份计划。"""
    def _no_answer(_prompt: str) -> str:
        raise RuntimeError("排程那一发没拿到回答（额度路空返回）")

    monkeypatch.setattr(llm_planning, "default_llm_call", lambda *a, **k: _no_answer)
    _seed_a_real_day(conn)

    result = llm_planning.plan_and_store_day(TOMORROW, conn=conn)

    assert result["ok"] is False and result["status"] == "channel_error", result
    assert "没排成" in result["reason"] and "一个字没写" in result["reason"]
    assert store.get_daily_plan(conn, TOMORROW) is None
    assert _row_count(conn, "plan_items") == 0, "挂了还多出块＝掉回均分"


def test_g_no_materials_does_not_ask_the_model_and_writes_nothing(conn, monkeypatch, tmp_path):
    """裁定 161：没有任何可排材料时不许打模型，也不许凭空排出块来。"""
    from plobi.agents.registry import AgentRegistry

    hits: list[str] = []

    def _never(_prompt: str) -> str:
        hits.append("called")
        return ""

    # 名册也是材料的一维（project_pace）——空库就得配一个空名册，否则「没材料」这句不成立。
    monkeypatch.setattr("plobi.agents.registry.load_registry",
                        lambda path=None: AgentRegistry(path=tmp_path / "projects.yaml"))
    monkeypatch.setattr(llm_planning, "default_llm_call", lambda *a, **k: _never)

    result = llm_planning.plan_and_store_day(TOMORROW, conn=conn)

    assert hits == [], "空材料还去打模型＝花真钱问『明天没什么可排』"
    assert result["ok"] is False and result["status"] == "nothing_to_schedule", result
    assert store.get_daily_plan(conn, TOMORROW) is None


def test_h_a_confirmed_plan_is_not_overwritten_by_the_mouth(conn, bare_registry):
    """人确认过的那一天不归嘴管（与 `generate_evening_plan` 同一口径）；改一块走 move_block。"""
    _seed_a_real_day(conn)
    assert llm_planning.plan_and_store_day(TOMORROW, conn=conn,
                                           proposal_fn=_good_proposal)["ok"] is True
    plan = store.get_daily_plan(conn, TOMORROW)
    store.set_plan_status(conn, plan.id, "confirmed")

    result = llm_planning.plan_and_store_day(TOMORROW, conn=conn,
                                             proposal_fn=_double_booked)

    assert result["ok"] is False and result["status"] == "confirmed_plan", result
    after = store.get_daily_plan(conn, TOMORROW)
    assert after.id == plan.id and after.status == "confirmed"
    assert len(store.list_plan_items(conn, after.id)) == len(store.list_plan_items(conn, plan.id))


# ---------------------------------------------------------------- ③ 这条路不经过脚本


def test_i_the_mouth_never_goes_through_the_evening_script(conn, bare_registry, monkeypatch):
    """判据③：秘书这一发**不调** `generate_evening_plan`（脚本那条线由另一路退役）。"""
    hits: list[str] = []

    def _boom(*args, **kwargs):
        hits.append("generate_evening_plan")
        raise AssertionError("排程开在秘书嘴上，这一发不许走脚本那份计划管线")

    monkeypatch.setattr(planning, "generate_evening_plan", _boom)
    _seed_a_real_day(conn)

    result = llm_planning.plan_and_store_day(TOMORROW, conn=conn, proposal_fn=_good_proposal)

    assert result["ok"] is True, result
    assert hits == [], "走到了脚本那份计划管线＝又回到「脚本排程」"
    assert "cron_evening_plan" not in sys.modules, "嘴上这一发把排程脚本 import 进来了"
    _plan, tomorrow = _tomorrow_items(conn)
    assert len(tomorrow) > 0


# ---------------------------------------------------------------- 嘴的接线（动作真的通向那一步）


@pytest.fixture()
def mouth_plugin(tmp_path, monkeypatch):
    """把插件 handler 与规划器都接在 tmp 的那份库上——**绝不碰真家目录**。"""
    real_connect = store.connect
    db = tmp_path / "agenda.db"
    monkeypatch.setattr(store, "connect", lambda path=None: real_connect(db))
    return _load_master_tools(), real_connect(db)


def test_j_the_secretary_action_calls_the_mouth_not_the_agenda_l2(mouth_plugin, bare_registry, monkeypatch):
    """`action=plan_tomorrow` 必须通向 `plan_and_store_day`，并且**不进** `run_secretary_ask`。"""
    MT, conn = mouth_plugin
    _seed_a_real_day(conn)
    seen: list[str] = []
    real_writer = llm_planning.plan_and_store_day

    def _spy(day: str, **kwargs):
        assert "conn" in kwargs, "handler 没把库接上，规划器会自己去开真家目录那份"
        seen.append(day)
        return real_writer(day, conn=conn, proposal_fn=_good_proposal)

    def _never(*args, **kwargs):
        raise AssertionError("plan_tomorrow 走回日程 L2 / 脚本那条线＝裁定 163 没收住")

    monkeypatch.setattr(llm_planning, "plan_and_store_day", _spy)
    monkeypatch.setattr("plobi.agents.registry.run_secretary_ask", _never)

    payload = json.loads(MT.handle_secretary_ask({
        "intent": "plan_day", "action": "plan_tomorrow",
        "user_text": "你自己把明天排一次", "date": TOMORROW,
    }))

    assert seen == [TOMORROW], seen
    assert payload["ok"] is True and payload["action"] == "plan_tomorrow", payload
    assert payload["block_count"] == 2
    assert store.list_plan_items(conn, payload["plan_id"]), "回话说落库了，读库却是空的"


def test_k_the_mouth_refusal_is_quoted_not_reported_as_success(mouth_plugin, bare_registry, monkeypatch):
    """那一发没排成时，handler 带回的必须是 `ok=false` + 那句人话，而且库里什么都没有。"""
    MT, conn = mouth_plugin
    _seed_a_real_day(conn)

    def _no_answer(_prompt: str) -> str:
        raise RuntimeError("排程那一发没拿到回答（额度路空返回）")

    monkeypatch.setattr(llm_planning, "default_llm_call", lambda *a, **k: _no_answer)

    payload = json.loads(MT.handle_secretary_ask({
        "intent": "mutate_agenda", "action": "plan_tomorrow",
        "user_text": "把明天重排一遍", "date": TOMORROW,
    }))

    assert payload["ok"] is False, payload
    assert payload["written"] == 0
    assert store.get_daily_plan(conn, TOMORROW) is None, "嘴上这一发挂了还把东西写进去了"
    assert "一个字没写" in payload["reason"], payload["reason"]
    assert payload.get("error"), "ok=false 却没带 error 字段＝面上会被念成成功"
