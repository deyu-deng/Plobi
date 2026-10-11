"""管家包（B5）的定时任务真源：作业表 + 注册 + 装机时的「只种一次」。

底座早就在，缺的只是**没有人调用注册**：``cron_evening_plan.py`` 每晚 20:00 读
``events`` 写 ``daily_plans``/``plan_items``（ADR-0012 §「20:00 规划 = 日程域
``no_agent`` cron」），``register_cron.py --all`` 能把作业登记进 ``cron/jobs.json``——
但这两件都只在有人手动敲命令时才发生。本模块把作业声明收成**一处**（原来住在
``scripts/plobi/register_butler.py``，那里现在只剩薄壳），并补上产品自己会走的那一步
:func:`ensure_nightly_plan_job`。

为什么种在桌面后端而不是安装脚本：实测这台跑的是 ``plobi_cli.main serve``
（``PLOBI_DESKTOP=1`` 那条分支，见 ``plobi_cli/web_server.py::_start_desktop_cron_ticker``），
cron ticker 就在它的进程里 60 秒一跳；``install.ps1`` 的 stage 在这台从来没走过（开发态装
机），写在安装脚本里的注册等于永远不会发生。``cron/jobs.json`` 是共享文件，所以哪只进程
先起来种都算数——网关那只也一样看得到。

种一次之后不再管：**用户删掉或关掉就是他的决定**，下次启动不许复活（同「作息模板」那条
纪律——用户改过的行一行不动）。 marker 只在真成功之后落盘，半途失败下次重试。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

REPO_ROOT = Path(__file__).resolve().parents[2]

NIGHTLY_PLAN_JOB_NAME = "plobi-evening-plan"

#: ``(job_name, 源脚本, 目标脚本名, schedule)`` —— schedule 用 5 段 cron。
BUTLER_JOBS: list[tuple[str, str, str, str]] = [
    ("plobi-morning-report", "cron_morning.py", "plobi_morning.py", "30 7 * * *"),
    ("plobi-message-digest", "cron_digest.py", "plobi_digest.py", "0 12 * * *"),
    ("plobi-quota-alert", "cron_quota.py", "plobi_quota.py", "0 8 * * *"),
    (NIGHTLY_PLAN_JOB_NAME, "cron_evening_plan.py", "plobi_evening_plan.py", "0 20 * * *"),
    ("plobi-daily-checkin", "cron_daily_checkin.py", "plobi_daily_checkin.py", "30 21 * * *"),
]


def _seed_marker_path() -> Path:
    from plobi_constants import get_plobi_home

    return get_plobi_home() / "plobi" / "butler_seed.json"


def _read_marker() -> dict[str, Any]:
    path = _seed_marker_path()
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _write_marker(marker: dict[str, Any]) -> None:
    path = _seed_marker_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp", prefix=".butler_seed_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(marker, handle, ensure_ascii=False, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def copy_butler_scripts(only: Iterable[str] | None = None) -> Path:
    """把源脚本复制进 ``PLOBI_HOME/scripts/`` —— ticker 只肯跑这个目录里的脚本。

    ``cron/scheduler.py::_run_job_script`` 会拒绝解析到 scripts 目录之外的路径，
    所以「注册一条作业」永远等于「先把文件放进去」。
    """
    from plobi_constants import get_plobi_home

    scripts_dir = get_plobi_home() / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    wanted = set(only) if only is not None else None
    for _name, src_name, dst_name, _schedule in BUTLER_JOBS:
        if wanted is not None and src_name not in wanted:
            continue
        shutil.copyfile(REPO_ROOT / "scripts" / "plobi" / src_name, scripts_dir / dst_name)
    return scripts_dir


def _upsert(cron_jobs: Any, *, name: str, schedule: str, script: str) -> str:
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


def register_butler_jobs(jobs_module: Any | None = None) -> list[str]:
    """手动注册全管家包（``register_cron.py --all`` 走这里；幂等，会把关掉的重新打开）。

    ``jobs_module`` 只为测试注入留的口——缺省仍取 ``cron.jobs``。
    """
    if jobs_module is None:
        from cron import jobs as jobs_module  # type: ignore[no-redef]

    copy_butler_scripts()
    return [
        _upsert(jobs_module, name=name, schedule=schedule, script=dst_name)
        for name, _src, dst_name, schedule in BUTLER_JOBS
    ]


def ensure_nightly_plan_job(
    jobs_module: Any | None = None,
    *,
    now: Callable[[], str] | None = None,
) -> str:
    """装机后第一次启动把「每晚 20:00 建明日计划」那条作业种下去。

    返回一个说得出发生了什么的原因串（``seeded`` / ``exists`` / ``already-seeded``），
    供调用方进日志；任何异常照实往外抛，由启动那一格接住并 warn（不许静默），
    但**不许**在失败时落下 marker——marker 落下就等于这台再也不试了。

    **脚本副本每次都刷，作业行只种一次**：ticker 跑的是 ``PLOBI_HOME/scripts/`` 里那份
    复制件，不是仓里那份。若跟着 marker 一起只复制一次，那么改了 ``cron_evening_plan.py``
    之后到的仍是旧副本——"代码合了 ≠ 会在跑"那一族（裁定 94 同族）。行的事归用户决定，
    文件的事归代码。
    """
    if jobs_module is None:
        from cron import jobs as jobs_module  # type: ignore[no-redef]

    spec = next(row for row in BUTLER_JOBS if row[0] == NIGHTLY_PLAN_JOB_NAME)
    _name, src_name, dst_name, schedule = spec
    copy_butler_scripts(only=[src_name])

    marker = _read_marker()
    if marker.get(NIGHTLY_PLAN_JOB_NAME):
        return "already-seeded"

    existing = next(
        (j for j in jobs_module.list_jobs(include_disabled=True) if j.get("name") == NIGHTLY_PLAN_JOB_NAME),
        None,
    )
    if existing is not None:
        # 已经有人在（手动 register_cron.py、或用户自己建的）——不抢他的决定，
        # 但 marker 要落下，免得下次启动又来问一遍。
        _mark(marker, NIGHTLY_PLAN_JOB_NAME, now)
        return "exists"

    jobs_module.create_job(
        prompt=None,
        schedule=schedule,
        name=_name,
        script=dst_name,
        no_agent=True,
        deliver="local",
    )
    _mark(marker, NIGHTLY_PLAN_JOB_NAME, now)
    return "seeded"


def _mark(marker: dict[str, Any], key: str, now: Callable[[], str] | None) -> None:
    marker[key] = (now or datetime.now)().isoformat(timespec="seconds")
    _write_marker(marker)
