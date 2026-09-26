"""NorthStar — deep public module for the edge runtime.

Callers (agent tool, HTTP API, skills) should use only this class.
Internal packages (queue, hid, risk, …) are implementation details.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from .compute_router import ComputeRouter
from .diagnose import build_upgrade_report
from .domains import DomainRegistry, DomainSlot
from .hid import HidJob, HidWorkerPool
from .kanban_bridge import mirror_enqueue_to_kanban
from .learning import PassiveLearner
from .master import master_dispatch_plan, summarize_for_master
from .night import NightPolicy, morning_report, tick_night_queue
from .preview_bus import PreviewBus
from .queue import TaskQueue, TaskStatus


class NorthStar:
    """Narrow façade over risk queue, HID fleet, preview, and ops.

    Reuses Hermes where possible:
    - execution/collaboration board → optional kanban mirror
    - 24/7 messaging → gateway + cron (not reimplemented here)
    - desktop shell → existing Electron preview rail via bus + HTTP
    - free-tier protocol surfaces → aigw (Antigravity), HID only when needed
    """

    def __init__(self) -> None:
        self._queue = TaskQueue()
        self._preview = PreviewBus()
        self._hid = HidWorkerPool()
        self._router = ComputeRouter()
        self._domains = DomainRegistry()
        self._learner = PassiveLearner()

    # ----- tasks / stages / approvals -----

    def task(
        self,
        action: str,
        *,
        goal: str = "",
        task_id: str = "",
        risk: str = "L1",
        domain: str = "generic",
        summary: str = "",
        result: str = "",
        reason: str = "",
        status: str = "",
        stage: str = "",
        surface: str = "",
        route: str = "",
        mirror_kanban: bool = True,
        **_: Any,
    ) -> Dict[str, Any]:
        action = (action or "").strip().lower()
        if action == "enqueue":
            rec = self._queue.enqueue(goal, risk=risk, domain=domain, summary=summary)
            kanban_id = None
            if mirror_kanban:
                kanban_id = mirror_enqueue_to_kanban(rec)
                if kanban_id:
                    self._queue.update(rec.id, meta={**rec.meta, "kanban_task_id": kanban_id})
                    rec = self._queue.get(rec.id) or rec
            return {"ok": True, "task": rec.__dict__, "kanban_task_id": kanban_id}
        if action == "board":
            return self._queue.board()
        if action == "get":
            rec = self._queue.get(task_id)
            return {"ok": bool(rec), "task": None if rec is None else rec.__dict__}
        if action == "approve":
            updated = self._queue.update(
                task_id, status=TaskStatus.QUEUED.value, awaiting_human=False
            )
            return {"ok": bool(updated), "task": None if updated is None else updated.__dict__}
        if action == "reject":
            updated = self._queue.update(
                task_id,
                status=TaskStatus.CANCELLED.value,
                awaiting_human=False,
                result_summary=reason or "rejected by human",
            )
            return {"ok": bool(updated), "task": None if updated is None else updated.__dict__}
        if action == "complete":
            master = summarize_for_master(
                goal=goal or task_id,
                status="completed",
                risk=risk,
                stage=stage or "done",
                result=result,
            )
            updated = self._queue.update(
                task_id,
                status=TaskStatus.COMPLETED.value,
                result_summary=master,
                awaiting_human=False,
            )
            return {
                "ok": bool(updated),
                "task": None if updated is None else updated.__dict__,
                "master_summary": master,
            }
        if action == "update":
            fields = {
                k: v
                for k, v in {
                    "status": status or None,
                    "summary": summary or None,
                    "result_summary": result or None,
                    "route": route or None,
                    "surface": surface or None,
                    "stage": stage or None,
                }.items()
                if v is not None
            }
            updated = self._queue.update(task_id, **fields)
            return {"ok": bool(updated), "task": None if updated is None else updated.__dict__}
        if action == "stage_status":
            rec = self._queue.get(task_id)
            if not rec:
                from .stages import StageMachine

                return StageMachine(domain=domain or "code").to_dict()
            return rec.stage_machine().to_dict() | {"task_id": task_id}
        if action == "stage_advance":
            return self._stage_advance(task_id)
        if action == "stage_approve":
            return self._stage_approve(task_id)
        return {"ok": False, "error": f"unknown task action: {action}"}

    def _stage_advance(self, task_id: str) -> Dict[str, Any]:
        rec = self._queue.get(task_id)
        if not rec:
            return {"ok": False, "error": "task not found"}
        sm = rec.stage_machine()
        result = sm.request_advance()
        rec.apply_stage(sm)
        self._queue.update(
            task_id,
            stage=rec.stage,
            awaiting_human=rec.awaiting_human,
            status=rec.status,
            domain=rec.domain,
        )
        return {**result, "task_id": task_id}

    def _stage_approve(self, task_id: str) -> Dict[str, Any]:
        rec = self._queue.get(task_id)
        if not rec:
            return {"ok": False, "error": "task not found"}
        sm = rec.stage_machine()
        sm.awaiting_human = True
        result = sm.approve()
        rec.apply_stage(sm)
        status = TaskStatus.COMPLETED.value if rec.stage == "done" else TaskStatus.QUEUED.value
        self._queue.update(
            task_id,
            stage=rec.stage,
            awaiting_human=rec.awaiting_human,
            status=status,
        )
        return {**result, "task_id": task_id}

    # ----- compute (HID + aigw routing) -----

    def compute(
        self,
        action: str,
        *,
        surface: str = "marvis",
        prefer: Optional[str] = None,
        prompt: str = "",
        goal: str = "",
        actions: Optional[List[Dict[str, Any]]] = None,
        device_id: Optional[str] = None,
        task_id: str = "",
        mock: Optional[bool] = None,
        include_raw: bool = False,
        risk: str = "L1",
        stage: str = "rough",
        **_: Any,
    ) -> Dict[str, Any]:
        action = (action or "").strip().lower()
        if action == "route":
            d = self._router.route(surface, prefer=prefer)
            return {
                "surface": d.surface,
                "path": d.path,
                "reason": d.reason,
                "fallback": d.fallback,
                "table": self._router.table(),
            }
        if action == "hid_status":
            return self._hid.status()
        if action == "hid_run":
            force_mock = True if mock is None else bool(mock)
            job = HidJob(
                job_id=f"hid_{uuid.uuid4().hex[:10]}",
                surface=surface,
                prompt=prompt or goal,
                actions=actions or [],
                device_id=device_id,
                meta={"force_mock": force_mock},
            )
            result = self._hid.run_job(job)
            master = summarize_for_master(
                goal=prompt or goal,
                status="ok" if result.ok else "failed",
                risk=risk,
                stage=stage,
                result=result.summary,
            )
            if task_id:
                self._queue.update(
                    task_id,
                    surface=surface,
                    route="hid",
                    result_summary=master,
                    status=TaskStatus.COMPLETED.value if result.ok else TaskStatus.FAILED.value,
                )
            return {
                "ok": result.ok,
                "job_id": result.job_id,
                "device_id": result.device_id,
                "surface": result.surface,
                "summary": result.summary,
                "master_summary": master,
                "raw": result.raw if include_raw else {"omitted": True},
            }
        return {"ok": False, "error": f"unknown compute action: {action}"}

    # ----- preview -----

    def preview(
        self,
        action: str,
        *,
        title: str = "",
        priority: str = "artifact",
        kind: str = "file",
        url: str = "",
        path: str = "",
        text: str = "",
        auto_open: bool = True,
        limit: int = 50,
        **_: Any,
    ) -> Dict[str, Any]:
        action = (action or "").strip().lower()
        if action == "push":
            item = self._preview.push(
                title or "preview",
                priority=priority,
                kind=kind,
                url=url,
                path=path,
                text=text,
                auto_open=auto_open,
            )
            return {"ok": True, "item": item.__dict__}
        if action == "list":
            return {"items": [i.__dict__ for i in self._preview.list_items(limit=limit)]}
        if action == "latest":
            item = self._preview.latest_for_auto_open()
            return {"item": None if item is None else item.__dict__}
        return {"ok": False, "error": f"unknown preview action: {action}"}

    # ----- ops: master helpers, night, mobile, domains, learn, diagnose -----

    def ops(
        self,
        action: str,
        *,
        goal: str = "",
        risk: str = "L1",
        domain: str = "generic",
        surface: str = "",
        status: str = "unknown",
        stage: str = "intake",
        result: str = "",
        error: str = "",
        max_chars: int = 800,
        instruction: str = "",
        text: str = "",
        kind: str = "",
        id: str = "",
        label: str = "",
        default_risk: str = "L1_local_mutate",
        enabled: bool = True,
        title: str = "",
        steps: Optional[List[str]] = None,
        draft_id: str = "",
        approve: bool = False,
        **_: Any,
    ) -> Dict[str, Any]:
        action = (action or "").strip().lower()
        if action == "master_plan":
            return master_dispatch_plan(goal, risk=risk, domain=domain, surface_hint=surface or None)
        if action == "master_summarize":
            return {
                "summary": summarize_for_master(
                    goal=goal,
                    status=status,
                    risk=risk,
                    stage=stage,
                    result=result,
                    error=error,
                    max_chars=max_chars,
                )
            }
        if action == "morning_report":
            return morning_report(self._queue, policy=NightPolicy())
        if action == "night_tick":
            return tick_night_queue(self._queue, policy=NightPolicy())
        if action == "mobile_board":
            board = self._queue.board()
            preview = self._preview.latest_for_auto_open()
            return {
                "board": board,
                "preview": None if preview is None else preview.__dict__,
                "capabilities": ["status", "approve", "reject", "instruct", "preview"],
                "no_remote_mouse": True,
            }
        if action == "mobile_instruct":
            instruction = (instruction or text or "").strip()
            if not instruction:
                return {"ok": False, "error": "instruction required"}
            rec = self._queue.enqueue(instruction, risk=risk, domain=domain or "generic")
            return {"ok": True, "enqueued": rec.__dict__}
        if action == "domain_list":
            domains = self._domains.list_domains(kind=kind or None)
            return {"domains": [d.__dict__ for d in domains]}
        if action == "domain_register":
            if not id:
                return {"ok": False, "error": "id required"}
            slot = DomainSlot(
                id=id,
                kind=kind or "money",
                label=label or id,
                default_risk=default_risk,
                enabled=enabled,
            )
            self._domains.register(slot)
            return {"ok": True, "domain": slot.__dict__}
        if action == "diagnose":
            report = build_upgrade_report()
            enqueued = []
            for f in report["findings"]:
                if f.get("requires_human"):
                    rec = self._queue.enqueue(
                        f"Self-upgrade proposal: {f['module']} — {f['proposal']}",
                        risk="L4",
                        domain="generic",
                        summary=f["symptom"],
                    )
                    enqueued.append(rec.id)
            report["enqueued_task_ids"] = enqueued
            return report
        if action == "learn_observe":
            return self._learner.observe(title or "untitled", [str(s) for s in (steps or [])])
        if action == "learn_drafts":
            return {"drafts": self._learner.list_drafts()}
        if action == "learn_resolve":
            draft = self._learner.resolve_draft(draft_id, approve=approve)
            if not draft:
                return {"ok": False, "error": "draft not found"}
            return {"ok": True, "draft": draft}
        return {"ok": False, "error": f"unknown ops action: {action}"}


# Process-local singleton for tools/HTTP (deep module entry).
_SERVICE: Optional[NorthStar] = None


def get_north_star() -> NorthStar:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = NorthStar()
    return _SERVICE
