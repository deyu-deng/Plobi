"""「明天计划每天真跑」这一格的测档（WP-SCHEDULE-REAL-PLAN 判据 1 / 3 / 4）。

底座一直在（ADR-0012 把 20:00 规划定成日程域 ``no_agent`` cron，脚本与注册器都在），
缺的是**没有人种下那条作业**：这台实测 ``jobs.json`` 不存在，而 ticker 每 60 秒一跳、
两枚 heartbeat 都是新的。所以本档钉的是「种下去 + 跑失败要说话 + 夜里那一发的模型门（裁定 161）」，不是重排算法。

三条口径：
- 种一次之后不再管 —— 用户删掉/关掉就是他的决定（同「作息模板」那条纪律）。
- marker 只在真成功之后落盘；半途失败下次启动重试。
- 「明天没东西可排」不是失败（走 ``status=empty`` 那句既有话），库锁与未知异常才是。
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]

from plobi.agenda import store
from plobi.butler import nightly
from plobi.notify.base import SendOutcome

NIGHTLY = nightly.NIGHTLY_PLAN_JOB_NAME


def _load_planner_script():
    """按路径加载 ``scripts/plobi/cron_evening_plan.py``（scripts/ 不是包）。"""
    spec = importlib.util.spec_from_file_location(
        "cron_evening_plan_under_test", REPO / "scripts" / "plobi" / "cron_evening_plan.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _tomorrow() -> str:
    """脚本自己算「明天」，所以种的事件也必须按真明天 —— 钉死日期就是定时炸弹。"""
    return (date.today() + timedelta(days=1)).isoformat()


@dataclass
class FakeJob:
    id: str
    name: str
    script: str = ""
    schedule: dict = field(default_factory=lambda: {"display": ""})
    enabled: bool = True


@dataclass
class FakeCronJobs:
    """替 ``cron.jobs`` 记账：只关心「被叫了几次、以什么参数叫」。"""

    existing: list[FakeJob] = field(default_factory=list)
    created: list[dict[str, Any]] = field(default_factory=list)
    raise_on_create: Exception | None = None

    def list_jobs(self, *, include_disabled: bool = False) -> list[dict[str, Any]]:
        return [
            {
                "id": j.id,
                "name": j.name,
                "script": j.script,
                "schedule": j.schedule,
                "enabled": j.enabled,
            }
            for j in self.existing
        ]

    def create_job(self, **kwargs: Any) -> dict[str, Any]:
        if self.raise_on_create is not None:
            raise self.raise_on_create
        self.created.append(kwargs)
        return {"id": f"job_{len(self.created)}", **kwargs}


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """一个假的 Plobi 家：作业表 marker、``scripts/``、``agenda.db`` 全落在 tmp。"""
    monkeypatch.setenv("PLOBI_HOME", str(tmp_path))
    monkeypatch.setenv("PLOBI_AGENDA_DB", str(tmp_path / "agenda.db"))
    monkeypatch.delenv("PLOBI_BUTLER_POLISH", raising=False)
    return tmp_path


# ---------------------------------------------------------------- 种下去（判据 1 前半）


def test_first_boot_creates_exactly_the_nightly_plan_job(home):
    fake = FakeCronJobs()

    assert nightly.ensure_nightly_plan_job(fake) == "seeded"
    assert len(fake.created) == 1
    job = fake.created[0]
    assert job["name"] == NIGHTLY
    assert job["schedule"] == "0 20 * * *"
    # R-104 / 裁定 154：排程这一条不再是脚本自跑（用户：「第二天的规划的定时任务需要给
    # 日程秘书，就是直接定时发提示词给日程秘书」）。其余四条照旧，见下面那条测试。
    assert job["no_agent"] is False
    assert job["prompt"] and "明日计划" in job["prompt"]
    assert job["attach_to_session"] is True
    assert job["deliver"] == "local"


def test_second_boot_does_nothing(home):
    fake = FakeCronJobs()
    nightly.ensure_nightly_plan_job(fake)
    fake.created.clear()

    assert nightly.ensure_nightly_plan_job(fake) == "already-seeded"
    assert fake.created == []


def test_a_code_change_reaches_the_deployed_copy_on_the_next_boot(home):
    """marker 只挡「再造一条作业」，不挡把脚本副本刷新一遍。

    ticker 跑的是 ``PLOBI_HOME/scripts/`` 里那份复制件；只种一次=以后改了码跑的仍是旧的
    （"代码合了 ≠ 会在跑"那一族）。行的事归用户决定，文件的事归代码。
    """
    fake = FakeCronJobs()
    nightly.ensure_nightly_plan_job(fake)
    deployed = home / "scripts" / "plobi_evening_plan.py"
    deployed.write_text("昨晚那份旧副本\n", encoding="utf-8")

    assert nightly.ensure_nightly_plan_job(fake) == "already-seeded"
    assert "generate_evening_plan" in deployed.read_text(encoding="utf-8")


def test_a_job_the_user_deleted_is_not_resurrected(home):
    """种过之后用户把作业删了 ⇒ 那是他的决定，下次启动不许再造一条。"""
    fake = FakeCronJobs()
    assert nightly.ensure_nightly_plan_job(fake) == "seeded"
    fake.created.clear()

    assert nightly.ensure_nightly_plan_job(FakeCronJobs()) == "already-seeded"
    assert fake.created == []


def test_an_existing_disabled_job_is_left_alone(home):
    """手动注册会把关掉的重新打开（那是有人下了命令）；开机这一种只做「没有→有」。"""
    fake = FakeCronJobs(existing=[FakeJob(id="job_x", name=NIGHTLY, enabled=False)])

    assert nightly.ensure_nightly_plan_job(fake) == "exists"
    assert fake.created == []
    assert fake.existing[0].enabled is False


def test_a_failed_seed_leaves_no_marker_so_next_boot_retries(home):
    fake = FakeCronJobs(raise_on_create=sqlite3.OperationalError("database is locked"))

    with pytest.raises(sqlite3.OperationalError):
        nightly.ensure_nightly_plan_job(fake)
    assert not nightly._seed_marker_path().exists()

    assert nightly.ensure_nightly_plan_job(FakeCronJobs()) == "seeded"


def test_the_script_it_registers_is_the_one_the_scheduler_will_accept(home):
    """ticker 只跑 ``PLOBI_HOME/scripts/`` 里的文件（路径穿越守卫）。

    作业里存的 ``script`` 与真正复制过去的文件名必须对得上，否则这条注册是死的：
    到点只报一句「Script not found」，明天照样没有计划。
    """
    fake = FakeCronJobs()
    nightly.ensure_nightly_plan_job(fake)
    scripts_dir = nightly.copy_butler_scripts(only=["cron_evening_plan.py"])
    landed = scripts_dir / fake.created[0]["script"]

    assert landed.is_file(), landed
    assert "generate_evening_plan" in landed.read_text(encoding="utf-8")


def test_seeding_the_planner_does_not_install_the_other_four(home):
    """本刀只管「明天计划真跑」这一格，不许顺手把早报/digest/额度预警一起推给用户。"""
    nightly.ensure_nightly_plan_job(FakeCronJobs())
    landed = sorted(p.name for p in (home / "scripts").iterdir())

    assert landed == ["plobi_evening_plan.py"]


def test_the_job_table_is_one_source_for_name_and_schedule(home):
    """表搬进产品码之后，脚本名与钟点只剩这一处；ADR-0012 定的是 20:00。"""
    row = next(r for r in nightly.BUTLER_JOBS if r[0] == NIGHTLY)

    assert row[1] == "cron_evening_plan.py"
    assert row[2] == "plobi_evening_plan.py"
    assert row[3] == "0 20 * * *"


def test_the_backend_startup_still_calls_the_seed():
    """判据 1 的「装机自动」全靠这一行接线 —— 被人删掉就是回到"没人敲命令就永远不注册"。

    这条只钉「接线在不在」，不重复证明它会种对：真跑那半的证据在交账里（本机现读
    ``next_run = 2026-10-11T20:00:00+08:00``），因为起一次真 lifespan 要把整只 dashboard 拉起来。
    """
    source = (REPO / "plobi_cli" / "web_server.py").read_text(encoding="utf-8")

    assert "_ensure_nightly_plan_job" in source
    assert "run_in_executor(None, _ensure_nightly_plan_job)" in source


def test_a_broken_home_does_not_take_the_backend_down(home, monkeypatch, caplog):
    """启动那一格必须接住失败并说一句话：种不上是次要的，把后端带崩是大事。"""
    import logging

    from plobi_cli import web_server

    def _broken(*args, **kwargs):
        raise OSError("磁盘满了")

    monkeypatch.setattr(nightly, "ensure_nightly_plan_job", _broken)
    with caplog.at_level(logging.WARNING, logger="plobi_cli.web_server"):
        web_server._ensure_nightly_plan_job()  # 不该抛

    assert "seed skipped" in caplog.text
    assert "磁盘满了" in caplog.text



# ------------------------------------------------------ 跑失败要说话（判据 1 后半）


class _RecordingNotifier:
    def __init__(self, *, configured: bool = True, ok: bool = True) -> None:
        self.configured = configured
        self._ok = ok
        self.bodies: list[str] = []

    def send(self, body: str) -> SendOutcome:
        self.bodies.append(body)
        return SendOutcome(ok=self._ok, detail="")


def _run_planner(monkeypatch, notifier: _RecordingNotifier | None = None):
    module = _load_planner_script()
    sink = notifier or _RecordingNotifier()
    monkeypatch.setattr("plobi.notify.get_notifier", lambda: sink)
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = module.main()
    return rc, out.getvalue(), err.getvalue(), sink


def _last_json(text: str) -> dict[str, Any]:
    return json.loads(text.strip().splitlines()[-1])


def _stored_plan_items(db: Path, day: str) -> list[dict[str, Any]]:
    """纯读库：不碰 ``generate_evening_plan``（那个 helper 会重排，用它当"没被抹"的证据就是自欺）。"""
    conn = store.connect(db)
    try:
        plan = store.get_daily_plan(conn, day)
        return [] if plan is None else [item.to_dict() for item in store.list_plan_items(conn, plan.id)]
    finally:
        conn.close()


def test_locked_db_is_named_as_a_cause_and_exits_non_zero(home, monkeypatch):
    def _locked(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    # 裁定 163 之后脚本只读库，所以"跑失败要说得出为什么"这一支要钉在**读**那一手
    # （以前它钉在 generate_evening_plan 上，那函数已经从这条路径撤下了）。
    monkeypatch.setattr("plobi.agenda.store.connect", _locked)
    rc, out, err, sink = _run_planner(monkeypatch)

    assert rc == 1
    payload = _last_json(out)
    assert payload["cause"] == "db_locked"
    assert "database is locked" in payload["detail"]
    # 同一句也进 stderr：cron 的 no_agent 分支把非零退出的 stderr 拼成告警正文。
    assert payload["cause"] in err
    assert sink.bodies == [], "失败了还推一份正文出去 = 半真半假的播报"


def test_an_unknown_crash_still_names_its_type(home, monkeypatch):
    def _boom(*args, **kwargs):
        raise ValueError("routine_templates 缺 duration_min 这一列")

    monkeypatch.setattr("plobi.agenda.store.connect", _boom)
    rc, out, _err, _sink = _run_planner(monkeypatch)

    assert rc == 1
    payload = _last_json(out)
    assert payload["cause"] == "exception"
    assert payload["detail"].startswith("ValueError:")


def _seed_plan_the_secretary_wrote(db: Path, *, status: str = "pending", items: int = 2) -> str:
    """秘书那一轮已经落过的真 entry。脚本只该读它——**不许**再排一遍。

    为什么这条要单独立：``generate_evening_plan`` 会先 DELETE 当天 ``plan_items`` 再重插，
    脚本一插手就把秘书排好的块抹掉。裁定 163 撤脚本排程的正解就是这个动作。
    """
    conn = store.connect(db)
    try:
        plan = store.upsert_daily_plan(
            conn, for_date=_tomorrow(), status=status, summary="明天由秘书排过一轮",
            conflict_count=0, event_count=items,
        )
        store.replace_plan_items(conn, plan.id, [
            {"id": f"itm_{n}", "title": f"写作业 {n}", "event_id": f"ddl:evt_{n}",
             "start_at": f"{_tomorrow()}T09:{n}0:00", "end_at": f"{_tomorrow()}T09:{n}0:30",
             "kind": "task",
             "evidence": {"kind": "ddl", "planner": "l2_agenda", "source": f"evt_{n}"}}
            for n in range(items)
        ])
        return plan.id
    finally:
        conn.close()


def test_nothing_to_schedule_is_not_reported_as_a_failure(home, monkeypatch):
    """既有口径：**空不是崩**。有一行 ``status=empty`` 的计划时，仍走那句「不是没跑」。

    这行计划由 ``generate_evening_plan()`` 在不给 planner 的情况下落（脚本侧零模型，
    见裁定 163），所以这一趟全程一次模型都不该打——探针就在那儿等着。
    """
    calls = _model_spy(monkeypatch)
    from plobi.agenda.planning import generate_evening_plan

    generate_evening_plan()                       # 没材料 ⇒ 落一行 status=empty，不打模型

    rc, out, _err, sink = _run_planner(monkeypatch)

    assert calls == [], "脚本侧还打模型＝裁定 163 没落地"
    assert rc == 0
    payload = _last_json(out)
    assert payload["plan"]["status"] == "empty"
    assert "不是没跑" in payload["plan"]["summary"]
    assert sink.bodies and "明天没有日程" in sink.bodies[0]


def test_a_missing_plan_row_is_reported_plainly_and_the_script_does_not_fill_it(home, monkeypatch):
    """秘书那一轮没落库 ⇒ 照实说"还没有"，**不许脚本代排**（那会顺手抹掉别人的块）。

    退出码仍是 0：这是"今天没排"，不是崩；崩的那两支（``db_locked`` / ``exception``）在判据 1 里。
    """
    calls = _model_spy(monkeypatch)

    rc, out, _err, _sink = _run_planner(monkeypatch)

    assert rc == 0 and calls == []
    payload = _last_json(out)
    assert payload["outcome"] == "no_plan" and payload["for_date"] == _tomorrow()
    assert "没落库" in payload["summary"]
    conn = store.connect(home / "agenda.db")
    try:
        assert store.get_daily_plan(conn, _tomorrow()) is None, "脚本代排＝偷偷替秘书落了一版"
    finally:
        conn.close()


# ------------------------------------- 模型门 + 幂等（判据 4 已按裁定 161 换对象 / 判据 3）


def _seed_tomorrow_event(db: Path) -> None:
    conn = store.connect(db)
    try:
        store.create_event(
            conn,
            title="组会",
            start_at=f"{_tomorrow()}T10:00:00",
            end_at=f"{_tomorrow()}T11:00:00",
            kind="meeting",
        )
    finally:
        conn.close()


def _model_spy(monkeypatch) -> list[str]:
    calls: list[str] = []

    def _spy(self, *args, **kwargs):
        calls.append("called")
        return None

    monkeypatch.setattr("plobi.quota.route.QuotaAwareCompleter.__call__", _spy)
    return calls


def _polish_spy(monkeypatch) -> list[str]:
    """润色那道门有没有被走到。它是另一个口子，与排程那一发分开数才对得上账。

    （``plobi.butler.polish`` 在包命名空间里被同名函数遮住了，所以要拿模块对象本身改。）
    """
    hits: list[str] = []

    def _spy(*args, **kwargs):
        hits.append("polished")
        return ""

    module = importlib.import_module("plobi.butler.polish")
    monkeypatch.setattr(module, "polish", _spy)
    return hits


def test_the_script_never_touches_a_model_and_never_wipes_the_secretarys_blocks(home, monkeypatch):
    """裁定 163 的主判据：**夜里那一发只属于秘书，脚本一发都不发**。

    两半都要钉：① 探针计数 **= 0**（今晚那 2886 tokens / 0.54 credit 就是这一发花掉的，
    换回一份 ``block_count=0`` 的空计划）；② 秘书已经落库的那些块**一行都不许少**——
    以前脚本走 ``generate_evening_plan`` 会先 DELETE 当天 ``plan_items`` 再重插，
    "顺手补一版"就是抹。润色那道门没开，所以 polish 也必须 0 次。
    """
    _seed_tomorrow_event(home / "agenda.db")
    _seed_plan_the_secretary_wrote(home / "agenda.db", items=2)
    calls = _model_spy(monkeypatch)
    polish_calls = _polish_spy(monkeypatch)
    before = _stored_plan_items(home / "agenda.db", _tomorrow())

    rc, out, _err, _sink = _run_planner(monkeypatch)

    assert rc == 0
    assert calls == [], "脚本还在发模型＝裁定 163 没落地"
    assert polish_calls == []
    after = _stored_plan_items(home / "agenda.db", _tomorrow())
    assert len(before) == 2, before                      # 前置：秘书那两行确实在库里
    assert len(after) == 2, after
    assert {r["title"] for r in after} == {r["title"] for r in before}, "秘书排的块被脚本重排抹掉了"


def test_the_polish_env_gate_is_the_only_way_to_reach_a_model(home, monkeypatch):
    """对照组：润色门一开就必须打到探针上，否则上面那些「0 次」没有信息量。

    裁定 163 之后，脚本里**唯一**还能走到模型的门就是 ``PLOBI_BUTLER_POLISH``；
    探针接的还是额度腿那一个 ``__call__``——所以它一亮就说明"脚本确实还留着一条能花钱的路"，
    而它默认关着。得先有一行计划（脚本不再自己生成，见 ``no_plan`` 那一支）。
    """
    _seed_tomorrow_event(home / "agenda.db")
    _seed_plan_the_secretary_wrote(home / "agenda.db")
    calls = _model_spy(monkeypatch)
    monkeypatch.setenv("PLOBI_BUTLER_POLISH", "1")

    rc, _out, _err, _sink = _run_planner(monkeypatch)

    assert rc == 0
    assert calls, "润色门开着却一次都没调用 ⇒ 探针接错了地方，上一条的零调用是假的"


def _plan_item_rows(db: Path) -> list[dict[str, Any]]:
    """比"排出来的安排"，不比行 id —— ``replace_plan_items`` 每轮重写行，id 必然换。

    「用户改过的不许被抹」那一半保护在**整张计划**这一层（``status=confirmed`` 直接原样返回），
    已由 ``tests/plobi/test_planning.py::test_confirmed_plan_is_not_overwritten`` 钉住；
    本档只补"同一遍内容跑两遍不漂移"这半。
    """
    from plobi.agenda.planning import generate_evening_plan

    plan = generate_evening_plan(_tomorrow(), db_path=db)
    conn = store.connect(db)
    try:
        rows = [item.to_dict() for item in store.list_plan_items(conn, plan.id)]
    finally:
        conn.close()
    for row in rows:
        row.pop("id", None)
    return rows


def test_running_the_same_day_twice_gives_the_same_plan(home):
    """判据 3：定时真跑会重入（catch-up、手动补跑、看门狗抢跑），第二遍不许改已经排好的。"""
    db = home / "agenda.db"
    _seed_tomorrow_event(db)

    first = _plan_item_rows(db)
    assert first, "一行都没排出来，这条幂等就是空的"
    assert _plan_item_rows(db) == first

# ---------------------------------------------------------------- R-104（裁定 154）


def test_only_the_planning_job_becomes_an_agent_turn(home):
    """判据④：只有排程那一条走 Agent。采集不需要推理，套一层 Agent 只多一个失败面。"""
    assert nightly.job_shape("plobi-source-collect") == {"prompt": None, "no_agent": True}
    assert nightly.job_shape("plobi-morning-report")["no_agent"] is True
    assert nightly.job_shape(NIGHTLY)["no_agent"] is False
    assert nightly.job_shape(NIGHTLY)["attach_to_session"] is True


def test_shape_is_realigned_on_every_boot_but_a_disabled_row_is_left_alone(home, tmp_path, monkeypatch):
    """marker 10-15 就落过了 ⇒ 光靠"只种一次"这台永远拿不到新形状：形态归代码，每次启动对齐。
    但**被用户停掉的行不动**（那是他的决定）。孤儿副本禁用不删（判据①）。"""
    seed = home / "plobi" / "butler_seed.json"
    seed.parent.mkdir(parents=True, exist_ok=True)
    seed.write_text(json.dumps({NIGHTLY: "2026-10-15T20:00:01"}), encoding="utf-8")
    orphan = tmp_path / "profiles" / "l2-agenda" / "cron"
    orphan.mkdir(parents=True)
    (orphan / "jobs.json").write_text(
        json.dumps({"jobs": [{"id": "orphan", "name": NIGHTLY, "enabled": True}]}),
        encoding="utf-8")
    monkeypatch.setattr("plobi_constants.get_default_plobi_root", lambda: tmp_path)

    rows = [{"id": "job_old", "name": NIGHTLY, "script": "plobi_evening_plan.py",
             "schedule": "0 20 * * *", "enabled": True,
             "no_agent": True, "prompt": "", "attach_to_session": None}]

    class ShapeFake:
        def list_jobs(self, *, include_disabled: bool = False):
            return list(rows)

        def update_job(self, job_id, updates):
            rows[0] = {**rows[0], **updates}
            return rows[0]

    fake = ShapeFake()
    assert nightly.ensure_nightly_plan_job(fake) == "already-seeded"
    assert rows[0]["no_agent"] is False and rows[0]["attach_to_session"] is True
    assert rows[0]["prompt"] and "明日计划" in rows[0]["prompt"]
    kept = json.loads((orphan / "jobs.json").read_text(encoding="utf-8"))["jobs"][0]
    assert kept["enabled"] is False and "R-104" in kept["disabled_reason"]

    rows[0] = {**rows[0], "enabled": False, "no_agent": True}
    assert nightly.align_job_shape(NIGHTLY, fake) == "left-disabled"
    assert rows[0]["no_agent"] is True
