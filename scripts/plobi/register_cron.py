"""注册 Plobi 调度任务到 Plobi cron（切片 A2/A5，幂等）。

用法（仓库根，开发 venv）::

    python scripts/plobi/register_cron.py            # A2：巡检+看门狗
    python scripts/plobi/register_cron.py --all      # A2+A5：含每日改动率日报

做三件事，全部可重复执行：
1. 写 ``PLOBI_HOME/plobi/runtime.json``（记录本仓库位置，供 cron 子进程引导）；
2. 把 ``scripts/plobi/cron_watchdog.py`` 复制到 ``PLOBI_HOME/scripts/``；
3. 按 ``cron.jobs.create_job`` 注册/更新任务（按 name 幂等，改了 schedule/script
   会原地 update，不产生重复任务）。

任务体 = ``no_agent`` 脚本（零 LLM 成本）；调度由底座 cron/scheduler.py 驱动，
随桌面后端常驻进程自启（AGENTS.md「Cron」节：3 分钟硬中断、tick 文件锁等
硬化不变量全部继承）。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

WATCHDOG_JOB_NAME = "plobi-agenda-watchdog"
WATCHDOG_SCRIPT = "plobi_watchdog.py"

# 与 config.example.yaml 的 plobi 段保持同源的字段；空名单 = fail-closed。
COLLECTOR_CONFIG = {
    "base_url": "http://127.0.0.1:5030",
    "talkers": [],
    "poll_minutes": 10,
    "enabled": True,
}


def _jobs():
    from cron import jobs as cron_jobs

    return cron_jobs


def _ensure_runtime_files(home: Path) -> None:
    plobi_home = home / "plobi"
    plobi_home.mkdir(parents=True, exist_ok=True)

    runtime = plobi_home / "runtime.json"
    runtime.write_text(
        json.dumps({"code_root": str(REPO_ROOT)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    config = plobi_home / "chatlog.json"
    if not config.exists():
        config.write_text(
            json.dumps(COLLECTOR_CONFIG, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[register] collector config created (whitelist empty -> fail-closed): {config}")


def _install_script(home: Path) -> Path:
    scripts_dir = home / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    target = scripts_dir / WATCHDOG_SCRIPT
    shutil.copyfile(REPO_ROOT / "scripts" / "plobi" / "cron_watchdog.py", target)
    return target


def _upsert_job(*, name: str, schedule: str, script: str) -> str:
    jobs = _jobs()
    existing = next((j for j in jobs.list_jobs(include_disabled=True) if j.get("name") == name), None)
    if existing is None:
        job = jobs.create_job(
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
        jobs.update_job(existing["id"], updates)
        print(f"[register] updated job {existing['id']} name={name}: {sorted(updates)}")
    else:
        print(f"[register] job up-to-date {existing['id']} name={name}")
    return existing["id"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="also register the A5 daily report job")
    args = parser.parse_args()

    from plobi_constants import get_plobi_home

    home = get_plobi_home()
    print(f"[register] PLOBI_HOME = {home}")

    _ensure_runtime_files(home)
    script = _install_script(home)
    print(f"[register] script installed: {script}")

    # A2：巡检 + 看门狗（10 分钟，≥30s ToS 红线余量充足）
    _upsert_job(name=WATCHDOG_JOB_NAME, schedule="10m", script=WATCHDOG_SCRIPT)

    if args.all:
        sys.path.insert(0, str(REPO_ROOT))
        from scripts.plobi.register_report import register_report_job

        register_report_job()

        from scripts.plobi.register_butler import register_butler_jobs

        register_butler_jobs()

    print("[register] done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
