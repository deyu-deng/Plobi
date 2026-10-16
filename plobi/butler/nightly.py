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

#: 每天那一轮信源采集（10-15 用户拍：「每天自动采，新条目进待确认」）。
#: 08:15 这个点是**读数点而不是承诺**：这台 07:30 有晨报、08:00 有额度提醒、20:00 建明日计划，
#: 采集排在计划之前才谈得上"秘书读的是当天的东西"；站方那侧只有 `min_minutes_between_pulls`
#: （默认 45 分钟带抖动）这一层礼貌约束，一天一轮远在它之上。
COLLECTION_JOB_NAME = "plobi-source-collect"

#: ``(job_name, 源脚本, 目标脚本名, schedule)`` —— schedule 用 5 段 cron。
BUTLER_JOBS: list[tuple[str, str, str, str]] = [
    ("plobi-morning-report", "cron_morning.py", "plobi_morning.py", "30 7 * * *"),
    ("plobi-message-digest", "cron_digest.py", "plobi_digest.py", "0 12 * * *"),
    ("plobi-quota-alert", "cron_quota.py", "plobi_quota.py", "0 8 * * *"),
    (COLLECTION_JOB_NAME, "cron_collect_sweep.py", "plobi_collect_sweep.py", "15 8 * * *"),
    (NIGHTLY_PLAN_JOB_NAME, "cron_evening_plan.py", "plobi_evening_plan.py", "0 20 * * *"),
    ("plobi-daily-checkin", "cron_daily_checkin.py", "plobi_daily_checkin.py", "30 21 * * *"),
]


#: 排程那条作业的形态（R-104 / 裁定 154）。用户原话：「现在很多日程都是代码生成的。而且定时任务
#: 不是每一次都新建一个会话……第二天的规划的定时任务需要给日程秘书，就是直接定时发提示词给日程秘书」
#: ——他指的是**作业形态**这一层：脚本内部从 10:2x 起已经在走模型（`cron_evening_plan.py` 传
#: `blocks_from_l1`），但作业本身仍是 `no_agent=True` 的脚本自跑 ⇒ 不经任何 Agent、没有 L2 归属、
#: 不进任何会话。
NIGHTLY_PLAN_PROMPT = (
    "把今晚生成的明日计划念给用户：先一句今天排到什么程度，再逐条说钟点与事，"
    "最后说一句为什么这么排。不要复述工具细节，不要自己重排时间——计划由脚本写好并校过边界，"
    "你的活是把这份计划说成人话，并把里面任何一条明显不合理的安排当场指出来。"
)


def job_shape(job_name: str) -> dict[str, object]:
    """这条管家作业该长什么样（R-104）。**只改排程那一条**：`plobi-source-collect`
    与其余四条一律保持脚本自跑（判据④：采集不需要推理，套一层 Agent 只多一个失败面）。"""
    if job_name == NIGHTLY_PLAN_JOB_NAME:
        return {"prompt": NIGHTLY_PLAN_PROMPT, "no_agent": False, "attach_to_session": True}
    return {"prompt": None, "no_agent": True}


def retire_profile_copies(job_name: str) -> list[str]:
    """判据①：同一条作业在这台**不止两处**（现核 10 个 profile 各有一份孤儿副本，
    `l2-agenda` 那份 ticker 心跳停在 10-13 21:50）。收成"只有一处在跑"。

    **禁用不删**：`profiles/` 不在任何 git 仓里，删了不可恢复。根因是 `cron/jobs.py:66`
    按 `get_plobi_home()`（profile 作用域）解析存储路径——那一半归 `WP-PROFILE-SHARED-LAYER`
    同族，不在本笔里顺手改。
    """
    from plobi_constants import get_default_plobi_root

    retired: list[str] = []
    profiles = get_default_plobi_root() / "profiles"
    if not profiles.is_dir():
        return retired
    for store in sorted(profiles.glob("*/cron/jobs.json")):
        try:
            data = json.loads(store.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rows = data.get("jobs") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            continue
        touched = False
        for row in rows:
            if isinstance(row, dict) and row.get("name") == job_name and row.get("enabled") is not False:
                row["enabled"] = False
                row["disabled_reason"] = (
                    "R-104：共享家目录那份才是唯一在跑的；这一份是 profile 作用域下的孤儿副本"
                    "（ticker 已停）。禁用不删，路径归属归 WP-PROFILE-SHARED-LAYER。"
                )
                touched = True
                retired.append(str(store.parent.parent.name))
        if touched:
            tmp = store.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, store)
    return retired


