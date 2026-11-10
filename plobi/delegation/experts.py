"""L2 派出去的**临时专家**：不进名册，但要看得见、点得开（WP-L3-EXPERTS）。

用户 10-28 的口径：「不算名册里的 Agent。但是 L2 调用的时候对应的专家会出现在左侧栏。
点开可以看详细工作日志。」

实现上不新造一套身份：`delegate_task` 每派一个子智能体，就会在**同一个状态库**里留一条
``source='subagent'`` 的子会话（``parent_session_id`` 指回发起它的那条会话）。所以：

- 名单 = 这个 Agent 名下会话的**子会话**（父子链），**只留带角色侧记的**（真专家），
  新的在前。没带 `expert` 的普通子智能体不进这一格——那是底栏子智能体面板的事
  （用户 11-04：两条腿分开）；
- 状态 = 进程内 ``list_active_subagents()`` 里还挂着的就是「正在干」，否则「干完了」；
- 工作日志 = 子会话自己的 messages（复用现成的 ``/api/sessions/:id/messages`` 路由）。

专家的身份只活在这一条记录里：没有名册行、没有长期家、不进 Agent 列表。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional, Sequence


def state_db_path() -> Path:
    from plobi_constants import get_plobi_state_home

    return get_plobi_state_home() / "state.db"


def _connect_readonly(path: Path) -> Optional[sqlite3.Connection]:
    if not path.is_file():
        return None
    try:
        return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error:
        return None


def _first_user_text(con: sqlite3.Connection, session_id: str) -> str:
    try:
        row = con.execute(
            "select content from messages where session_id = ? and role = 'user' "
            "and content is not null and content != '' order by rowid limit 1",
            (session_id,),
        ).fetchone()
    except sqlite3.Error:
        return ""
    return str(row[0]).strip() if row else ""


def _sidecar_of(session_id: str) -> dict[str, Any]:
    """这一趟派的是哪类专家（`delegate_task` 派完时写下的侧记）。"""
    try:
        from plobi.delegation.expert_roles import experts_dir
    except Exception:  # noqa: BLE001
        return {}
    try:
        payload = json.loads((experts_dir() / f"{session_id}.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _live_by_child_session() -> dict[str, dict[str, Any]]:
    try:
        from tools.delegate_tool import list_active_subagents
    except Exception:  # noqa: BLE001 — 名单读不到不能拖垮列表
        return {}
    live: dict[str, dict[str, Any]] = {}
    for record in list_active_subagents():
        child = str(record.get("child_session_id") or "").strip()
        if child:
            live[child] = record
    return live


def experts_for_agent(agent_name: str, *, limit: int = 50) -> dict[str, Any]:
    """这个 Agent 派出去过的临时专家（子会话），正在干的排最前。"""
    name = str(agent_name or "").strip()
    path = state_db_path()
    con = _connect_readonly(path)
    if con is None:
        return {"experts": [], "reason": f"读不到状态库（{path}）"}
    try:
        try:
            parent_ids = [
                str(row[0])
                for row in con.execute("select id from sessions where agent_id = ?", (name,))
            ]
        except sqlite3.Error:
            parent_ids = []
        if not parent_ids:
            return {"experts": [], "reason": f"名册里 {name} 还没有会话"}

        marks = ",".join("?" for _ in parent_ids)
        try:
            rows = con.execute(
                "select id, parent_session_id, started_at, ended_at, message_count, tool_call_count, model "
                f"from sessions where source = 'subagent' and parent_session_id in ({marks}) "
                "order by started_at desc limit ?",
                (*parent_ids, limit),
            ).fetchall()
        except sqlite3.Error:
            return {"experts": [], "reason": "状态库里没有会话表"}

        live = _live_by_child_session()
        # 派工票状态只读一次（独立 sqlite，不碰共享总账）：左栏那枚徽标要能
        # 看出这趟活是「在跑 / 已完成 / 已撤销」，而不是再发一轮请求。
        ticket_status: dict[str, str] = {}
        try:
            from plobi.delegation.tickets import TicketLedger

            ticket_status = {
                t.id: t.status for t in TicketLedger().list_tickets(limit=1000)
            }
        except Exception:
            pass
        experts: list[dict[str, Any]] = []
        for sid, parent, started, ended, messages, tools, model in rows:
            sid = str(sid)
            sidecar = _sidecar_of(sid)
            role = str(sidecar.get("role") or "").strip()
            # 只认**真专家**：带角色侧记的那些。没带 `expert` 的普通子智能体不进这一格
            # ——它们的工作日志归底栏那枚子智能体面板（用户 11-04：两条腿要分开）。
            if not role:
                continue
            record = live.get(sid) or {}
            ticket_id = str(sidecar.get("ticket_id") or "").strip()
            experts.append({
                "goal": str(sidecar.get("goal") or "").strip() or _first_user_text(con, sid),
                "id": sid,
                "messages": int(messages or 0),
                "model": str(record.get("model") or model or ""),
                "parent_session_id": str(parent or ""),
                "started_at": float(started or 0),
                "role": role,
                "status": "running" if record else "done",
                "ticket_id": ticket_id,
                "ticket_status": ticket_status.get(ticket_id, ""),
                "tool_calls": int(record.get("tool_count") or tools or 0),
            })
    finally:
        con.close()

    experts.sort(key=lambda item: (0 if item["status"] == "running" else 1, -item["started_at"]))

    return {"experts": experts, "live": sum(1 for item in experts if item["status"] == "running")}
