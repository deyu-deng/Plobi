"""Master orchestrator helpers — keep context clean."""

from __future__ import annotations

from typing import Any, Dict, Optional


MASTER_SYSTEM_ADDENDUM = """
You are the Plobi Master (总指挥). Behave like a competent human manager:

- Keep your context CLEAN. Do not ingest raw tool logs, screenshots dumps, or
  full free-tier chat transcripts.
- You only: clarify goals, assign work, track status, request human approval at
  stage gates / high risk, and accept summarized results.
- Delegate dirty work to sub-agents and HID/aigw workers via North Star tools
  (`plobi_task_*`, `plobi_route`, `plobi_hid_run`).
- Never drive Marvis/Cursor GUIs yourself; workers burn free quotas.
- Prefer short status updates. Escalate decisions with a one-line ask.
""".strip()


def summarize_for_master(
    *,
    goal: str,
    status: str,
    risk: str,
    stage: str,
    result: str = "",
    error: str = "",
    max_chars: int = 800,
) -> str:
    """Compress worker outcome into a manager-safe summary."""
    body = result or error or "(no details)"
    body = body.strip().replace("\r\n", "\n")
    if len(body) > max_chars:
        body = body[: max_chars - 20] + "\n… [truncated for master]"
    return (
        f"goal: {goal}\n"
        f"status: {status}\n"
        f"risk: {risk}\n"
        f"stage: {stage}\n"
        f"summary:\n{body}"
    )


def master_dispatch_plan(
    goal: str,
    *,
    risk: str = "L1",
    domain: str = "generic",
    surface_hint: Optional[str] = None,
) -> Dict[str, Any]:
    """Pure helper: suggest how Master should route a goal (no side effects)."""
    return {
        "goal": goal,
        "risk": risk,
        "domain": domain,
        "suggested_steps": [
            "plobi_task_enqueue",
            "plobi_route",
            "plobi_hid_run or aigw worker",
            "plobi_task_complete with summary only",
            "plobi_stage_advance (may pause for human)",
        ],
        "surface_hint": surface_hint,
        "master_rules": [
            "do_not_load_raw_logs",
            "paid_api_for_master_only",
            "free_tier_via_workers",
        ],
    }
