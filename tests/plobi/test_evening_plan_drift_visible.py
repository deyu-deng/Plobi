"""今晚那一轮别再静默跳过：显式钉住 + 漂移要留结构化的一格（裁定 179）。

现读成因：`plobi-evening-plan`（`54a589f0ebb7`）10-18 20:00:08 到点，`last_status=error`，
错因是 #44585 那道配置漂移闸——**守卫本身没错**（不擅自换模型花用户的钱是对的），
错在两件事：那条作业从没被钉过（`provider`/`model` 全空 ⇒ 一直靠"跟着默认走"），
而拒起只进 `last_error`，日程那一屏什么都没有 ⇒ 明天没有计划，没人知道为什么。

两条一起修，不是二选一：
① 出生/启动时**显式钉住当时真的解析得出的那一对**，钉过就不再动（每次启动跟着默认重钉
   ＝把 `unpinned` 换成"看着像钉了"的静默跟随，正是这条裁定划的硬边界）；
② 真漂移了 ⇒ 在作业记录上留 `last_drift`（哪根轴、从什么漂到什么），面上拿结构说话人话。

判据里「不许为了让它跑起来就把守卫关掉」那条由 `tests/cron/test_cron_provider_pin.py`
钉着（未钉的作业照旧拒起）——本档不重复拆它，只钉这两格新形状。
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from cron import jobs
from plobi.butler import nightly

NIGHTLY = nightly.NIGHTLY_PLAN_JOB_NAME
DRIFT_TEXT = (
    "RuntimeError: Skipped to prevent unintended spend: global inference config drifted "
    "since this job was created (provider 'aigw' -> 'custom'; model 'workbuddy/hy3' -> "
    "'workbuddy/space-bunny'), and this job is unpinned. No inference call was made."
)


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("PLOBI_HOME", str(tmp_path))
    monkeypatch.setenv("PLOBI_AGENDA_DB", str(tmp_path / "agenda.db"))
    return tmp_path


# ------------------------------------------------------------------ ① 钉法


def test_the_pin_is_what_actually_resolves_not_a_name_from_config(home, monkeypatch):
    """钉的是运行时解析得出的那个 provider；抄 ``config.yaml`` 里写的名字会钉出一条红条。"""
    monkeypatch.setattr(
        "plobi_cli.runtime_provider.resolve_runtime_provider",
        lambda requested=None: {"provider": "custom", "base_url": "http://127.0.0.1:8000/v1"},
    )
    monkeypatch.setattr(
        "plobi_cli.config.load_config",
        lambda: {"model": {"provider": "wb-gateway", "default": "workbuddy/space-bunny"}},
    )

    assert nightly.current_effective_axes() == {"provider": "custom", "model": "workbuddy/space-bunny"}


def test_an_unreadable_default_pins_nothing_rather_than_half_a_pin(home, monkeypatch):
    """解析不出来 / 读不出默认 ⇒ 什么都不钉。宁缺不编：那一格继续按未钉跑，由 ② 出声。"""
    def boom(*_a, **_kw):
        raise RuntimeError("no provider configured")

    monkeypatch.setattr("plobi_cli.runtime_provider.resolve_runtime_provider", boom)
    assert nightly.current_effective_axes() == {}

    monkeypatch.setattr(
        "plobi_cli.runtime_provider.resolve_runtime_provider",
        lambda requested=None: {"provider": "custom"},
    )
    monkeypatch.setattr("plobi_cli.config.load_config", lambda: {"model": {}})
    assert nightly.current_effective_axes() == {}


def test_a_seeded_job_is_born_pinned(home, monkeypatch):
    """第一次种下去就带着钉——不钉 = 到点第一次就可能被漂移闸静默跳过。"""
    created = []

    class Fake:
        def list_jobs(self, *, include_disabled: bool = False):
            return []

        def create_job(self, **kwargs):
            created.append(kwargs)
            return {"id": "job_1", **kwargs}

    monkeypatch.setattr(nightly, "current_effective_axes",
                        lambda: {"provider": "custom", "model": "workbuddy/space-bunny"})

    assert nightly.ensure_nightly_plan_job(Fake()) == "seeded"
    assert created[0]["provider"] == "custom"
    assert created[0]["model"] == "workbuddy/space-bunny"


def test_align_pins_once_and_never_re_follows_the_default(home, tmp_path, monkeypatch):
    """裁定 179 的硬边界：钉过就不动。跟着默认每次启动重钉＝静默跟随换了个名字。"""
    from plobi.agents.registry import AgentEntry, AgentRegistry

    roster = tmp_path / "projects.yaml"
    entry = AgentEntry(name="agenda", role="l2_agenda", profile="agenda")
    AgentRegistry(path=roster, agents={entry.name: entry}).save()
    monkeypatch.setenv("PLOBI_PROJECTS_CONFIG", str(roster))

    seed = home / "plobi" / "butler_seed.json"
    seed.parent.mkdir(parents=True, exist_ok=True)
    seed.write_text('{"plobi-evening-plan": "2026-10-15T20:00:01"}', encoding="utf-8")
    rows = [{"id": "j", "name": NIGHTLY, "enabled": True, "prompt": nightly.NIGHTLY_PLAN_PROMPT,
             "no_agent": False, "attach_to_session": True, "recipient": "agenda",
             "provider": None, "model": None}]

    class Fake:
        def list_jobs(self, *, include_disabled: bool = False):
            return list(rows)

        def update_job(self, job_id, updates):
            rows[0] = {**rows[0], **updates}
            return rows[0]

    fake = Fake()
    monkeypatch.setattr(nightly, "current_effective_axes",
                        lambda: {"provider": "custom", "model": "workbuddy/space-bunny"})
    assert nightly.align_job_shape(NIGHTLY, fake) == "aligned:model,provider"

    # 默认又漂了：已钉过的那一行**不许被重钉**，漂移由守卫挡下并走 ② 出声。
    monkeypatch.setattr(nightly, "current_effective_axes",
                        lambda: {"provider": "other", "model": "other/new-model"})
    assert nightly.align_job_shape(NIGHTLY, fake) == "already-aligned"
    assert rows[0]["provider"] == "custom" and rows[0]["model"] == "workbuddy/space-bunny"


def test_the_pin_reaches_the_record_through_the_real_store(home, monkeypatch):
    """走真的 ``create_job``：钉上去之后那两个轴就算已钉，快照那一格不该再把这条腿算成未钉。"""
    monkeypatch.setattr(nightly, "current_effective_axes",
                        lambda: {"provider": "custom", "model": "workbuddy/space-bunny"})
    job = jobs.create_job(prompt="排明天", schedule="0 20 * * *", name=NIGHTLY,
                          no_agent=False, provider="custom", model="workbuddy/space-bunny")
    assert job["provider"] == "custom" and job["model"] == "workbuddy/space-bunny"
    assert not job["provider_snapshot"] and not job["model_snapshot"]


# ------------------------------------------------------------------ ② 漂移要出声


def test_a_drift_refusal_leaves_a_structured_field_on_the_record(home):
    """面上要说人话，靠的是结构，不是去解析那句英文。"""
    job = jobs.create_job(prompt="hi", schedule="every 1d", name="drift-probe")
    jobs.mark_job_run(job["id"], success=False, error=DRIFT_TEXT)

    row = jobs.get_job(job["id"])
    assert row["last_status"] == "error"
    axes = {(a["axis"], a["from"], a["to"]) for a in row["last_drift"]["axes"]}
    assert axes == {("provider", "aigw", "custom"), ("model", "workbuddy/hy3", "workbuddy/space-bunny")}


def test_an_ordinary_failure_does_not_fake_a_drift(home):
    """别的红不许被写成"默认模型变了"——那会把用户指向一个不存在的原因。"""
    job = jobs.create_job(prompt="hi", schedule="every 1d", name="not-drift")
    jobs.mark_job_run(job["id"], success=False, error="RuntimeError: provider timeout")
    assert jobs.get_job(job["id"])["last_drift"] is None


def test_a_successful_run_clears_the_previous_drift(home):
    """跑成了，屏上那句「今晚没排」必须跟着消失。"""
    job = jobs.create_job(prompt="hi", schedule="every 1d", name="clear-drift")
    jobs.mark_job_run(job["id"], success=False, error=DRIFT_TEXT)
    assert jobs.get_job(job["id"])["last_drift"] is not None

    jobs.mark_job_run(job["id"], success=True)
    row = jobs.get_job(job["id"])
    assert row["last_drift"] is None and row["last_status"] == "ok"
