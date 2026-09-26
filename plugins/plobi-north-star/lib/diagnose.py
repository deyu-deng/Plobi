"""Self-diagnosis stubs — propose upgrades, never silent-apply L4 changes."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import List


@dataclass
class Finding:
    module: str
    symptom: str
    severity: str  # low | med | high
    proposal: str
    risk: str = "L4_self_modify"


def scan_basic() -> List[Finding]:
    """Lightweight host checks — extend with real profilers later."""
    findings: List[Finding] = []
    # Queue file growth / HID mock mode as examples
    try:
        from hermes_constants import get_hermes_home

        home = get_hermes_home()
    except Exception:
        from pathlib import Path

        home = Path.home() / ".hermes"

    tasks = home / "plobi" / "tasks.json"
    if tasks.exists() and tasks.stat().st_size > 2_000_000:
        findings.append(
            Finding(
                module="task_queue",
                symptom=f"tasks.json is large ({tasks.stat().st_size} bytes)",
                severity="med",
                proposal="Archive completed tasks older than 14d into tasks-archive.jsonl",
            )
        )

    findings.append(
        Finding(
            module="hid_worker",
            symptom="Calibrate Marvis focus hotkeys per host",
            severity="low",
            proposal="Store per-host HID calibration under ~/.hermes/plobi/hid_calibration.json",
            risk="L1_local_mutate",
        )
    )
    return findings


def build_upgrade_report() -> dict:
    findings = scan_basic()
    return {
        "generated_at": time.time(),
        "findings": [
            {
                "module": f.module,
                "symptom": f.symptom,
                "severity": f.severity,
                "proposal": f.proposal,
                "risk": f.risk,
                "requires_human": f.risk.startswith("L4") or f.risk.startswith("L3"),
            }
            for f in findings
        ],
        "policy": "Never auto-apply L4 self-modify; enqueue as blocked_awaiting_human.",
    }
