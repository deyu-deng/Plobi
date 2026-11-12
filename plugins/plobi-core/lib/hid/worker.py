"""HID worker pool — multi-worker interface, single device default."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .device import DeviceRegistry
from .pico import PicoBridge


@dataclass
class HidJob:
    job_id: str
    surface: str
    prompt: str
    actions: List[Dict[str, Any]] = field(default_factory=list)
    device_id: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HidResult:
    ok: bool
    job_id: str
    device_id: str
    surface: str
    summary: str
    raw: Dict[str, Any] = field(default_factory=dict)


class HidWorkerPool:
    def __init__(self, registry: Optional[DeviceRegistry] = None):
        self.registry = registry or DeviceRegistry()

    def status(self) -> dict:
        return {
            "devices": [d.__dict__ for d in self.registry.list_devices()],
            "screen_lock": self.registry.screen.status(),
        }

    def run_job(self, job: HidJob, *, worker_id: str = "worker-1") -> HidResult:
        device = None
        if job.device_id:
            device = self.registry.get(job.device_id)
        if device is None:
            device = self.registry.pick_available(
                prefer_kind="mock" if job.meta.get("force_mock") else "pico2w"
            )
        if device is None:
            return HidResult(
                ok=False,
                job_id=job.job_id,
                device_id="",
                surface=job.surface,
                summary="no HID device available",
            )

        if not self.registry.screen.acquire(worker_id, timeout=float(job.meta.get("lock_timeout", 30))):
            return HidResult(
                ok=False,
                job_id=job.job_id,
                device_id=device.device_id,
                surface=job.surface,
                summary="screen lock busy — another HID job holds the foreground",
            )

        try:
            from .adapters import get_adapter

            adapter = get_adapter(job.surface)
            plan = adapter.plan(job.prompt, job.actions)
            bridge = PicoBridge(device.device_id, kind=device.kind, endpoint=device.endpoint)
            ping = bridge.ping()
            if not ping.get("ok"):
                return HidResult(
                    ok=False,
                    job_id=job.job_id,
                    device_id=device.device_id,
                    surface=job.surface,
                    summary=f"pico ping failed: {ping}",
                    raw={"ping": ping},
                )
            run = bridge.run_actions(plan.get("actions") or [])
            summary = adapter.summarize(job.prompt, run)
            return HidResult(
                ok=bool(run.get("ok")),
                job_id=job.job_id,
                device_id=device.device_id,
                surface=job.surface,
                summary=summary,
                raw={"ping": ping, "run": run, "plan": plan},
            )
        finally:
            self.registry.screen.release(worker_id)
