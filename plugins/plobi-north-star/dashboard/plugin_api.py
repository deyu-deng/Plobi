"""North Star HTTP API for the desktop / dashboard frontend.

Mounted at ``/api/plugins/plobi-north-star/`` by Hermes dashboard plugin
discovery. Auth uses the same session-token middleware as all
``/api/plugins/*`` routes.

This file is a thin HTTP adapter over the deep ``NorthStar`` façade.
Electron and web UI must call these routes — not import ``lib/*``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(_PLUGIN_ROOT / "lib") not in sys.path:
    # boot.py lives in lib/
    sys.path.insert(0, str(_PLUGIN_ROOT / "lib"))

from boot import get_north_star  # noqa: E402

router = APIRouter()


class TaskBody(BaseModel):
    action: str
    goal: str = ""
    task_id: str = ""
    risk: str = "L1"
    domain: str = "generic"
    summary: str = ""
    result: str = ""
    reason: str = ""
    status: str = ""
    stage: str = ""
    surface: str = ""
    route: str = ""
    mirror_kanban: bool = True


class ComputeBody(BaseModel):
    action: str
    surface: str = "marvis"
    prefer: Optional[str] = None
    prompt: str = ""
    goal: str = ""
    actions: Optional[List[Dict[str, Any]]] = None
    device_id: Optional[str] = None
    task_id: str = ""
    mock: Optional[bool] = None
    include_raw: bool = False
    risk: str = "L1"
    stage: str = "rough"


class PreviewBody(BaseModel):
    action: str
    title: str = ""
    priority: str = "artifact"
    kind: str = "file"
    url: str = ""
    path: str = ""
    text: str = ""
    auto_open: bool = True
    limit: int = Field(default=50, ge=1, le=200)


class OpsBody(BaseModel):
    action: str
    goal: str = ""
    risk: str = "L1"
    domain: str = "generic"
    surface: str = ""
    status: str = "unknown"
    stage: str = "intake"
    result: str = ""
    error: str = ""
    instruction: str = ""
    text: str = ""
    kind: str = ""
    id: str = ""
    label: str = ""
    title: str = ""
    steps: Optional[List[str]] = None
    draft_id: str = ""
    approve: bool = False


@router.get("/health")
async def health():
    return {
        "ok": True,
        "module": "plobi-north-star",
        "public": ["task", "compute", "preview", "ops"],
        "docs": "docs/plobi/north_star/API.md",
    }


@router.get("/board")
async def board():
    return get_north_star().task("board")


@router.post("/task")
async def task(body: TaskBody):
    return get_north_star().task(**body.model_dump())


@router.post("/compute")
async def compute(body: ComputeBody):
    return get_north_star().compute(**body.model_dump())


@router.post("/preview")
async def preview(body: PreviewBody):
    return get_north_star().preview(**body.model_dump())


@router.get("/preview")
async def preview_list(limit: int = 50):
    return get_north_star().preview("list", limit=limit)


@router.post("/ops")
async def ops(body: OpsBody):
    return get_north_star().ops(**body.model_dump())


@router.get("/morning-report")
async def morning():
    return get_north_star().ops("morning_report")
