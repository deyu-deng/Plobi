"""Night autonomy policy + morning report hooks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional
from zoneinfo import ZoneInfo

from .queue import TaskQueue, TaskStatus
from .risk import RiskLevel, can_run_at_night


@dataclass
class NightPolicy:
    tz: str = "Asia/Shanghai"
    start_hour: int = 0
    end_hour: int = 7
    allow_l1: bool = True

    def is_night(self, now: Optional[datetime] = None) -> bool:
        try:
            tz = ZoneInfo(self.tz)
        except Exception:
            tz = ZoneInfo("UTC")
        now = now or datetime.now(tz)
        if now.tzinfo is None:
            now = now.replace(tzinfo=tz)
        else:
            now = now.astimezone(tz)
        h = now.hour
        if self.start_hour <= self.end_hour:
            return self.start_hour <= h < self.end_hour
        # wraps midnight
        return h >= self.start_hour or h < self.end_hour


def morning_report(queue: Optional[TaskQueue] = None, *, policy: Optional[NightPolicy] = None) -> dict:
    queue = queue or TaskQueue()
    policy = policy or NightPolicy()
    tasks = queue.list_tasks()
    awaiting = [
        t
        for t in tasks
        if t.status == TaskStatus.BLOCKED_AWAITING_HUMAN.value or t.awaiting_human
    ]
    completed = [t for t in tasks if t.status == TaskStatus.COMPLETED.value]
    failed = [t for t in tasks if t.status == TaskStatus.FAILED.value]
    night_blocked = [
        t
        for t in awaiting
        if not can_run_at_night(RiskLevel.parse(t.risk), allow_l1=policy.allow_l1)
    ]
    lines: List[str] = [
        "# Plobi 早报",
        f"- night_window: {policy.start_hour:02d}:00–{policy.end_hour:02d}:00 ({policy.tz})",
        f"- completed: {len(completed)}",
        f"- failed: {len(failed)}",
        f"- awaiting_you: {len(awaiting)}",
        f"- high_risk_held: {len(night_blocked)}",
        "",
        "## 需要你批准",
    ]
    if not awaiting:
        lines.append("- （无）")
    else:
        for t in awaiting[:30]:
            lines.append(f"- [{t.risk}] {t.id}: {t.goal} (stage={t.stage})")
    lines.append("")
    lines.append("## 昨夜完成（摘要）")
    if not completed:
        lines.append("- （无新完成）")
    else:
        for t in completed[-20:]:
            lines.append(f"- {t.id}: {t.result_summary or t.summary or t.goal}")
    return {
        "markdown": "\n".join(lines),
        "awaiting_human": [{"id": t.id, "goal": t.goal, "risk": t.risk} for t in awaiting],
        "completed_count": len(completed),
        "failed_count": len(failed),
    }


def tick_night_queue(queue: Optional[TaskQueue] = None, *, policy: Optional[NightPolicy] = None) -> dict:
    """Claim one night-runnable task or block high-risk ones. Does not execute work."""
    queue = queue or TaskQueue()
    policy = policy or NightPolicy()
    night = policy.is_night()
    claimed = queue.claim_runnable(night=night, allow_l1_night=policy.allow_l1)
    return {
        "is_night": night,
        "claimed": None
        if claimed is None
        else {"id": claimed.id, "goal": claimed.goal, "risk": claimed.risk},
    }
