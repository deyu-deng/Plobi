"""档案那一族（``dossier`` 门类）的**注册表工具**。

从 provider 搬过来（``WP-HUMAN-REGISTRY-TOOLS``，11-14）：**provider 只管脑页**，
人读面（笔记／日记／档案）那 17 把改成注册表工具 ⇒ 门类 note／diary／dossier 在
``toolsets.py`` 里那一层**一个字没动**，Tools 页那一格照样管用。

这一族五把：``dossier_read``／``dossier_write``（基本两把）＋ ``dossier_list``／
``dossier_task_toggle``／``dossier_stage_update``（细动作，11-14 补）。

**schema 从 provider 逐字搬过来**——描述文案是给模型看的契约，一个字不动。
**闸的口径逐字不变**：档案本体的字段级写**不过** L1 那道闸（那是 Agent 自己维护
档案用的手）；当日清单 ``tasks.json`` 走整份覆盖那一路，闸门照旧。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

from tools.human_common import (
    DOSSIER_PATH_PATTERN,
    DOSSIER_PREFIX,
    FACE_HINTS,
    json_result,
    read_face,
    resolve_dossier_by_name,
    to_logical,
    write_face,
)

logger = logging.getLogger(__name__)

DOSSIER_READ_TOOL = "dossier_read"
DOSSIER_WRITE_TOOL = "dossier_write"
DOSSIER_LIST_TOOL = "dossier_list"
DOSSIER_TASK_TOGGLE_TOOL = "dossier_task_toggle"
DOSSIER_STAGE_UPDATE_TOOL = "dossier_stage_update"

DOSSIER_READ_SCHEMA = {
    "name": DOSSIER_READ_TOOL,
    "description": (
        "Read one file of an agent's dossier on the human side by its LOGICAL "
        "path, ``Dossiers/...`` — the record "
        "``Dossiers/<category>/<name>/dossier.json`` (goal, status, stages), "
        "the day-by-day checklist beside it (``tasks.json``), or the index "
        "beside that. Only paths under ``Dossiers/`` resolve — anything "
        "outside that prefix belongs to another tool."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Logical path of the dossier file, e.g. "
                    "Dossiers/projects/Aura/dossier.json"
                ),
            }
        },
        "required": ["path"],
    },
    }

DOSSIER_WRITE_SCHEMA = {
    "name": DOSSIER_WRITE_TOOL,
    "description": (
        "Edit one agent's dossier on the human side — the structured file "
        "``Dossiers/<category>/<name>/dossier.json`` — by LOGICAL path "
        "(``path``), or by the dossier's own name (``agent``, e.g. ``Aura``). "
        "Name only the fields you are changing: ``goal`` (what this project is "
        "for), ``status`` (where it stands now, one paragraph), ``stages`` (the "
        "whole stage list — every stage needs ``id`` and ``title``, and ``done`` "
        "is true only for a stage that is actually finished; pass every stage, "
        "including the ones you are not changing) and ``log_append`` (one "
        "paragraph appended to the running history). Every other key in the file "
        "is kept exactly as it is, including keys you do not recognise, and "
        "``updated`` is set to today for you. The file has to exist and be valid "
        "JSON: a broken file is refused, never overwritten, and a stage list with "
        "a wrong shape is refused without writing anything. For another file in "
        "the same dossier folder (the day-by-day ``tasks.json``), give "
        "``content`` instead and it is written as-is. This is the tool for "
        "editing a dossier; ``dossier_read`` reads one back."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Logical path of the dossier file, e.g. "
                    "Dossiers/projects/Aura/dossier.json"
                ),
            },
            "agent": {
                "type": "string",
                "description": (
                    "The dossier's own name instead of its path, e.g. ``Aura``. "
                    "Give either ``path`` or ``agent``, not both."
                ),
            },
            "goal": {
                "type": "string",
                "description": (
                    "The new goal of this project — what it is for. Omit to keep "
                    "the current one."
                ),
            },
            "status": {
                "type": "string",
                "description": (
                    "The new status — where this project stands now, one "
                    "paragraph. Omit to keep the current one."
                ),
            },
            "stages": {
                "type": "array",
                "description": (
                    "The whole stage list, replacing the current one. Each stage "
                    "needs ``id`` and ``title``; ``done`` is true only for a "
                    "stage that is really finished."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "title": {"type": "string"},
                        "detail": {"type": "string"},
                        "done": {"type": "boolean"},
                    },
                    "required": ["id", "title"],
                },
            },
            "log_append": {
                "type": "string",
                "description": (
                    "One paragraph appended to the running history. Omit to leave "
                    "the history alone."
                ),
            },
            "content": {
                "type": "string",
                "description": (
                    "The full new contents of another file in the same dossier "
                    "folder (the day-by-day ``tasks.json``). Give it instead of "
                    "the fields above."
                ),
            },
        },
        "required": ["path"],
    },
    }

DOSSIER_LIST_SCHEMA = {
    "name": DOSSIER_LIST_TOOL,
    "description": (
        "List the dossiers on the human side — one row per agent in the "
        "roster: its goal, where it stands, its stages and whether the "
        "dossier file exists. You get the summary, not the whole file; "
        "read one with ``dossier_read``."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
    }

DOSSIER_TASK_TOGGLE_SCHEMA = {
    "name": DOSSIER_TASK_TOGGLE_TOOL,
    "description": (
        "Tick or untick one item in an agent's day-by-day checklist (the "
        "file beside the dossier). The checklist belongs to the agent, "
        "so the file records who changed the item — ``agent`` for you, "
        "``user`` for them. To see the items first, read "
        "``Dossiers/<category>/<name>/tasks.json`` with ``dossier_read``."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "agent": {
                "type": "string",
                "description": "Whose checklist — by name (e.g. ``Aura``).",
            },
            "path": {
                "type": "string",
                "description": (
                    "Logical path of the checklist file, e.g. "
                    "Dossiers/projects/Aura/tasks.json"
                ),
            },
            "date": {
                "type": "string",
                "description": "Which day — ``YYYY-MM-DD``.",
            },
            "id": {
                "type": "string",
                "description": "Which item — its id in that day's list.",
            },
            "done": {
                "type": "boolean",
                "description": "True to tick it, false to untick it.",
            },
        },
        "required": ["date", "id", "done"],
    },
    }

DOSSIER_STAGE_UPDATE_SCHEMA = {
    "name": DOSSIER_STAGE_UPDATE_TOOL,
    "description": (
        "Change **one** stage of an agent's dossier — mark it finished, "
        "rename it, or adjust its detail — without sending the whole "
        "stage list back. ``dossier_write`` replaces the stage list as a "
        "group (a stage you leave out is deleted); this one touches a "
        "single stage by id."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "agent": {
                "type": "string",
                "description": "Whose dossier — by name (e.g. ``Aura``).",
            },
            "path": {
                "type": "string",
                "description": (
                    "Logical path of the dossier file, e.g. "
                    "Dossiers/projects/Aura/dossier.json"
                ),
            },
            "stage_id": {
                "type": "string",
                "description": "Which stage — its id in the dossier.",
            },
            "done": {
                "type": "boolean",
                "description": "True to mark the stage finished.",
            },
            "title": {
                "type": "string",
                "description": "A new title for that stage.",
            },
            "detail": {
                "type": "string",
                "description": "A new detail line for that stage.",
            },
        },
        "required": ["stage_id"],
    },
    }


# ── 五把手 ──────────────────────────────────────────────────────────────────


def dossier_read(args: Dict[str, Any], **_kw: Any) -> str:
    """读一份档案（``Dossiers/<类>/<名>/…``）。"""
    return json_result(read_face(args, (DOSSIER_PREFIX,), FACE_HINTS[DOSSIER_PREFIX]))


def dossier_list(args: Dict[str, Any], **_kw: Any) -> str:
    """列档案（名册里每一行 ＋ 它那份档案的摘要）。

    真源＝屏上那张卡（``agent_dossier.build_index``）⇒ 同一个数据。
    给的是**摘要**（目标／现状／阶段／有没有本体），不是整份档案；
    读一份用 ``dossier_read``。
    """
    from plobi.human import agent_dossier
    from plobi.human import dossier_profile as dp
    from plobi.mind.paths import MindUnavailable

    from tools.human_common import resolve_roots_or_error

    try:
        roots = resolve_roots_or_error()
        index = agent_dossier.build_index()
    except MindUnavailable as exc:
        return json_result({"success": False, "error": str(exc)})
    except Exception as exc:  # noqa: BLE001
        logger.warning("[human] dossier_list failed: %s", exc)
        return json_result({"success": False, "error": f"列档案失败：{exc}"})

    rows = []
    for agent in index.get("agents") or []:
        name = str(agent.get("name") or "")
        logical = ""
        if name:
            resolved = resolve_dossier_by_name(roots.mind, name)
            if resolved is not None:
                logical = f"{resolved[0]}/{dp.PROFILE_FILENAME}"
        rows.append(
            {
                "name": name,
                "displayName": agent.get("displayName", ""),
                "category": agent.get("category", ""),
                "status": agent.get("status", ""),
                "goal": agent.get("goal", ""),
                "summary": agent.get("summary", ""),
                "stages": agent.get("stages", []),
                "milestones": agent.get("milestones", {}),
                "updated": agent.get("updated", ""),
                "profileExists": agent.get("profileExists", False),
                "path": logical,
            }
        )
    return json_result({
        "success": True,
        "agents": rows,
        "totals": index.get("totals", {}),
    })


def dossier_task_toggle(args: Dict[str, Any], **_kw: Any) -> str:
    """勾掉/取消勾掉当日清单里的**一条**（屏上那一下勾选的那条路）。

    ``doneBy`` 记的是**最后一次动它的人**：Agent 动的是 ``agent``，人动的是 ``user``。
    """
    from plobi.human import dossier_tasks as dt
    from plobi.mind.paths import (
        MindUnavailable,
        UnsafeMindPath,
        safe_target,
    )

    from tools.human_common import resolve_roots_or_error

    payload = args or {}
    agent = str(payload.get("agent") or "").strip()
    shown = str(payload.get("path") or "").strip().replace("\\", "/")
    date = str(payload.get("date") or "").strip()
    item_id = str(payload.get("id") or "").strip()
    done = payload.get("done")
    if not date:
        return json_result({"success": False, "error": "date 是必需的：哪一天的那一条"})
    if not item_id:
        return json_result({"success": False, "error": "id 是必需的：哪一条"})
    if not isinstance(done, bool):
        return json_result({"success": False, "error": "done 要是一个布尔值（勾上还是取消）"})
    try:
        roots = resolve_roots_or_error()
    except MindUnavailable as exc:
        return json_result({"success": False, "error": str(exc)})

    if shown:
        logical = to_logical(shown)
    elif agent:
        resolved = resolve_dossier_by_name(roots.mind, agent)
        if resolved is None:
            return json_result({
                "success": False,
                "error": f"名册与盘上都找不到叫 {agent!r} 的档案",
            })
        logical = f"{resolved[0]}/{dt.TASKS_FILENAME}"
    else:
        return json_result({
            "success": False,
            "error": "agent（或 path）是必需的：要说清改谁的清单",
        })
    if not logical.endswith("/" + dt.TASKS_FILENAME):
        return json_result({
            "success": False,
            "path": logical,
            "error": f"只认 Dossiers/<类>/<名>/{dt.TASKS_FILENAME} 这一种形状",
        })
    try:
        target = safe_target(roots, logical)
    except UnsafeMindPath as exc:
        return json_result({"success": False, "error": str(exc)})
    try:
        dt.toggle_task(target, date, item_id, done, actor=dt.DONE_BY_AGENT)
    except dt.TasksError as exc:
        return json_result({"success": False, "path": logical, "error": str(exc)})
    except OSError as exc:
        return json_result({"success": False, "path": logical, "error": f"写不下去：{exc}"})
    return json_result({
        "success": True,
        "path": logical,
        "date": date,
        "id": item_id,
        "done": done,
        "doneBy": dt.DONE_BY_AGENT,
    })


def dossier_stage_update(args: Dict[str, Any], **_kw: Any) -> str:
    """改**一个**阶段（``dossier_write`` 的 stages 是整组替换）。

    走档案本体那套字段级写：只改点名的几格、文件里不认识的键原样留着、落盘原子。
    """
    from plobi.human import dossier_profile as dp
    from plobi.mind.paths import (
        MindUnavailable,
        UnsafeMindPath,
        is_safe_relative,
        safe_target,
    )

    from tools.human_common import resolve_roots_or_error

    payload = args or {}
    agent = str(payload.get("agent") or "").strip()
    shown = str(payload.get("path") or "").strip().replace("\\", "/")
    stage_id = str(payload.get("stage_id") or "").strip()
    if not stage_id:
        return json_result({"success": False, "error": "stage_id 是必需的：改哪一条阶段"})
    changes = {
        key: payload[key]
        for key in ("done", "title", "detail")
        if payload.get(key) is not None
    }
    if not changes:
        return json_result({
            "success": False,
            "error": "至少给一格：done／title／detail。",
        })
    try:
        roots = resolve_roots_or_error()
    except MindUnavailable as exc:
        return json_result({"success": False, "error": str(exc)})

    if shown:
        logical = to_logical(shown)
        if not logical.endswith("/" + dp.PROFILE_FILENAME):
            return json_result({
                "success": False,
                "path": logical,
                "error": f"只认 Dossiers/<类>/<名>/{dp.PROFILE_FILENAME} 这一种形状",
            })
        if not is_safe_relative(logical):
            return json_result({"success": False, "error": f"{shown} 不在可写的面里"})
        try:
            target = safe_target(roots, logical)
        except UnsafeMindPath as exc:
            return json_result({"success": False, "error": str(exc)})
    elif agent:
        resolved = resolve_dossier_by_name(roots.mind, agent)
        if resolved is None:
            return json_result({
                "success": False,
                "error": f"名册与盘上都找不到叫 {agent!r} 的档案",
            })
        logical, target = resolved
        logical = f"{logical}/{dp.PROFILE_FILENAME}"
    else:
        return json_result({
            "success": False,
            "error": "agent（或 path）是必需的：要说清改哪一份档案",
        })

    current = dp.read_profile(target)
    if current.error or current.profile is None:
        return json_result({
            "success": False,
            "path": logical,
            "error": current.error or f"{dp.PROFILE_FILENAME} 读不出来",
        })
    stages = [stage.to_dict() for stage in current.profile.stages]
    hit = next(
        (row for row in stages if str(row.get("id") or "") == stage_id), None
    )
    if hit is None:
        known = "、".join(str(row.get("id") or "") for row in stages) or "（没有）"
        return json_result({
            "success": False,
            "path": logical,
            "error": f"这份档案里没有 id 为「{stage_id}」的阶段；现有的是：{known}",
        })
    hit.update(changes)
    try:
        result = dp.apply_profile_update(target, stages=stages)
    except dp.ProfileError as exc:
        return json_result({"success": False, "path": logical, "error": str(exc)})
    except OSError as exc:
        return json_result({"success": False, "path": logical, "error": f"写不下去：{exc}"})
    profile = result.profile
    if result.error or profile is None:
        return json_result({
            "success": False,
            "path": logical,
            "error": result.error or "写完读不回来：盘上那份档案不在预期形状里",
        })
    return json_result({
        "success": True,
        "path": logical,
        "stage": hit,
        "updated": profile.updated,
        "stages": {
            "done": profile.milestones_done,
            "total": profile.milestones_total,
        },
    })


def dossier_write(args: Dict[str, Any], **_kw: Any) -> str:
    """档案本体（``dossier.json``）的**字段级**编辑。

    走 :func:`plobi.human.dossier_profile.update_profile`：只改点名的几格、文件里
    我们不认识的键原样留着（只增不改）、落盘原子（临时文件 ＋ ``os.replace``）。

    **不过 L1 那道闸**：这道手是给 Agent 自己维护档案用的（别的写手是
    整份文件覆盖，那几把手的闸门照旧——闸开着才落盘，闸关着进待批）。

    档案目录里的**另一个文件**（当日清单 ``tasks.json``）走 ``content`` 那一路：
    整份覆盖，闸门与别的写手一样。

    校验全在**落盘前**：路径形状、文件可解析、字段形状；有一条不合法就报错，
    盘上那一份**一个字节都不动**。
    """
    from plobi.human import dossier_profile as dp
    from plobi.mind.paths import (
        MindUnavailable,
        UnsafeMindPath,
        is_safe_relative,
        safe_target,
    )

    from tools.human_common import resolve_roots_or_error

    payload = args or {}
    shown = str(payload.get("path") or "").strip().replace("\\", "/")
    agent = str(payload.get("agent") or "").strip()
    if not shown and not agent:
        return json_result({"success": False, "error": "path（或 agent）是必需的：要说清改哪一份档案"})

    try:
        roots = resolve_roots_or_error()
    except MindUnavailable as exc:
        return json_result({"success": False, "error": str(exc)})

    if shown:
        logical = to_logical(shown)
        content = payload.get("content")
        if isinstance(content, str) and content.strip():
            # 档案目录里的**另一个文件**（当日清单 ``tasks.json``）：整份覆盖，
            # 走别的写手那条闸（旧那把通吃的 ``surface_write`` 有这个能力，
            # 按种类拆开之后不许丢）。档案本体自己不走这一路——它只吃字段。
            if DOSSIER_PATH_PATTERN.match(logical):
                return json_result({
                    "success": False,
                    "path": logical,
                    "error": (
                        "档案本体（dossier.json）只走字段级那一路：给 goal／status／stages／"
                        "log_append，别整份覆盖。"
                    ),
                })
            return json_result(write_face(
                {"path": shown, "content": content},
                (DOSSIER_PREFIX,),
            ))
        if not DOSSIER_PATH_PATTERN.match(logical):
            return json_result({
                "success": False,
                "error": (
                    f"{shown} 不是档案本体：只认 "
                    "Dossiers/<类>/<名>/dossier.json 这一种形状。"
                ),
            })
        if not is_safe_relative(logical):
            return json_result({"success": False, "error": f"{shown} 不在可写的面里"})
        try:
            target = safe_target(roots, logical)
        except UnsafeMindPath as exc:
            return json_result({"success": False, "error": str(exc)})
    else:
        resolved = resolve_dossier_by_name(roots.mind, agent)
        if resolved is None:
            return json_result({
                "success": False,
                "error": f"名册与盘上都找不到叫 {agent!r} 的档案",
            })
        logical, target = resolved

    changes = {
        key: payload[key]
        for key in ("goal", "status", "stages", "log_append")
        if key in payload and payload[key] is not None
    }
    if not changes:
        return json_result({
            "success": False,
            "error": "没有要改的格子：至少给 goal／status／stages／log_append 里的一个",
        })

    try:
        result = dp.apply_profile_update(target, **changes)
    except dp.ProfileError as exc:
        return json_result({"success": False, "path": logical, "error": str(exc)})
    except OSError as exc:
        logger.warning("[human] dossier_write(%s) failed: %s", logical, exc)
        return json_result({"success": False, "path": logical, "error": f"写不下去：{exc}"})

    profile = result.profile
    if result.error or profile is None:
        # 写完读不回来 = 没落成，不许报成功。
        return json_result({
            "success": False,
            "path": logical,
            "error": result.error or "写完读不回来：盘上那份档案不在预期形状里",
        })
    return json_result({
        "success": True,
        "path": logical,
        "updated": profile.updated,
        "goal": profile.goal,
        "status": profile.status,
        "stages": {"done": profile.milestones_done, "total": profile.milestones_total},
    })


# ── 注册 ────────────────────────────────────────────────────────────────────

from tools.registry import registry

registry.register(
    name=DOSSIER_READ_TOOL,
    toolset="dossier",
    schema=DOSSIER_READ_SCHEMA,
    handler=dossier_read,
    description="Read one dossier record by its logical path.",
    emoji="🗂",
)
registry.register(
    name=DOSSIER_WRITE_TOOL,
    toolset="dossier",
    schema=DOSSIER_WRITE_SCHEMA,
    handler=dossier_write,
    description="Field-level edit of a dossier record (goal / status / stages / log).",
    emoji="🗂",
)
registry.register(
    name=DOSSIER_LIST_TOOL,
    toolset="dossier",
    schema=DOSSIER_LIST_SCHEMA,
    handler=dossier_list,
    description="List the dossiers (roster rows + summaries).",
    emoji="🗂",
)
registry.register(
    name=DOSSIER_TASK_TOGGLE_TOOL,
    toolset="dossier",
    schema=DOSSIER_TASK_TOGGLE_SCHEMA,
    handler=dossier_task_toggle,
    description="Tick / untick one item in a day's task list.",
    emoji="🗂",
)
registry.register(
    name=DOSSIER_STAGE_UPDATE_TOOL,
    toolset="dossier",
    schema=DOSSIER_STAGE_UPDATE_SCHEMA,
    handler=dossier_stage_update,
    description="Change one stage of a dossier (done / title / detail).",
    emoji="🗂",
)
