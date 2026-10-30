"""「停止这一趟」与「错过怎么办」的行为契约（从上游摘过来的两件，按我们的形状重写）。

三条要钉住：
1. 取消标记**只对这一趟有效**——比这趟开始更早写下的标记不许误杀下一趟；
2. 默认行为不变（晚到仍在宽限内照跑）；`catch_up=False` 时**记一笔 missed 并跳过**；
3. 取消落账要写成 `cancelled`，不是 `error`——「停了」和「坏了」是两句不同的话。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from cron import cancellation
from cron.jobs import (
    create_job,
    get_due_jobs,
    get_job,
    load_jobs,
    mark_job_run,
    save_jobs,
    _compute_grace_seconds,
    _ensure_aware,
    _plobi_now,
)


@pytest.fixture
def tmp_cron_dir(tmp_path, monkeypatch):
    """作业存储与取消标记都落进临时目录（同 tests/cron/test_jobs.py 的手法）。"""
    monkeypatch.setattr("cron.jobs.CRON_DIR", tmp_path / "cron")
    monkeypatch.setattr("cron.jobs.JOBS_FILE", tmp_path / "cron" / "jobs.json")
    monkeypatch.setattr("cron.jobs.OUTPUT_DIR", tmp_path / "cron" / "output")
    monkeypatch.setattr(cancellation, "_cancels_dir", lambda: tmp_path / "cron" / "cancels")
    return tmp_path


# ── 取消标记 ────────────────────────────────────────────────────────────


def test_a_cancel_only_applies_to_the_run_it_was_written_for(tmp_cron_dir):
    assert cancellation.request_cancel("job-a", "test")
    marker = cancellation.read_cancel("job-a")
    assert marker and marker["reason"] == "test"

    # 这一趟开始得更早 ⇒ 标记算数，读一次就收走
    assert cancellation.cancel_requested("job-a", since=marker["requested_at"] - 1)
    assert cancellation.pop_cancel("job-a", since=marker["requested_at"] - 1)
    assert not cancellation.cancel_requested("job-a")

    # 换一趟：请求写在它开始之前 ⇒ 不算数，也不被 pop 掉（留给它自己的那一趟）
    assert cancellation.request_cancel("job-b", "old")
    old = cancellation.read_cancel("job-b")
    assert not cancellation.cancel_requested("job-b", since=old["requested_at"] + 5)
    assert cancellation.pop_cancel("job-b", since=old["requested_at"] + 5) is None
    assert cancellation.read_cancel("job-b") is not None
    cancellation.clear("job-b")


def test_an_unreadable_marker_is_not_a_stop_request(tmp_cron_dir):
    path = cancellation._cancels_dir() / "job-c.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert cancellation.cancel_requested("job-c") is False


def test_a_job_id_with_path_characters_gets_no_marker(tmp_cron_dir):
    assert cancellation.request_cancel("../escape") is False
    assert cancellation._marker_path("../escape") is None


# ── 错过这一轮怎么办 ────────────────────────────────────────────────────


def test_missed_run_is_recorded_and_skipped_when_catch_up_is_off(tmp_cron_dir):
    job = create_job(prompt="nightly", schedule="every 1h", catch_up=False)
    jobs = load_jobs()
    jobs[0]["next_run_at"] = (datetime.now() - timedelta(minutes=35)).isoformat()
    save_jobs(jobs)

    assert get_due_jobs() == []

    updated = get_job(job["id"])
    assert updated["missed_runs"] == 1
    assert updated["last_missed_at"]
    next_dt = _ensure_aware(datetime.fromisoformat(updated["next_run_at"]))
    assert next_dt > _plobi_now()


def test_default_is_the_old_behaviour_and_still_runs_late(tmp_cron_dir):
    job = create_job(prompt="stale", schedule="every 1h")
    jobs = load_jobs()
    jobs[0]["next_run_at"] = (datetime.now() - timedelta(minutes=35)).isoformat()
    save_jobs(jobs)

    due = get_due_jobs()
    assert [j["id"] for j in due] == [job["id"]]
    assert get_job(job["id"]).get("missed_runs") in (None, 0)


def test_grace_minutes_overrides_the_window(tmp_cron_dir):
    # 默认 hourly 宽限 30 分钟；给这个任务 0 分钟宽限 ⇒ 晚 1 分钟就算错过
    strict = create_job(prompt="strict", schedule="every 1h", grace_minutes=0, catch_up=False)
    permissive = create_job(prompt="permissive", schedule="every 1h")
    jobs = load_jobs()
    for row in jobs:
        row["next_run_at"] = (datetime.now() - timedelta(minutes=1)).isoformat()
    save_jobs(jobs)

    due_ids = {j["id"] for j in get_due_jobs()}
    assert strict["id"] not in due_ids
    assert permissive["id"] in due_ids
    assert get_job(strict["id"])["missed_runs"] == 1


def test_grace_minutes_is_clamped_and_zero_is_honoured():
    assert _compute_grace_seconds({"kind": "interval", "minutes": 60}, {"grace_minutes": 0}) == 0
    assert _compute_grace_seconds({"kind": "interval", "minutes": 60}, {"grace_minutes": 10}) == 600
    assert _compute_grace_seconds({"kind": "interval", "minutes": 60}, {"grace_minutes": 99999}) == 1440 * 60
    # 没有覆盖值时还是老规矩（半小时，夹在 [120, 7200]）
    assert _compute_grace_seconds({"kind": "interval", "minutes": 60}, {}) == 1800


# ── 落账 ────────────────────────────────────────────────────────────────


def test_a_cancelled_run_is_booked_as_cancelled_not_error(tmp_cron_dir):
    job = create_job(prompt="stop me", schedule="every 1h")
    mark_job_run(job["id"], False, "cancelled on request", cancelled=True)
    row = get_job(job["id"])
    assert row["last_status"] == "cancelled"
    assert row["last_error"] == "cancelled on request"
    assert row.get("last_drift") is None
