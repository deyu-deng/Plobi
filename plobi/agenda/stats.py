"""改动率仪表（切片 A5）。

MVP 成功标准 §3.1（docs/specs/MVP-AI-Secretary-Requirements.md）::

    系统维护的日程条目中，用户手动删除或修改的比例 ≤ 20%（连续 7 天统计）
    采集方式：用户对「待确认」条目的确认/忽略动作即数据（ADR-0009 副产品）

口径（可回溯到 action_log 台账，无自报数据）::

    分母 = 窗口内对**非 manual 来源**条目的全部落定动作
           （confirm / dismiss / manual_edit / manual_delete）
    分子 = dismiss + manual_edit + manual_delete（用户否决或手动改系统给的条目）
    改动率 = 分子 / 分母；分母为 0 时无数据（不评定）

manual 来源条目的编辑删除不计入——用户改自己录的事实不构成对系统的否决。

无模型调用；只读 events 之外新增的 ``action_log`` 台账（append-only）。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from .service import AgendaService, get_service
from .store import connect, list_actions

TARGET_RATE = 0.20


def change_report(
    service: Optional[AgendaService] = None,
    *,
    days: int = 7,
    now: Optional[datetime] = None,
) -> dict:
    """Compute the change-rate metric over the trailing ``days`` window."""
    service = service or get_service()
    reference = now or datetime.now()
    window_start = (reference - timedelta(days=days)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    conn = connect(service.db_path)
    try:
        actions = list_actions(
            conn, since=window_start.isoformat(), until=reference.isoformat()
        )
    finally:
        conn.close()

    counts = {"confirm": 0, "dismiss": 0, "manual_edit": 0, "manual_delete": 0}
    for row in actions:
        if row["event_source"] == "manual":
            continue
        counts[row["action"]] = counts.get(row["action"], 0) + 1

    overrides = counts["dismiss"] + counts["manual_edit"] + counts["manual_delete"]
    total = overrides + counts["confirm"]
    rate: Optional[float] = round(overrides / total, 4) if total else None

    return {
        "days": days,
        "window_start": window_start.isoformat(),
        "accepted": counts["confirm"],
        "dismissed": counts["dismiss"],
        "manual_edits": counts["manual_edit"],
        "manual_deletes": counts["manual_delete"],
        "overrides": overrides,
        "total": total,
        "rate": rate,
        "target": TARGET_RATE,
        "meets_target": rate is None or rate <= TARGET_RATE,
    }


def format_report(report: dict) -> str:
    """DingTalk text body for the daily report."""
    head = f"[Plobi] 改动率日报（近 {report['days']} 天）"
    if report["total"] == 0:
        return f"{head}\n窗口内暂无落定动作，尚无数据。\n目标：改动率 ≤ {report['target']:.0%}"

    rate_pct = f"{report['rate']:.0%}"
    verdict = "✅ 达标" if report["meets_target"] else "⚠️ 超标"
    lines = [
        head,
        f"改动率: {rate_pct}（目标 ≤ {report['target']:.0%}）{verdict}",
        f"确认 {report['accepted']} / 忽略 {report['dismissed']}"
        f" / 手动改 {report['manual_edits']} / 手动删 {report['manual_deletes']}",
        f"共 {report['total']} 条系统条目落定，其中 {report['overrides']} 条被否决或手动修改",
    ]
    return "\n".join(lines)
