"""WP-RISK-TWO-LEVELS — 风险分级以契约为准拆成 6 级（裁定 160）。

改之前的实况（现核）：``lib/risk.py`` 只有 5 级；``L1a_organize`` / ``L1b_destructive_local``
在代码里 0 命中，``RiskLevel.parse("L1b_destructive_local")`` 直接抛 ``ValueError``；
``can_run_at_night(allow_l1=True)`` 把**整格** L1 放过夜——里面含着删文件、改开机项、
装卸软件、改 ``.env``、``git push``。这三份文件在改之前**一条测试都没有**，所以本档每条
都写成会红的断言，不是把现状描一遍。

真家 ``plobi/tasks.json`` 此刻不存在（风险队列本机从未落过任务），所以把"没声明"判到重的
那格没有存量冲击——这也是本刀敢收紧的凭据。
"""

from __future__ import annotations

import importlib
import inspect
import json
import pathlib
import sys
import types

import pytest

LIB = pathlib.Path(__file__).resolve().parents[2] / "plugins" / "plobi-north-star" / "lib"
_PKG = "plobi_ns_lib_under_test"

#: 契约原文：``Docs/specs/risk-and-gates.md`` §RiskLevel 那张表的第一列
CONTRACT_LEVELS = [
    "L0_observe",
    "L1a_organize",
    "L1b_destructive_local",
    "L2_external_msg",
    "L3_money_or_irreversible",
    "L4_self_modify",
]


def _lib():
    """按一个临时包名加载这三份——``queue.py`` 用相对 import，得给它们共同的父包。"""
    pkg = sys.modules.get(_PKG)
    if pkg is None:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(LIB)]  # type: ignore[attr-defined]
        sys.modules[_PKG] = pkg
    return (
        importlib.import_module(f"{_PKG}.risk"),
        importlib.import_module(f"{_PKG}.queue"),
        importlib.import_module(f"{_PKG}.night"),
    )


@pytest.fixture(scope="module")
def lib():
    return _lib()


@pytest.fixture()
def risk(lib):
    return lib[0]


@pytest.fixture()
def queue_mod(lib):
    return lib[1]


@pytest.fixture()
def night_mod(lib):
    return lib[2]


@pytest.fixture()
def queue(queue_mod, tmp_path):
    return queue_mod.TaskQueue(path=tmp_path / "tasks.json")


def _legacy_row(task_id: str, risk_value: str) -> dict:
    return {
        "id": task_id, "goal": "轮转日志", "risk": risk_value, "status": "queued",
        "domain": "generic", "stage": "intake", "awaiting_human": False, "summary": "",
        "result_summary": "", "route": "", "surface": "",
        "created_at": "2026-10-16T00:00:00+00:00", "updated_at": "2026-10-16T00:00:00+00:00",
        "meta": {},
    }


# ── 必做 1 / 2：枚举与解析 ────────────────────────────────────────────────────


def test_the_enum_is_the_contract_table_not_five_levels_anymore(risk):
    assert [m.value for m in risk.RiskLevel] == CONTRACT_LEVELS


@pytest.mark.parametrize("written", ["L1b_destructive_local", "L1B_DESTRUCTIVE_LOCAL", "L1b"])
def test_the_new_destructive_level_parses_today_it_threw(risk, written):
    assert risk.RiskLevel.parse(written) is risk.RiskLevel.L1B_DESTRUCTIVE_LOCAL


@pytest.mark.parametrize("written", ["L1a_organize", "L1A_ORGANIZE", "L1a"])
def test_the_new_organize_level_parses(risk, written):
    assert risk.RiskLevel.parse(written) is risk.RiskLevel.L1A_ORGANIZE


@pytest.mark.parametrize("legacy", ["L1_local_mutate", "L1_LOCAL_MUTATE", " l1_local_mutate "])
def test_the_legacy_value_still_parses_and_lands_on_l1a(risk, legacy):
    """裁定 160 逐字：存量不许解析失败，旧那一格历史上就是「整理类」。"""
    assert risk.RiskLevel.parse(legacy) is risk.RiskLevel.L1A_ORGANIZE


@pytest.mark.parametrize("undeclared", [None, "", "   ", "L1", "l1", "L1"])
def test_undeclared_risk_lands_on_the_heavy_level_not_the_free_one(risk, undeclared):
    """「裸 ``L1`` 落到哪一级」必须钉死：没说是轻是重，就按重的挂起等人批。"""
    assert risk.RiskLevel.parse(undeclared) is risk.RiskLevel.L1B_DESTRUCTIVE_LOCAL


@pytest.mark.parametrize("bogus", ["probably-fine", "L1_LOCAL", "L5", "L1c"])
def test_a_string_that_is_not_a_level_still_raises(risk, bogus):
    """不许"看着像就放行"——表外写法照旧抛。"""
    with pytest.raises(ValueError):
        risk.RiskLevel.parse(bogus)


# ── 必做 3：夜跑政策 ──────────────────────────────────────────────────────────


def test_night_table_only_observe_and_organize_pass_by_default(risk):
    table = {m.value: risk.can_run_at_night(m) for m in risk.RiskLevel}
    assert table == {
        "L0_observe": True,
        "L1a_organize": True,          # 契约：可自动
        "L1b_destructive_local": False,  # 本刀收的就是这一格
        "L2_external_msg": False,
        "L3_money_or_irreversible": False,
        "L4_self_modify": False,
    }


