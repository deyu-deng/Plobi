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
# WP-L1-EVERY-TURN — L1 mouth gate (33.1 / 33.4)
# ---------------------------------------------------------------------------
#
# Background: 33.1 + 33.4 裁定把这周 L1 的"嘴"收成只剩
# ``plobi_secretary_ask`` 与 ``plobi_checkin_respond``。其余 4 个 Master
# 工具（status / preview / dispatch / approve）+ 深工具 ``plobi`` 在 L1
# default profile 上**不可见**——把它们的 ``check_fn`` 切到下面这个恒 False
# 的函数，模型就分不到这几张嘴。``SECRETARY_ASK_*`` / intent 表 / handler
# / schema 全部保留（M3 才开；handler 保留是任务书 §3 的硬要求）。
# 详见 ``plobi/mind/writer.py`` 禁碰清单 + 任务书 WP-L1-EVERY-TURN。

def check_plobi_l1_mouth_disabled() -> bool:
    """恒 False — L1 default profile 的嘴门（M3 才开；handler 保留）。

    裁定 **33.1 + 33.4**：本周 L1 总秘书的"嘴"收成只剩
    ``plobi_secretary_ask`` + ``plobi_checkin_respond`` 两张。其余 4 个
    Master 工具 + 深工具 ``plobi`` 在 L1 default profile 上**不可见**——
    切到下面这个恒 False 的 ``check_fn`` 即可，模型就分不到。

    用作以下 5 个工具的 ``check_fn``：

    * ``plobi_master_status`` / ``plobi_master_preview`` /
      ``plobi_master_dispatch`` / ``plobi_master_approve``（4 个 Master 窄工具）
    * ``plobi``（深工具）

    配套保留的（仍用 ``check_plobi_master_mode``）：

    * ``plobi_secretary_ask``
    * ``plobi_checkin_respond``

    本函数**永远返回 False**——WP-L1-EVERY-TURN 之前它会返回 True，模型
    会分心去试 Master 工具。切到 False 之后，``tools/registry.py`` 的
    ``check_fn`` 流程直接把工具对 L1 隐藏，模型分不到。

    为什么不直接 unregister？任务书 §3 明确：「**不删** handler / schema
    / SECRETARY_ASK_* / intent 表」——M3 切回 True 只需要把 ``check_fn``
    改回 ``check_plobi_master_mode`` 即可（注册点见
    ``plugins/plobi-north-star/__init__.py::register``）。
    """
    return False


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

