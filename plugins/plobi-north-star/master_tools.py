"""L1 Master dispatch narrow tools — kanban wrappers + secretary_ask.

B1 slice: the secretary (L1) only ever sees *dispatch / status / preview /
approval* for the collaboration board (docs/plobi/north_star/API.md
principle 4 "Narrow Master"). It never gets the raw ``kanban_*`` lifecycle
tools — those stay with workers (task-scoped) and orchestrator profiles.

WP-BE-5: ``plobi_secretary_ask`` is the §8.2 path for 「明天安排」/
「写早报」. It does **not** go through kanban. Kanban tools remain
registered but those two phrases are not implemented by the board.

Each kanban handler is a thin wrapper over the SAME code path the CLI and
dashboard use (``plobi_cli.kanban_db``), so the three surfaces cannot
drift:

- ``plobi_master_preview``  → ``kanban_db.dispatch_once(dry_run=True)``
- ``plobi_master_dispatch`` → ``kanban_db.dispatch_once()``
- ``plobi_master_status``   → ``kanban_db.board_stats()`` + list_tasks
- ``plobi_master_approve``  → ``specify_triage_task`` (triage→todo) then
  ``promote_task`` (todo/blocked→ready) — the "approve to dispatchable"
  chain, no LLM required.
- ``plobi_secretary_ask``   → agenda L2 + chatlog ``refresh_agenda``
  (and later N3 / aigw briefing)

Service gate: the tools are registered under the ``plobi_north_star``
toolset with ``check_plobi_master_mode()``. They appear only for
non-worker agents whose profile enables that toolset; dispatcher-spawned
workers (``PLOBI_KANBAN_TASK`` set) never see them.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict
from typing import Any, Optional

from tools.registry import tool_error

logger = logging.getLogger(__name__)

MASTER_TOOLSET = "plobi_north_star"


# ---------------------------------------------------------------------------
# Service gate
# ---------------------------------------------------------------------------


def _toolset_enabled_in_config(cfg: dict) -> bool:
    """True when the active profile opted into ``plobi_north_star``.

    Checks top-level ``toolsets`` *and* ``platform_toolsets.cli/gateway``
    (the shape the default profile actually stores). A ``master`` profile
    is not required (ARCH-RULINGS 裁定 6).
    """
    top = cfg.get("toolsets") or []
    if isinstance(top, list) and MASTER_TOOLSET in {str(item) for item in top}:
        return True
    platforms = cfg.get("platform_toolsets") or {}
    if isinstance(platforms, dict):
        for key in ("cli", "gateway"):
            listed = platforms.get(key) or []
            if isinstance(listed, list) and MASTER_TOOLSET in {str(item) for item in listed}:
                return True
    plugins = cfg.get("plugins") or {}
    if isinstance(plugins, dict):
        enabled = plugins.get("enabled") or []
        if isinstance(enabled, list) and "plobi-north-star" in enabled:
            return True
        entries = plugins.get("entries") or {}
        if isinstance(entries, dict):
            entry = entries.get("plobi-north-star") or {}
            if isinstance(entry, dict) and entry.get("enabled"):
                return True
    return False


def _profile_has_plobi_toolset() -> bool:
    try:
        from plobi_cli.config import load_config

        cfg = load_config()
        return _toolset_enabled_in_config(cfg)
    except Exception:
        return False


def check_plobi_master_mode() -> bool:
    """Service gate for L1 Master + secretary_ask tools.

    Dispatcher-spawned workers are excluded. The active profile (usually
    ``default``) must enable ``plobi_north_star`` — a ``master`` profile
    is not required and must not be created as a prerequisite.
    """
    if os.environ.get("PLOBI_KANBAN_TASK"):
        return False
    return _profile_has_plobi_toolset()


# ---------------------------------------------------------------------------
# WP-L1-MOUTH-SWITCH — L1 派工-family mouths ride a product switch (裁定 78)
# ---------------------------------------------------------------------------
#
# Five tools sit behind a user-facing switch instead of a hardcoded gate:
# ``plobi_master_status`` / ``plobi_master_preview`` / ``plobi_master_dispatch``
# / ``plobi_master_approve`` and the deep ``plobi`` tool. Handlers, schemas,
# ``SECRETARY_ASK_*`` and the intent table are all kept — that is exactly what
# 裁定 33.4 decided to keep ("handler 不删"); what it never decided was that the
# switch should live in this file. See the merged 33 / 33.1 / 33.2 row and the
# 33.4 row in ``Docs/DECISIONS.md``.

def check_plobi_l1_master_tools() -> bool:
    """True ⇔ the user turned these mouths on in the product.

    Reads ``agent.l1_master_tools_enabled`` (config.yaml, default False) and still
    requires the service gate underneath it, so a profile without the
    ``plobi_north_star`` toolset — or a dispatcher-spawned kanban worker — gets
    nothing from this switch. Composition, not a second policy: this answers
    "did the user want these", ``check_plobi_master_mode`` answers "can this
    profile serve them at all".

    Why a real predicate rather than ``return False``: a hardcoded False is not a
    legal carrier for a policy (裁定 78). While it was in place the user could
    tick these tools in ``plobi tools`` / config and they still would not appear
    — which is what P7 ("用户在 UI 上打开的东西，不得被下一次启动悄悄抹掉") and
    裁定 49 ("勾了就生效") forbid. Off stays the default; **reopening is ticking
    the switch, not editing this plugin.**

    Registered by ``plugins/plobi-north-star/__init__.py::register`` for the five
    tools listed above. ``plobi_secretary_ask`` / ``plobi_checkin_respond`` keep
    their own gate (``check_plobi_master_mode``) and are untouched here.
    """
    try:
        from plobi_cli.config import cfg_get, load_config

        enabled = bool(
            cfg_get(load_config(), "agent", "l1_master_tools_enabled", default=False)
        )
    except Exception:
        # A config read that fails must not hand L1 extra mouths.
        return False
    if not enabled:
        return False
    return check_plobi_master_mode()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _connect(board: Optional[str] = None):
    """Import + connect lazily so the module imports cleanly in non-kanban
    contexts (mirrors tools/kanban_tools._connect)."""
    from plobi_cli import kanban_db as kb

    return kb, kb.connect(board=board)


def _parse_bool_arg(args: dict, name: str, *, default: bool = False):
    value = args.get(name)
    if value is None:
        return default, None
    if isinstance(value, bool):
        return value, None
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True, None
    if text in {"false", "0", "no"}:
        return False, None
    return default, f"{name} must be a boolean or 'true'/'false'"


def _normalize_profile(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "-", "null"}:
        return None
    return text


def _board_slug(args: dict) -> Optional[str]:
    raw = args.get("board")
    if raw is None or str(raw).strip() == "":
        return None
    return str(raw).strip()


def _task_brief(task) -> dict[str, Any]:
    return {
        "id": task.id,
        "title": task.title,
        "status": task.status,
        "assignee": task.assignee,
        "priority": task.priority,
        "created_at": task.created_at,
    }


def _board_name(board: Optional[str]) -> str:
    if board:
        return board
    try:
        from plobi_cli import kanban_db as kb

        return kb.get_current_board()
    except Exception:
        return "default"


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

_BOARD_PROP = {
    "type": "string",
    "description": "Kanban board slug (omit for the active board).",
}

MASTER_STATUS_SCHEMA = {
    "name": "plobi_master_status",
    "description": (
        "Board status for the L1 secretary: per-status counts, tasks "
        "awaiting human approval (triage/blocked/review), and the ready "
        "queue that a dispatch would spawn."
    ),
    "parameters": {
        "type": "object",
        "properties": {"board": _BOARD_PROP},
        "required": [],
    },
}

MASTER_PREVIEW_SCHEMA = {
    "name": "plobi_master_preview",
    "description": (
        "Dry-run dispatch: show what a dispatcher tick WOULD do (reclaim, "
        "promote, spawn, skip) without mutating board state. Use before "
        "plobi_master_dispatch so Master approves before anything spawns."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "board": _BOARD_PROP,
            "max": {
                "type": "integer",
                "description": "Live concurrency cap (default: unlimited).",
            },
        },
        "required": [],
    },
}

MASTER_DISPATCH_SCHEMA = {
    "name": "plobi_master_dispatch",
    "description": (
        "Run one dispatcher tick: reclaim stale workers, promote "
        "parent-free todos to ready, spawn ready+assigned tasks. "
        "Pass dry_run=true to preview without spawning."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "board": _BOARD_PROP,
            "max": {
                "type": "integer",
                "description": "Live concurrency cap (default: unlimited).",
            },
            "dry_run": {
                "type": "boolean",
                "description": "Preview only; no claims, no spawns.",
            },
        },
        "required": [],
    },
}

MASTER_APPROVE_SCHEMA = {
    "name": "plobi_master_approve",
    "description": (
        "Approve a task into the dispatchable queue: triage → todo → ready "
        "(via specify_triage_task + promote_task, the same chain the kanban "
        "CLI uses). Refuses tasks already running/done."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "Task id to approve."},
            "reason": {"type": "string", "description": "Optional audit reason."},
            "assignee": {
                "type": "string",
                "description": "Optional profile to assign while specifying a triage task.",
            },
            "force": {
                "type": "boolean",
                "description": "Override unsatisfied parent dependencies on promote.",
            },
            "board": _BOARD_PROP,
        },
        "required": ["task_id"],
    },
}

def handle_master_status(args: dict, **kwargs) -> str:
    kb, conn = _connect(board=_board_slug(args))
    try:
        stats = kb.board_stats(conn)
        tasks = kb.list_tasks(conn)
        awaiting = sorted(
            (t for t in tasks if t.status in ("triage", "blocked", "review")),
            key=lambda t: t.created_at or 0,
        )
        ready = sorted(
            (t for t in tasks if t.status == "ready"),
            key=lambda t: (-(t.priority or 0), t.created_at or 0),
        )
        return json.dumps(
            {
                "ok": True,
                "board": _board_name(_board_slug(args)),
                "by_status": stats["by_status"],
                "oldest_ready_age_seconds": stats["oldest_ready_age_seconds"],
                "awaiting_human": [_task_brief(t) for t in awaiting],
                "ready": [_task_brief(t) for t in ready],
            },
            ensure_ascii=False,
        )
    finally:
        conn.close()


def _dispatch_payload(args: dict, *, dry_run: bool) -> str:
    board = _board_slug(args)
    kb, conn = _connect(board=board)
    try:
        max_spawn = args.get("max")
        if max_spawn is not None:
            try:
                max_spawn = int(max_spawn)
            except (TypeError, ValueError):
                return tool_error("max must be an integer")
        result = kb.dispatch_once(
            conn,
            dry_run=dry_run,
            max_spawn=max_spawn,
            board=board,
        )
        return json.dumps({"ok": True, "dry_run": dry_run, **asdict(result)})
    finally:
        conn.close()


def handle_master_preview(args: dict, **kwargs) -> str:
    return _dispatch_payload(args, dry_run=True)


def handle_master_dispatch(args: dict, **kwargs) -> str:
    dry_run, err = _parse_bool_arg(args, "dry_run", default=False)
    if err:
        return tool_error(err)
    return _dispatch_payload(args, dry_run=dry_run)


def handle_master_approve(args: dict, **kwargs) -> str:
    tid = str(args.get("task_id") or "").strip()
    if not tid:
        return tool_error("task_id is required", ok=False)
    reason = args.get("reason")
    force, ferr = _parse_bool_arg(args, "force", default=False)
    if ferr:
        return tool_error(ferr, ok=False)
    assignee = _normalize_profile(args.get("assignee"))

    kb, conn = _connect(board=_board_slug(args))
    try:
        task = kb.get_task(conn, tid)
        if task is None:
            return tool_error(f"task {tid} not found", ok=False)
        before = task.status

        # triage → todo (no LLM needed; plain specify with optional assignee).
        if before == "triage":
            kb.specify_triage_task(
                conn, tid, assignee=assignee, author="plobi-master"
            )
            task = kb.get_task(conn, tid)

        # todo/blocked → ready (parent gate; force overrides).
        if task.status in ("todo", "blocked"):
            ok, why = kb.promote_task(
                conn, tid, actor="plobi-master", reason=reason, force=force
            )
            if not ok:
                return tool_error(why or f"promote refused for {tid}", ok=False)
            after = kb.get_task(conn, tid).status
        elif task.status == "ready":
            # ``specify_triage_task`` lands the task on ``todo``; parent-free
            # tasks are immediately recomputed to ``ready`` inside the same
            # txn. Either way the approve goal (dispatchable) is reached.
            after = "ready"
        else:
            return tool_error(
                f"task {tid} is {task.status!r}; approve only applies to "
                "triage/todo/blocked",
                ok=False,
            )
        return json.dumps(
            {
                "ok": True,
                "task_id": tid,
                "status_before": before,
                "status_after": after,
                "reason": reason,
            },
            ensure_ascii=False,
        )
    finally:
        conn.close()


CHECKIN_RESPOND_SCHEMA = {
    "name": "plobi_checkin_respond",
    "description": (
        "配置提案落地入口（R3：确认 N 才生效，本工具绝不直接改配置）。"
        "用户明确说要改项目每周节奏、或「某段时间不要排会/"
        "项目块」（avoid 窗）时调用——把指令变成一张配置提案卡。"
        "pace_updates 改 l2_project 的每周节奏；"
        "avoid_windows 列窗 11:00–13:00 / 18:00–20:00 同日窗，确认后规划器把"
        "该窗当占用（**仅项目推进块**——已确认课表 / 已有日程不受影响）。"
        "用户只是闲聊、或没说清改什么 → 不要调用本工具，先用追问澄清。"
        "自由文本原话用 free_text 存档。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "pace_updates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "project_id": {
                            "type": "string",
                            "description": "l2_project 项目名",
                        },
                        "weekly_hours": {
                            "type": "number",
                            "description": "每周目标小时数，(0, 168]",
                        },
                    },
                    "required": ["project_id", "weekly_hours"],
                },
                "description": "项目节奏修改列表；无则不传",
            },
            "avoid_windows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "start_time": {
                            "type": "string",
                            "description": "HH:MM；同日窗起点；省略拒绝（要么全有要么全无）",
                        },
                        "end_time": {
                            "type": "string",
                            "description": "HH:MM；同日窗终点，必须晚于 start_time",
                        },
                    },
                    "required": ["start_time", "end_time"],
                },
                "description": (
                    "avoid 窗修改列表；每条【start_time, end_time】都是同日窗。"
                    "确认后规划器把该窗当占用、**仅阻止项目推进块落入**——已确认"
                    "事件不受影响（用户的课程行不会因此被删）。"
                    "无则不传。"
                ),
            },
            "free_text": {
                "type": "string",
                "description": (
                    "用户自由文本原话（可选）。原样追加存档到 Mind 当日 digest，"
                    "不做解析、不产生提案。"
                ),
            },
        },
    },
}


def handle_checkin_respond(args: dict, **kwargs) -> str:
    """C6b: 用户明确指令 → 配置提案卡。**绝不直接改配置**（R3 分级）。

    只消化回访会话里用户的明确指令；校验与建卡全部复用
    ``plobi.agenda.checkin.propose_config_change``，落地走
    ``AgendaService.confirm_card``（确认 N 既有链路），本工具不碰。
    """
    from datetime import datetime

    from plobi.agenda import checkin, store

    pace_updates = args.get("pace_updates")
    avoid_windows = args.get("avoid_windows")
    free_text = str(args.get("free_text") or "").strip()
    if pace_updates is not None and not isinstance(pace_updates, list):
        return tool_error("pace_updates must be a list", ok=False)
    if avoid_windows is not None and not isinstance(avoid_windows, list):
        return tool_error("avoid_windows must be a list", ok=False)
    if not pace_updates and not avoid_windows and not free_text:
        return tool_error(
            "nothing to do: pass pace_updates / avoid_windows / free_text",
            ok=False,
        )

    response: dict[str, Any] = {"ok": True}
    if free_text:
        try:
            response["archived"] = checkin.archive_free_text(
                free_text, day=datetime.now(), source="desktop"
            )
        except Exception as exc:  # 存档失败不挡提案
            response["archived"] = False
            response["archive_error"] = str(exc)

    if pace_updates or avoid_windows:
        try:
            conn = store.connect(None)
            try:
                card = checkin.propose_config_change(
                    conn,
                    pace_updates=pace_updates,
                    avoid_windows=avoid_windows,
                )
            finally:
                conn.close()
        except store.AgendaValidationError as exc:
            return tool_error(str(exc), ok=False)
        except Exception as exc:  # 任何异常都不许打断 L1 会话
            logger.warning("plobi_checkin_respond failed: %s", exc)
            return tool_error(f"checkin_respond failed: {exc}", ok=False)
        response.update(
            {
                "card_id": card.id,
                "confirm_seq": card.confirm_seq,
                "questions": card.questions,
                "note": "提案卡已创建；用户回复 确认N 后才落地（R3：改配置须人批）",
            }
        )
    return json.dumps(response, ensure_ascii=False)
