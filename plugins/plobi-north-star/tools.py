"""Thin agent-facing adapter — one deep tool over :class:`lib.facade.NorthStar`."""

from __future__ import annotations

import json
from typing import Any

from .lib.facade import get_north_star


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def plobi(args: dict, **kwargs) -> str:
    """Unified North Star tool.

    ``area``: task | compute | preview | ops
    ``action``: area-specific verb (see docs/plobi/north_star/API.md)
    """
    area = str(args.get("area") or "").strip().lower()
    action = str(args.get("action") or "").strip().lower()
    # Remaining keys are forwarded; facade ignores unknowns.
    payload = {k: v for k, v in args.items() if k not in {"area", "action"}}
    ns = get_north_star()
    if area == "task":
        return _json(ns.task(action, **payload))
    if area == "compute":
        return _json(ns.compute(action, **payload))
    if area == "preview":
        return _json(ns.preview(action, **payload))
    if area == "ops":
        return _json(ns.ops(action, **payload))
    return _json(
        {
            "ok": False,
            "error": f"unknown area: {area!r}",
            "hint": "area must be one of: task, compute, preview, ops",
        }
    )


# --- Back-compat shims (not registered as tools) for skills/tests migrating ---


def plobi_task_board(args: dict, **kwargs) -> str:
    return plobi({"area": "task", "action": "board", **args})


def plobi_mobile_board(args: dict, **kwargs) -> str:
    return plobi({"area": "ops", "action": "mobile_board", **args})


def plobi_morning_report(args: dict, **kwargs) -> str:
    return plobi({"area": "ops", "action": "morning_report", **args})


def plobi_task_approve(args: dict, **kwargs) -> str:
    return plobi({"area": "task", "action": "approve", **args})


def plobi_task_reject(args: dict, **kwargs) -> str:
    return plobi({"area": "task", "action": "reject", **args})


def plobi_mobile_instruct(args: dict, **kwargs) -> str:
    return plobi({"area": "ops", "action": "mobile_instruct", **args})


def plobi_preview_list(args: dict, **kwargs) -> str:
    return plobi({"area": "preview", "action": "list", **args})
