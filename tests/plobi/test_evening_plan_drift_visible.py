"""今晚那一轮别再静默跳过：漂移要在作业记录上留结构化的一格（裁定 179 的第二半）。

现读成因：`plobi-evening-plan`（`54a589f0ebb7`）10-18 20:00:08 到点，`last_status=error`，
错因是 #44585 那道配置漂移闸——**守卫本身没错**（不擅自换模型花用户的钱是对的），
错在两件事：那条作业从没被钉过（`provider`/`model` 全空 ⇒ 一直靠"跟着默认走"），
而拒起只进 `last_error`，日程那一屏什么都没有 ⇒ 明天没有计划，没人知道为什么。

原裁定有两条一起修：① 出生/启动时显式钉住（`plobi/butler/nightly.py` 的
`current_effective_axes` / `align_job_shape`）；② 真漂移了留 `last_drift`。
**① 那半 2026-10-21 随那层私有播种一起删掉了**（WP-BUTLER-SEED-REMOVAL：作业只在
通用 Cron 页里建，钉不钉、钉哪个由用户在那里选，代码不再替他钉），本档因此只钉 ②——
它是通用 `cron.jobs` 的能力，与谁建作业无关。

判据里「不许为了让它跑起来就把守卫关掉」那条由 `tests/cron/test_cron_provider_pin.py`
钉着（未钉的作业照旧拒起）——本档不重复拆它。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from cron import jobs

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
