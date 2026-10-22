"""每条信源自己的采集频率 —— 「这一轮该不该打这一家」的判定。**不是第二个调度器**。

存在的理由（裁定 191 二）：全局只有 ``plobi-source-collect`` 一条 cron（每天 08:15 把所有源
一起跑），所以「学在浙大我设成每天某个时刻采一次」这句话在代码里**没有落点**——设置面上
没有那一格，公共层里也没有读它的人。这一格补的是**声明 + 判定**：cron 还是那一条，
它跑到每一家之前先问这里一句「到点了没」。

三件容易混成一件事的东西，这里只占第一件：

- **频率（cadence）**：用户想要多久打一次。落在这一层，可读可写，值落在 ``sources.yaml``
  那一条信源自己的格里（与 ``enabled`` 同一层、同一个回落链）。
- **节流（``min_minutes_between_pulls``）**：两次真打学校之间的最短间隔。仍在各家采集器自己那侧，
  它防的是「把站方打爆」，不是「我想几点采」。**这一层不删它、也不替代它**：两把闸叠加生效，
  到了点而节流没到，采集器自己会报 ``throttled`` —— 那是两条不同的话，不许合成一句。
- **调度（cron）**：进程什么时候醒。不改，仍是一条。

时间那半的算术**复用** ``cron/jobs.py`` 那两只现成的手（``parse_schedule`` / ``compute_next_run``），
不在这里再写一份 cron 解析：同一件事在仓里长两套，早晚不一致（裁定 94）。

**读不到时不猜**：写坏的那一句频率判成「不认识 ⇒ 这一轮不打」，而不是「静默回落成每轮都打」——
后者会让一个拼错的 ``0 3 * *`` 变成每天 08:15 稳定打一遍学校，而面上什么都看不出来。
反过来，**上一轮什么时候打的读不到**（台账缺、共享根解析不到）时按「没打过」处理 ⇒ 打：
那是本刀之前的现行行为，不是一个新风险，台账坏了不该把采集整体停掉。

台账（每家最后一次真被这一轮打过）单独一份文件，不写进 ``sources.yaml``：那一份是**用户按过的东西**，
跟着用户走；这一份是**机器跑出来的读数**。混在一起，下一次读配置的人就分不清哪一格是人写的。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

__all__ = ["LEDGER_FILENAME", "ORIGIN_CONFIG", "ORIGIN_LEDGER_UNREADABLE", "ORIGIN_PER_ROUND",
           "ORIGIN_SPEC", "cadence_env", "ledger_path", "mark_run", "partition", "reading",
           "readings", "resolve_cadence"]

#: 空串是一个**读数**而不是省略：「这一条没设频率 ⇒ 跟着全局那一轮，每轮都打」。
#: 这是本刀之前的现行行为，所以四条现存信源不声明也照旧跑，不产生一次静默的行为变化。
ORIGIN_PER_ROUND = "per_round"
#: 值从哪来（与 ``sources_config.effective_enabled`` 的 ``enabled_origin`` 同一口径）：
#: 「我明明设成每天三点怎么还在每轮打」这句话只能由这一格回答。
ORIGIN_CONFIG = "config"
ORIGIN_SPEC = "spec"
ORIGIN_LEDGER_UNREADABLE = "ledger_unreadable"

LEDGER_FILENAME = "collect_runs.json"

ENV_LEDGER = "PLOBI_COLLECT_LEDGER"

_INVALID_PREFIX = "这一句频率不认识："
_NEVER_PREFIX = "这一句频率不会再触发："


def ledger_path() -> Path:
    """台账落点：共享层里与 ``sources.yaml`` 同目录的那一份。

    目录解析**借用** ``sources_config.sources_path()``，不在这里第二次解析共享根：
    第二次解析就是第二次可能不一致（那正是 ``agenda_db_path`` 记下的分裂形状）。
    """
    override = (os.environ.get(ENV_LEDGER) or "").strip()
    if override:
        return Path(override)
    from plobi.collectors import sources_config

    return sources_config.sources_path().parent / LEDGER_FILENAME


def resolve_cadence(source_id: str) -> tuple[str, str]:
    """这条信源**现在生效**的那一句频率，与它是从哪来的 ``(值, 归属)``。

    回落链照 ``effective_enabled``：用户那层写了就以它为准 → 没写就用包里的声明 →
    两边都没声明就是 ``""``（每轮都打）。
    """
    from plobi.collectors import registry, sources_config

    written = (sources_config.entry(source_id) or {}).get("cadence")
    if isinstance(written, str) and written.strip():
        return written.strip(), ORIGIN_CONFIG
    declared = getattr(registry.spec(source_id), "cadence", "") or ""
    if declared.strip():
        return declared.strip(), ORIGIN_SPEC
    return "", ORIGIN_PER_ROUND


def _now_iso(now: Optional[datetime]) -> str:
    from cron import jobs as cron_jobs

    reference = now if now is not None else cron_jobs._plobi_now()
    return reference.isoformat(timespec="seconds")


def _compare(left: str, right: str) -> int:
    """把两句 ISO 拉进同一个时区再比 —— 这台会漂时钟，字符串序不算数。"""
    from cron import jobs as cron_jobs

    a = cron_jobs._ensure_aware(datetime.fromisoformat(left))
    b = cron_jobs._ensure_aware(datetime.fromisoformat(right))
    return -1 if a < b else (0 if a == b else 1)


def _read_ledger() -> Optional[dict[str, Any]]:
    """台账那份表。

    ``None`` = **读不到**（共享根解析不到，或那一份在却打不开）；``{}`` = 真的还没有记录。
    两者不能合成一个：前者是「这台记不住事」，后者是「还没打过」。
    """
    try:
        path = ledger_path()
    except Exception as exc:                              # noqa: BLE001 - 共享根读不到 = 记不住
        logger.debug("cadence: 台账目录解析不到：%s", exc)
        return None
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.debug("cadence: 台账在但读不动（%s）：%s", path, exc)
        return None
    return loaded if isinstance(loaded, dict) else None


def _last_run_at(ledger: Optional[dict[str, Any]], source_id: str) -> Optional[str]:
    if not isinstance(ledger, dict):
        return None
    row = ledger.get(source_id)
    if not isinstance(row, dict):
        return None
    value = str(row.get("last_run_at") or "").strip()
    return value or None


def reading(source_id: str, *, now: Optional[datetime] = None,
            ledger: Optional[dict[str, Any]] = ...) -> dict[str, Any]:  # type: ignore[assignment]
    """这一条到没到点的**完整读数**——不打的时候也要说得出为什么没打。

    ``ledger=...``（默认）自己去问台账；测试可以钉一份进来，跳过那次文件读。
    键位是稳定的：``cadence`` / ``cadence_origin`` / ``last_run_at`` / ``next_run_at`` /
    ``due`` / ``state`` / ``note``。``state`` 是那一轮里给人看的那一格话。
    """
    from cron import jobs as cron_jobs

    reference = _now_iso(now)
    if ledger is ...:
        ledger = _read_ledger()

    cadence, origin = resolve_cadence(source_id)
    row: dict[str, Any] = {"cadence": cadence, "cadence_origin": origin,
                           "last_run_at": _last_run_at(ledger, source_id), "next_run_at": "",
                           # 上一轮那句话（出错时屏上要说清是谁的锅）。没有就是空串，不编。
                           "last_detail": str(((ledger or {}).get(source_id) or {}).get("last_detail") or "")
                           if isinstance(ledger, dict) else ""}

    if ledger is None and origin != ORIGIN_PER_ROUND:
        row.update(due=True, state="ledger_unreadable",
                   note="台账读不到：按「没打过」处理，这一轮照打（不是把采集停掉的理由）")
        return row

    if not cadence:
        row.update(due=True, state="per_round",
                   note="没设频率：跟着全局那一轮，每轮都打")
        return row

    try:
        parsed = cron_jobs.parse_schedule(cadence)
    except (ValueError, RuntimeError) as exc:
        row.update(due=False, state="invalid_cadence", note=f"{_INVALID_PREFIX}{exc}")
        return row

    last = row["last_run_at"]
    if not last:
        row.update(due=True, state="first_run",
                   note="设了频率，但这台还没有「上一轮什么时候打的」记录 ⇒ 这一轮打一次，之后按频率")
        return row

    nxt = cron_jobs.compute_next_run(parsed, last)
    if nxt is None:
        row.update(due=False, state="exhausted",
                   note=f"{_NEVER_PREFIX}{cadence}（上一轮 {last} 之后没有下一次了）")
        return row

    iso = str(nxt)
    row["next_run_at"] = iso
    due = _compare(iso, reference) <= 0
    row.update(due=due, state="due" if due else "not_due",
               note="" if due else f"下一轮该打在 {iso}")
    return row


def readings(source_ids: Iterable[str], *, now: Optional[datetime] = None) -> list[dict[str, Any]]:
    ledger = _read_ledger()
    return [{"source": sid, **reading(sid, now=now, ledger=ledger)} for sid in sorted(source_ids)]


def partition(source_ids: Iterable[str], *, now: Optional[datetime] = None
              ) -> tuple[tuple[str, ...], list[dict[str, Any]]]:
    """分成「这一轮该打的」与「这一轮没到点的读数」。

    返回的第二个值是**逐条的行**，形状与 ``sweep`` 那些行同一族（``source`` / ``ok`` / ``state`` /
    ``detail``）——这样 ``plobi-source-collect`` 那一轮的正文里，"为什么今天没打它"是一行读数，
    不是要人去翻配置猜。没到点**不是失败**：``ok=True``。
    """
    due: list[str] = []
    skipped: list[dict[str, Any]] = []
    for sid in sorted(source_ids):
        item = reading(sid, now=now)
        if item["due"]:
            due.append(sid)
            continue
        skipped.append({"source": sid, "ok": True, "state": item["state"],
                        "detail": item["note"] or f"频率 {item['cadence']!r} 还没到点",
                        "cadence": item["cadence"], "cadence_origin": item["cadence_origin"],
                        "next_run_at": item["next_run_at"]})
    return tuple(due), skipped


def last_run(source_id: str) -> dict[str, str]:
    """上一轮**碰过它没有、结果是什么、那句话怎么说**。设置页那一屏的"出错原因"读这一格。

    单独一只口是为了让"读台账"这件事只有一个地方做：`reading()` 判的是到没到点，
    把上一轮的结果塞进去会让那一只函数同时回答两个问题（而这两个问题的失效方式不同——
    台账读不到时"到点"该回 `True` 照打，"上一轮结果"该回空而不是回"没打过"）。
    """
    ledger = _read_ledger() or {}
    row = ledger.get(source_id) or {}

    return {"state": str(row.get("last_state") or ""),
            "detail": str(row.get("last_detail") or ""),
            "at": str(row.get("last_run_at") or "")}


def mark_run(source_id: str, *, state: str = "", detail: str = "",
             now: Optional[datetime] = None) -> Optional[Path]:
    """记下「这一轮真的打过它了」——**打过**，不管打成分工还是失败。

    为什么失败也记：频率管的是"多久碰它一次"，不判"碰成功没"。失败还按频率等下一次，
    才有"学校挂了不会变成每 15 分钟重试一次"这一条；碰没碰成在每一轮自己的读数里。
    台账写不进（共享根坏了）返回 ``None`` —— 出声由调用方的那一行读数负责，这里不抛。

    ``detail`` 是 10-22 加的一格：设置页那一屏要把"出错"说成一句人话（「凭据不对」/
    「要人工验证码」/「页面改了」），而那句话只有跑的那一轮知道。它**逐字存**、不加工，
    与 `sweep` 那一轮回给日志的是同一句。
    """
    reference = _now_iso(now)
    ledger = _read_ledger()
    if ledger is None:
        ledger = {}
    ledger[source_id] = {"last_run_at": reference, "last_state": state}
    if detail:
        ledger[source_id]["last_detail"] = detail[:400]
    try:
        path = ledger_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=str(path.parent), prefix=f".{LEDGER_FILENAME}.",
            suffix=".tmp", delete=False,
        )
    except (OSError, Exception) as exc:                   # noqa: BLE001 - 共享根读不到就是记不住，别抛给那一轮
        logger.debug("cadence: 台账写不了：%s", exc)
        return None
    try:
        with handle:
            json.dump(ledger, handle, ensure_ascii=False, sort_keys=True, indent=2)
        os.replace(handle.name, path)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise
    return path


def cadence_env(source_id: str) -> dict[str, Any]:
    """给设置面与 ``/api/collect/sources`` 的那几格读数（含"下一次该打在几点"）。

    单独一个函数是因为**两处要读同一份**（面与那一轮的正文）；各处自己拼一次就是两份口径。
    """
    item = reading(source_id)
    return {"cadence": item["cadence"], "cadence_origin": item["cadence_origin"],
            "next_run_at": item["next_run_at"], "due": item["due"],
            #: **不叫 `state`**：那一格已经被活性探针占了（live / stale / never / no_store），
            #: 同名覆盖一次，面上那句"这条信源活着吗"就变成"到点没"——两个不同的问题共用一个键。
            "cadence_state": item["state"], "cadence_note": item["note"]}