def align_job_shape(job_name: str, jobs_module: object) -> str:
    """把已存在的作业行刷成声明的形态——**行的存在与否归 marker 与用户，形态归代码**。

    与脚本副本同一手法：marker 10-15 就落过了，光靠"只种一次"这台永远拿不到 R-104 的新形状。
    被用户停掉的行**不动**（那是他的决定）。
    """
    shape = job_shape(job_name)
    row = next((j for j in jobs_module.list_jobs(include_disabled=True)
                if j.get("name") == job_name), None)
    if row is None:
        return "no-row"
    if row.get("enabled") is False:
        return "left-disabled"
    changed = {k: val for k, val in shape.items() if row.get(k) != val}
    if not changed:
        return "already-aligned"
    jobs_module.update_job(row.get("id") or job_name, changed)
    return "aligned:" + ",".join(sorted(changed))


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


def _ensure_seeded_job(job_name: str, jobs_module: Any, now: Callable[[], str] | None) -> str:
    """按名字种一条管家作业：**脚本副本每次都刷，作业行只种一次**。

    ticker 跑的是 ``PLOBI_HOME/scripts/`` 里那份复制件，不是仓里那份；若跟着 marker 一起只复制一次，
    那么改了源脚本之后到的仍是旧副本——"代码合了 ≠ 会在跑"那一族（裁定 94 同族）。
    行的事归用户决定，文件的事归代码。marker 只在真成功之后落，半途失败下次重试。
    """
    spec = next(row for row in BUTLER_JOBS if row[0] == job_name)
    _name, src_name, dst_name, schedule = spec
    copy_butler_scripts(only=[src_name])
    retire_profile_copies(job_name)

    marker = _read_marker()
    if marker.get(job_name):
        # 返回词不变（别的测试钉的就是这几个字），但形态每次都对齐——不这么做，
        # 这台会永远停在 `no_agent=True` 那一版。
        align_job_shape(job_name, jobs_module)
        return "already-seeded"

    existing = next(
        (j for j in jobs_module.list_jobs(include_disabled=True) if j.get("name") == job_name),
        None,
    )
    if existing is not None:
        # 已经有人在（手动 register_cron.py、或用户自己建的）——不抢他的决定，
        # 但 marker 要落下，免得下次启动又来问一遍。
        _mark(marker, job_name, now)
        return "exists"

    shape = job_shape(job_name)
    jobs_module.create_job(
        prompt=shape["prompt"],
        schedule=schedule,
        name=_name,
        script=dst_name,
        no_agent=bool(shape["no_agent"]),
        attach_to_session=shape.get("attach_to_session"),
        deliver="local",
    )
    _mark(marker, job_name, now)
    return "seeded"


def ensure_nightly_plan_job(
    jobs_module: Any | None = None,
    *,
    now: Callable[[], str] | None = None,
) -> str:
    """装机后第一次启动把「每晚 20:00 建明日计划」那条作业种下去。

    返回一个说得出发生了什么的原因串（``seeded`` / ``exists`` / ``already-seeded``），
    供调用方进日志；任何异常照实往外抛，由启动那一格接住并 warn（不许静默），
    但**不许**在失败时落下 marker——marker 落下就等于这台再也不试了。
    """
    if jobs_module is None:
        from cron import jobs as jobs_module  # type: ignore[no-redef]

    return _ensure_seeded_job(NIGHTLY_PLAN_JOB_NAME, jobs_module, now)


def ensure_collection_job(
    jobs_module: Any | None = None,
    *,
    now: Callable[[], str] | None = None,
) -> str:
    """种「每天 08:15 一轮信源采集」那一条。语义与 :func:`ensure_nightly_plan_job` 逐字相同。

    为什么要有这一条：10-15 现核到 ``collecting_sources()`` 全仓零调用方 ⇒ 采集器合了两年、
    库里最后一次进新条目停在 10-10。执行方补在 ``plobi/collectors/sweep.py``，
    这一格补的是"谁每天去敲它一次"。

    ⚠️ **启动那一步还接不上**：唯一的种作业调用点在 ``plobi_cli/web_server.py`` 的桌面启动里，
    那一档 Network 在派（`WP-COLLECT-WIN` 的硬边界写着不许为起采集去动它）。
    在他们接上之前，这台可以手动跑 ``register_cron.py --all`` 或直接调本函数。
    """
    if jobs_module is None:
        from cron import jobs as jobs_module  # type: ignore[no-redef]

    return _ensure_seeded_job(COLLECTION_JOB_NAME, jobs_module, now)


def _mark(marker: dict[str, Any], key: str, now: Callable[[], str] | None) -> None:
    marker[key] = (now or datetime.now)().isoformat(timespec="seconds")
    _write_marker(marker)