SECRETARY_ASK_SCHEMA = {
    "name": "plobi_secretary_ask",
    "description": (
        "你是本机 Plobi 总秘书（L1）——不是 Nous / MiniMax / 日程 L2 / 通用 chatbot "
        "/ Cursor / Claude Code。提示词里的 Nous / MiniMax 只是通道，不是身份，"
        "用户纠正过多次。问你「你是谁」时：你是听他说话、统筹派工的总秘书，"
        "活派给下面项目助手；不要开工具菜单、不要列七个 intent 让用户挑（A/B/C）、"
        "不要列 MCP/skills 让用户手动选。你没有 terminal 不是故障——"
        "WP-L2-DIET（裁定 33.3）已剥掉；不要解释框架、不要让用户改配置。"
        "改代码 / 跑命令 / 操控某 AI 软件：派给对应项目 L2，"
        "L2 再管 L3。第一动作仍是 plobi_secretary_ask，七个 intent 一个不增。"
        "用户问明天安排/日常日程 → intent=refresh_agenda；"
        "根据明天日程写早报 → intent=write_briefing；"
        "用户让你加/改/取消一条日程 → intent=mutate_agenda（带上 action 和钟点，"
        "缺钟点先问用户，不要编 9:00，不要默认 1 小时；"
        "「这期完了 / 这门课后面都不用去了」 → action=cancel_matching，"
        "禁止用循环 delete 冒充，工具一次性批量清）；"
        "问今天/明天/本周/某天/待确认有什么 → intent=query_agenda"
        "（只读日程库，不打采集，采集不通也能答）；"
        "用户口头确认或忽略某条待确认 → intent=decide_pending（等价看板点确认/忽略；"
        "用户贴一段群消息当证据，title 允许空，用 user_text 全文匹配证据里的 "
        "snippet / 标题 / 地点；0 条/多条不猜，回 candidates 让用户挑）；"
        "问各项目怎么样了 / 在推什么项目 / 给每个项目配 L2 → intent=project_status"
        "（读 Mind 项目子树，不打采集，不 spawn 日程 L2，"
        "weekly_hours 未设就如实说未设，不准发明小时数）；"
        "「吃饭睡觉还没安排 / 排一下明天 / 按真实项目再排一遍」→ intent=plan_day"
        "（调规划器，禁止用 mutate_agenda 一条条把午饭/晚饭/睡眠写进 events）。"
        "用户说「别中午排会 / 午饭往后挪 / 别把会排在午饭」→ 调 plobi_checkin_respond"
        "（不是 mutate_agenda）。query_agenda 用 range=today/tomorrow/date 时，"
        "响应会带 anchors（作息锚点：早饭/午饭/晚饭/睡眠，title+start_at+end_at+flex）"
        "+ plan_items（当日 daily_plan 的项）；这些字段与右栏时间轴同源（"
        "plobi.agenda.planning.day_surface），嘴和轴钟点不会漂。range=week / "
        "range=pending 是「不是一天轴」——不带 anchors / plan_items，正常回 events / "
        "pending 即可。"
        "不要用 session_search 搜会话，不要开 terminal 跑命令，不要 clarify 空转。"
        "工具内部按名单选日程 L2（缺则 spawn 一个模板），不要写死 agent id。"
        "采集不通时如实报错，不要编造日程；mutate_agenda 写入不受采集状态影响；"
        "project_status / plan_day 不打 chatlog、不 spawn 日程 L2。"
        "不要用 kanban dispatch 代替本工具。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "intent": {
                "type": "string",
                "enum": [
                    "refresh_agenda",
                    "write_briefing",
                    "mutate_agenda",
                    "query_agenda",
                    "decide_pending",
                    "project_status",
                    "plan_day",
                ],
                "description": (
                    "refresh_agenda = 明天的日常安排（一轮采集刷新 + 看板摘要）；"
                    "write_briefing = 根据明天的日程写早报（同样先刷新）；"
                    "mutate_agenda = 用户口头的加/改/取消/批量取消日程（直接落库，不碰采集）；"
                    "query_agenda = 查今天/明天/本周/某天/待确认（只读库，不采集；"
                    "range=today/tomorrow/date 时响应带 anchors 作息锚点 + plan_items "
                    "当日 daily_plan，week/pending 不带）；"
                    "decide_pending = 用户口头确认/忽略一条待确认（title 允许空，"
                    "用 user_text 全文匹配证据里 snippet / 标题 / 地点；"
                    "0 条/多条不猜，回 candidates 让用户挑）；"
                    "project_status = 列 Mind 在推的所有项目（l2_project，只读 Mind，"
                    "如「给每个项目配 L2」也走这条）；"
                    "plan_day = 排明天（或某天）的计划（调规划器，不要 mutate_agenda 补作息）。"
                ),
            },
            "user_text": {
                "type": "string",
                "description": (
                    "用户原话。decide_pending 时若 title/event_id 都空，"
                    "本字段全文当针，匹配证据里的 snippet / 标题 / 地点（@所有人/空白先清掉）。"
                    "原样交给日程 L2，不要改写或省略。"
                ),
            },
            "action": {
                "type": "string",
                "enum": ["create", "update", "delete", "cancel_matching"],
                "description": (
                    "mutate_agenda 必填：create=新增，update=修改，delete=取消单条，"
                    "cancel_matching=批量取消「这期完了/这门课后面都不用去了」"
                    "（一次性删，禁止循环 delete；带 source=timetable + start_at=起算日）。"
                ),
            },
            "title": {
                "type": "string",
                "description": (
                    "mutate_agenda：create 必填；update/delete 用标题关键词"
                    "定位那条日程（要改标题时也填新标题）；"
                    "cancel_matching：可空，标题子串过滤。"
                    "decide_pending：可空，title 与 event_id 都空时用 user_text 当针。"
                ),
            },
            "start_at": {
                "type": "string",
                "description": (
                    "mutate_agenda：create 必填，本地 ISO（如 2026-09-12T15:00:00）；"
                    "update 改钟点时给；不确定就先问用户，禁止编 9:00。"
                    "cancel_matching：YYYY-MM-DD，「从哪天起」算起，缺省=今天本地，"
                    "过去的不动。"
                ),
            },
            "end_at": {
                "type": "string",
                "description": "mutate_agenda 可空：结束时间，不确定就 null，禁止默认 1 小时。",
            },
            "kind": {
                "type": "string",
                "enum": ["meeting", "task", "ddl", "class"],
                "description": "mutate_agenda 可空，默认 task。",
            },
            "event_id": {
                "type": "string",
                "description": (
                    "mutate_agenda：update/delete 已知事件 id 时优先用它。"
                    "decide_pending：已知待确认项 id 时优先用它；"
                    "title + user_text 都空才报缺针。"
                ),
            },
            "source": {
                "type": "string",
                "description": (
                    "mutate_agenda：cancel_matching 必填，本刀只支持 source=timetable；"
                    "其它 source 回错、零删除。"
                ),
            },
            "range": {
                "type": "string",
                "enum": ["today", "tomorrow", "week", "date", "pending"],
                "description": (
                    "query_agenda 必填：today=今天，tomorrow=明天，"
                    "week=今天起 7 天，date=某一天（配 date），pending=全部待确认。"
                ),
            },
            "date": {
                "type": "string",
                "description": (
                    "query_agenda：range=date 必填，YYYY-MM-DD（不要自己换算成星期）。"
                    "decide_pending：可空，限定在某一天找那条待确认。"
                    "plan_day：可空，YYYY-MM-DD；缺省=明天（本地）。"
                ),
            },
            "decision": {
                "type": "string",
                "enum": ["confirm", "dismiss"],
                "description": (
                    "decide_pending 必填：confirm=确认（落在日程里），"
                    "dismiss=忽略（改动的回滚原值，新建的直接删掉）。"
                ),
            },
        },
        "required": ["intent", "user_text"],
    },
}


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


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