def test_no_knob_releases_the_destructive_level_at_night(risk):
    """这条是本刀的全部意义：没有任何参数能让 ``L1b`` 夜里自己跑。"""
    knobs = [p for p in inspect.signature(risk.can_run_at_night).parameters if p != "risk"]
    assert knobs == ["allow_l1a_night"], knobs
    for value in (True, False):
        assert risk.can_run_at_night(
            risk.RiskLevel.L1B_DESTRUCTIVE_LOCAL, allow_l1a_night=value
        ) is False


def test_the_old_permissive_param_name_fails_loudly(risk):
    """照旧写 ``allow_l1=...`` 要 TypeError，而不是悄悄沿用「整格 L1 过夜」的旧语义。"""
    with pytest.raises(TypeError):
        risk.can_run_at_night(risk.RiskLevel.L1A_ORGANIZE, allow_l1=True)  # type: ignore[call-arg]


def test_requires_human_now_covers_the_destructive_half(risk):
    assert [m.value for m in risk.RiskLevel if risk.requires_human(m)] == CONTRACT_LEVELS[2:]


# ── 必做 4：调用点 ────────────────────────────────────────────────────────────


def test_destructive_task_is_blocked_on_arrival(queue):
    rec = queue.enqueue("把回收站清空", risk="L1b_destructive_local")
    assert (rec.status, rec.awaiting_human) == ("blocked_awaiting_human", True)


def test_destructive_work_is_held_and_runs_once_a_human_approves(queue):
    """必做 5 那一条：夜里被挂起、**人批之后才跑**（批了不必等到早晨）。"""
    rec = queue.enqueue("改开机项", risk="L1b_destructive_local")
    assert queue.claim_runnable(night=True) is None      # 夜里没批过 → 不领
    assert queue.claim_runnable(night=False) is None     # 白天没批过 → 也不领

    approved = queue.update(rec.id, status="queued", awaiting_human=False)
    assert (approved.meta or {}).get("human_released") is True

    claimed = queue.claim_runnable(night=True)
    assert claimed is not None and claimed.id == rec.id


def test_organize_work_runs_at_night_until_the_knob_is_off(queue_mod, tmp_path):
    first = queue_mod.TaskQueue(path=tmp_path / "a.json")
    first.enqueue("归档本月日志", risk="L1a_organize")
    assert first.claim_runnable(night=True) is not None

    second = queue_mod.TaskQueue(path=tmp_path / "b.json")
    rec = second.enqueue("整理缓存", risk="L1a_organize")
    assert second.claim_runnable(night=True, allow_l1a_night=False) is None
    assert second.get(rec.id).status == "blocked_awaiting_human"


def test_legacy_rows_on_disk_do_not_stall_the_night_queue(tmp_path, queue_mod):
    """存量兼容不只是 ``parse`` 吃得下：盘上那行若还写着旧值，夜里要照常领得走。"""
    path = tmp_path / "tasks.json"
    path.write_text(json.dumps({"tasks": [_legacy_row("tsk_legacy", "L1_local_mutate")]}),
                    encoding="utf-8")
    q = queue_mod.TaskQueue(path=path)
    claimed = q.claim_runnable(night=True)
    assert claimed is not None and claimed.id == "tsk_legacy"


def test_a_queued_destructive_row_is_held_by_the_night_gate_itself(tmp_path, queue_mod):
    """绕过 ``enqueue`` 直接落盘的 ``L1b`` 行也必须被夜闸挡住。

    创建即挂起是第一道，这一条钉的是第二道——不然只要有一行不是走 ``enqueue`` 进来的
    （导入、迁移、别的线写的），重活就在半夜自己跑了。
    """
    row = _legacy_row("tsk_destructive_row", "L1b_destructive_local")
    path = tmp_path / "tasks.json"
    path.write_text(json.dumps({"tasks": [row]}), encoding="utf-8")
    q = queue_mod.TaskQueue(path=path)
    assert q.claim_runnable(night=True) is None
    assert q.get("tsk_destructive_row").status == "blocked_awaiting_human"


def test_a_row_with_no_risk_at_all_is_treated_as_destructive(tmp_path, queue_mod):
    """缺字段＝没声明，落重的那格；不许当成"大概能自动"。"""
    row = _legacy_row("tsk_no_risk", "L1_local_mutate")
    del row["risk"]
    row["status"] = "queued"
    path = tmp_path / "tasks.json"
    path.write_text(json.dumps({"tasks": [row]}), encoding="utf-8")
    q = queue_mod.TaskQueue(path=path)
    assert q.claim_runnable(night=True) is None
    assert q.get("tsk_no_risk").status == "blocked_awaiting_human"


# ── 必做 5：夜跑那一轮对外说得出什么 ──────────────────────────────────────────


def test_tick_and_morning_report_agree_that_l1b_awaits_you(night_mod, queue, monkeypatch):
    monkeypatch.setattr(night_mod.NightPolicy, "is_night", lambda self, now=None: True)
    rec = queue.enqueue("git push 预备", risk="L1b_destructive_local")

    tick = night_mod.tick_night_queue(queue, policy=night_mod.NightPolicy())
    assert tick["is_night"] is True and tick["claimed"] is None

    report = night_mod.morning_report(queue, policy=night_mod.NightPolicy())
    assert [t["id"] for t in report["awaiting_human"]] == [rec.id]
    assert "[L1b_destructive_local]" in report["markdown"]


def test_night_policy_has_no_bucket_that_can_be_left_open(night_mod, risk):
    policy = night_mod.NightPolicy()
    assert hasattr(policy, "allow_l1a_night") and not hasattr(policy, "allow_l1")
    for level in risk.RiskLevel:
        auto = risk.can_run_at_night(level, allow_l1a_night=policy.allow_l1a_night)
        assert auto is (level in (risk.RiskLevel.L0_OBSERVE, risk.RiskLevel.L1A_ORGANIZE)), level
