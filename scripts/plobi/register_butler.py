"""B5 管家包任务注册（由 register_cron.py --all 调用；幂等）。

把三个 cron 脚本复制到 ``HERMES_HOME/scripts/`` 并按 name 幂等注册/更新任务。
"""

from __future__ import annotations

import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# (job_name, 源脚本, 目标脚本名, schedule)
# schedule 用 5 段 cron（与 register_report.py 一致）。
JOBS = [
    ("plobi-morning-report", "cron_morning.py", "plobi_morning.py", "30 7 * * *"),
    ("plobi-message-digest", "cron_digest.py", "plobi_digest.py", "0 12 * * *"),
    ("plobi-quota-alert", "cron_quota.py", "plobi_quota.py", "0 8 * * *"),
    ("plobi-evening-plan", "cron_evening_plan.py", "plobi_evening_plan.py", "0 20 * * *"),
    ("plobi-daily-checkin", "cron_daily_checkin.py", "plobi_daily_checkin.py", "30 21 * * *"),
]


def _upsert(cron_jobs, *, name: str, schedule: str, script: str) -> str:
    existing = next(
        (j for j in cron_jobs.list_jobs(include_disabled=True) if j.get("name") == name),
        None,
    )
    if existing is None:
        job = cron_jobs.create_job(
            prompt=None,
            schedule=schedule,
            name=name,
            script=script,
            no_agent=True,
            deliver="local",
        )
        print(f"[register] created job {job['id']} name={name} schedule={schedule}")
        return job["id"]

    updates: dict = {}
    if existing.get("script") != script:
        updates["script"] = script
    if existing.get("schedule", {}).get("display") != schedule:
        updates["schedule"] = schedule
    if not existing.get("enabled"):
        updates["enabled"] = True
    if updates:
        cron_jobs.update_job(existing["id"], updates)
        print(f"[register] updated job {existing['id']} name={name}: {sorted(updates)}")
    else:
        print(f"[register] job up-to-date {existing['id']} name={name}")
    return existing["id"]


def register_butler_jobs() -> list[str]:
    from hermes_constants import get_hermes_home

    home = get_hermes_home()
    scripts_dir = home / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)

    from cron import jobs as cron_jobs

    ids: list[str] = []
    for name, src_name, dst_name, schedule in JOBS:
        src = REPO_ROOT / "scripts" / "plobi" / src_name
        dst = scripts_dir / dst_name
        shutil.copyfile(src, dst)
        ids.append(_upsert(cron_jobs, name=name, schedule=schedule, script=dst_name))
    return ids