def handle_secretary_ask(args: dict, **kwargs) -> str:
    """§8.2: route 「明天安排」/「写早报」/「加改删日程」/「查日程」/「确认待确认」.

    Not kanban. Read + decide intents answer from the shared agenda store.
    """
    from plobi.agents.registry import run_secretary_ask

    result = run_secretary_ask(
        str(args.get("intent") or ""),
        str(args.get("user_text") or ""),
        action=args.get("action"),
        title=args.get("title"),
        start_at=args.get("start_at"),
        end_at=args.get("end_at"),
        kind=args.get("kind"),
        event_id=args.get("event_id"),
        range=args.get("range"),
        date=args.get("date"),
        decision=args.get("decision"),
    )
    if not result.get("ok"):
        extra = {key: value for key, value in result.items() if key != "error"}
        extra.setdefault("ok", False)
        return tool_error(result.get("error") or "secretary_ask failed", **extra)
    return json.dumps(result, ensure_ascii=False)


# ---------------------------------------------------------------------------
# C6b: daily check-in respond — config proposal card (R3: human-approved)
# ---------------------------------------------------------------------------


CHECKIN_RESPOND_SCHEMA = {
    "name": "plobi_checkin_respond",
    "description": (
        "配置提案落地入口（R3：确认 N 才生效，本工具绝不直接改配置）。"
        "用户明确说要改作息模板时间、改项目每周节奏、或「某段时间不要排会/"
        "项目块」（avoid 窗）时调用——把指令变成一张配置提案卡。routine_updates"
        " 改作息模板 start/end_time；pace_updates 改 l2_project 的每周节奏；"
        "avoid_windows 列窗 11:00–13:00 / 18:00–20:00 同日窗，确认后规划器把"
        "该窗当占用（**仅项目推进块**——已确认课表 / 作息锚点 / 弹性餐不受影响）。"
        "用户只是闲聊、或没说清改什么 → 不要调用本工具，先用追问澄清。"
        "自由文本原话用 free_text 存档。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "routine_updates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "template_id": {
                            "type": "string",
                            "description": "作息模板 id（如 seed-sleep）",
                        },
                        "start_time": {
                            "type": "string",
                            "description": "HH:MM；省略表示不变",
                        },
                        "end_time": {
                            "type": "string",
                            "description": "HH:MM；省略表示不变",
                        },
                    },
                    "required": ["template_id"],
                },
                "description": "作息模板修改列表；无则不传",
            },
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
                    "事件 / 作息锚点 / 弹性餐不受影响（用户的课程行不会因此被删）。"
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

    routine_updates = args.get("routine_updates")
    pace_updates = args.get("pace_updates")
    avoid_windows = args.get("avoid_windows")
    free_text = str(args.get("free_text") or "").strip()
    if routine_updates is not None and not isinstance(routine_updates, list):
        return tool_error("routine_updates must be a list", ok=False)
    if pace_updates is not None and not isinstance(pace_updates, list):
        return tool_error("pace_updates must be a list", ok=False)
    if avoid_windows is not None and not isinstance(avoid_windows, list):
        return tool_error("avoid_windows must be a list", ok=False)
    if not routine_updates and not pace_updates and not avoid_windows and not free_text:
        return tool_error(
            "nothing to do: pass routine_updates / pace_updates / avoid_windows / free_text",
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

    if routine_updates or pace_updates or avoid_windows:
        try:
            conn = store.connect(None)
            try:
                card = checkin.propose_config_change(
                    conn,
                    routine_updates=routine_updates,
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
