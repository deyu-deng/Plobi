"""A5 日报任务注册（由 register_cron.py --all 调用；幂等）。"""

from __future__ import annotations

import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

REPORT_JOB_NAME = "plobi-change-rate-report"
REPORT_SCRIPT = "plobi_report.py"


def register_report_job() -> str:
    from plobi_constants import get_plobi_home

    home = get_plobi_home()
    scripts_dir = home / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    target = scripts_dir / REPORT_SCRIPT
    shutil.copyfile(REPO_ROOT / "scripts" / "plobi" / "cron_report.py", target)

    from cron import jobs as cron_jobs

    existing = next(
        (j for j in cron_jobs.list_jobs(include_disabled=True) if j.get("name") == REPORT_JOB_NAME),
        None,
    )
    schedule = "0 21 * * *"  # 每天 21:00（M2 规划层 20:00 生成计划之后）
    if existing is None:
        job = cron_jobs.create_job(
            prompt=None,
            schedule=schedule,
            name=REPORT_JOB_NAME,
            script=REPORT_SCRIPT,
            no_agent=True,
            deliver="local",
        )
        print(f"[register] created job {job['id']} name={REPORT_JOB_NAME} schedule={schedule}")
        return job["id"]

    updates: dict = {}
    if existing.get("script") != REPORT_SCRIPT:
        updates["script"] = REPORT_SCRIPT
    if existing.get("schedule", {}).get("display") != schedule:
        updates["schedule"] = schedule
    if not existing.get("enabled"):
        updates["enabled"] = True
    if updates:
        cron_jobs.update_job(existing["id"], updates)
        print(f"[register] updated job {existing['id']} name={REPORT_JOB_NAME}: {sorted(updates)}")
    else:
        print(f"[register] job up-to-date {existing['id']} name={REPORT_JOB_NAME}")
    return existing["id"]
