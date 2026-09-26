"""Persistent task queue with risk + stage metadata."""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from .risk import RiskLevel, can_run_at_night, requires_human
from .stages import Stage, StageMachine


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    BLOCKED_AWAITING_HUMAN = "blocked_awaiting_human"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TaskRecord:
    id: str
    goal: str
    risk: str = RiskLevel.L1_LOCAL_MUTATE.value
    status: str = TaskStatus.QUEUED.value
    domain: str = "generic"
    stage: str = Stage.INTAKE.value
    awaiting_human: bool = False
    summary: str = ""
    result_summary: str = ""
    route: str = ""
    surface: str = ""
    created_at: str = field(default_factory=_utc_now)
    updated_at: str = field(default_factory=_utc_now)
    meta: Dict[str, Any] = field(default_factory=dict)

    def stage_machine(self) -> StageMachine:
        return StageMachine.from_dict(
            {
                "domain": self.domain,
                "stage": self.stage,
                "awaiting_human": self.awaiting_human,
                "gated": self.meta.get("gated"),
            }
        )

    def apply_stage(self, sm: StageMachine) -> None:
        self.domain = sm.domain
        self.stage = sm.stage.value
        self.awaiting_human = sm.awaiting_human
        if sm.awaiting_human:
            self.status = TaskStatus.BLOCKED_AWAITING_HUMAN.value
        self.updated_at = _utc_now()


class TaskQueue:
    """JSON-file backed queue under PLOBI_HOME/plobi/tasks.json."""

    def __init__(self, path: Optional[Path] = None):
        if path is None:
            try:
                from plobi_constants import get_plobi_home

                root = get_plobi_home() / "plobi"
            except Exception:
                root = Path.home() / ".plobi" / "plobi"
            path = root / "tasks.json"
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        if not self.path.exists():
            self._write({"tasks": []})

    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"tasks": []}

    def _write(self, data: dict) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def list_tasks(self, status: Optional[str] = None) -> List[TaskRecord]:
        with self._lock:
            tasks = [TaskRecord(**t) for t in self._read().get("tasks", [])]
        if status:
            tasks = [t for t in tasks if t.status == status]
        return tasks

    def get(self, task_id: str) -> Optional[TaskRecord]:
        for t in self.list_tasks():
            if t.id == task_id:
                return t
        return None

    def enqueue(
        self,
        goal: str,
        *,
        risk: str = "L1",
        domain: str = "generic",
        summary: str = "",
        meta: Optional[dict] = None,
    ) -> TaskRecord:
        risk_level = RiskLevel.parse(risk)
        rec = TaskRecord(
            id=f"tsk_{uuid.uuid4().hex[:12]}",
            goal=goal.strip(),
            risk=risk_level.value,
            domain=domain,
            summary=summary,
            meta=meta or {},
        )
        if requires_human(risk_level) and risk_level != RiskLevel.L1_LOCAL_MUTATE:
            # High-risk starts blocked until explicitly released for run,
            # unless it's only observe — L2+ need approval before run.
            if risk_level.value.startswith("L2") or risk_level.value.startswith("L3") or risk_level.value.startswith("L4"):
                rec.status = TaskStatus.BLOCKED_AWAITING_HUMAN.value
                rec.awaiting_human = True
        with self._lock:
            data = self._read()
            data.setdefault("tasks", []).append(asdict(rec))
            self._write(data)
        return rec

    def update(self, task_id: str, **fields: Any) -> Optional[TaskRecord]:
        with self._lock:
            data = self._read()
            for i, raw in enumerate(data.get("tasks", [])):
                if raw.get("id") == task_id:
                    raw.update({k: v for k, v in fields.items() if v is not None})
                    raw["updated_at"] = _utc_now()
                    data["tasks"][i] = raw
                    self._write(data)
                    return TaskRecord(**raw)
        return None

    def claim_runnable(self, *, night: bool = False, allow_l1_night: bool = True) -> Optional[TaskRecord]:
        with self._lock:
            data = self._read()
            for i, raw in enumerate(data.get("tasks", [])):
                if raw.get("status") != TaskStatus.QUEUED.value:
                    continue
                risk = RiskLevel.parse(raw.get("risk"))
                if night and not can_run_at_night(risk, allow_l1=allow_l1_night):
                    raw["status"] = TaskStatus.BLOCKED_AWAITING_HUMAN.value
                    raw["awaiting_human"] = True
                    raw["updated_at"] = _utc_now()
                    data["tasks"][i] = raw
                    self._write(data)
                    continue
                raw["status"] = TaskStatus.RUNNING.value
                raw["updated_at"] = _utc_now()
                data["tasks"][i] = raw
                self._write(data)
                return TaskRecord(**raw)
        return None

    def board(self) -> Dict[str, Any]:
        tasks = self.list_tasks()
        by_status: Dict[str, List[dict]] = {}
        for t in tasks:
            by_status.setdefault(t.status, []).append(
                {
                    "id": t.id,
                    "goal": t.goal,
                    "risk": t.risk,
                    "stage": t.stage,
                    "domain": t.domain,
                    "awaiting_human": t.awaiting_human,
                    "summary": t.summary or t.result_summary,
                }
            )
        pending_human = [
            t for t in tasks if t.status == TaskStatus.BLOCKED_AWAITING_HUMAN.value or t.awaiting_human
        ]
        return {
            "counts": {k: len(v) for k, v in by_status.items()},
            "awaiting_human": [
                {"id": t.id, "goal": t.goal, "risk": t.risk, "stage": t.stage} for t in pending_human
            ],
            "by_status": by_status,
        }
