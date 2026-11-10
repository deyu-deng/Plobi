"""每一跳派工的**授权层**（Ticket / Mandate，WP-L3-TICKET-MANDATE）。

裁定 216：Ticket / Mandate 是**跳无关**的协议，不是「执行边界专用」。L1→L2
（两个 Router 之间）、L2→L3（Router→Worker）、外部事件→Router 都用同一张表，
靠 ``leg`` 区分；所以 ``record_router`` 与 ``record_trigger`` / ``record_offload``
是同一份协议的三个实例。判据是**这条边谁持有授权、谁接单**，不是节点在第几层。

这一层只回答一件事：*这一跳允许谁、以什么范围、干到什么时候*。它不是第五套任务账——
任务账（``kanban.db``）、会话账（``state.db`` 的 ``sessions``）、外包流水
（``offload_rounds.jsonl``）继续各当各的真源，本模块只用 ``refs_json`` 存关联键，
**不复制**它们的字段。

为什么独立一个 sqlite 文件而不是给 ``state.db`` 加表：``state.db`` 是全 Agent 共享
总账（``plobi_state.py:748-799``），此刻在 ``WP-SAY-ACTS`` 的在派族里；授权层落在
``get_plobi_state_home()`` 同一家的独立文件，既不碰共享 schema，又保留事务与索引
（JSONL 追加式做不到可撤销，见 ``offload.py::RoundLedger``）。

形状的来源：DeepSeek 2026-10-31《Super-agent 设计探索》里的 Ticket / Mandate——
创建时固化授权，后续消息、评论与引用文档**不能改变权限**。11-08 裁定只交付能力，
默认 ``observed``：不改变任何执行行为，§4 #35 拍板后才由调用方决定是否自动放行。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from types import MappingProxyType
from pathlib import Path
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

POLICY_VERSION = "2026-11-08.1"
DB_FILENAME = "delegation_tickets.db"
DB_ENV = "PLOBI_TICKET_DB"

OBSERVED = "observed"
ACTIVE = "active"
DONE = "done"
FAILED = "failed"
REVOKED = "revoked"
TERMINAL = frozenset({DONE, FAILED, REVOKED})
VALID_STATUSES = frozenset({OBSERVED, ACTIVE, DONE, FAILED, REVOKED})

#: Worker 汇报的六种结构化状态（裁定 216）。「是否在等人」由字段判断，
#: 不从自由文本里猜；progress/waiting/blocked 不改票的生命周期，
#: ready→done、failed→failed、dropped→revoked。
REPORT_STATES = frozenset({
    "progress", "waiting", "ready", "blocked", "failed", "dropped",
})
TERMINAL_REPORTS = frozenset({"ready", "failed", "dropped"})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    id             TEXT PRIMARY KEY,
    parent_agent   TEXT NOT NULL DEFAULT '',
    leg            TEXT NOT NULL DEFAULT '',
    goal           TEXT NOT NULL DEFAULT '',
    scope_json     TEXT NOT NULL DEFAULT '{}',
    policy_version TEXT NOT NULL DEFAULT '',
    triggered_by   TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'observed',
    created_at     REAL NOT NULL,
    updated_at     REAL NOT NULL,
    refs_json      TEXT NOT NULL DEFAULT '{}',
    revoke_reason  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tickets_parent ON tickets(parent_agent, created_at DESC);
CREATE TABLE IF NOT EXISTS ticket_reports (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id  TEXT NOT NULL,
    state      TEXT NOT NULL,
    note       TEXT NOT NULL DEFAULT '',
    reporter   TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ticket_reports_ticket
    ON ticket_reports(ticket_id, created_at DESC);
"""


@dataclass(frozen=True)
class Ticket:
    id: str
    parent_agent: str
    leg: str
    goal: str
    scope: dict[str, Any] = field(default_factory=dict)
    policy_version: str = POLICY_VERSION
    triggered_by: str = ""
    status: str = OBSERVED
    created_at: float = 0.0
    updated_at: float = 0.0
    refs: dict[str, Any] = field(default_factory=dict)
    revoke_reason: str = ""

    def __post_init__(self) -> None:
        # frozen dataclass 只挡属性赋值，挡不住 ``ticket.scope["..."] = ...``。
        # 授权范围必须连内存里的那份也改不动，否则「后续消息改不了权限」只是半句真话。
        object.__setattr__(self, "scope", MappingProxyType(dict(self.scope)))
        object.__setattr__(self, "refs", MappingProxyType(dict(self.refs)))

    @property
    def executable(self) -> bool:
        return self.status == ACTIVE

    def allows(self, capability: str) -> bool:
        """授权范围只认创建时固化的白名单；没写白名单 = 一项都没授权。"""
        allowed = self.scope.get("allowed_capabilities") or []
        return isinstance(allowed, list) and capability in allowed

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "parent_agent": self.parent_agent,
            "leg": self.leg,
            "goal": self.goal,
            "scope": dict(self.scope),
            "policy_version": self.policy_version,
            "triggered_by": self.triggered_by,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "refs": dict(self.refs),
            "revoke_reason": self.revoke_reason,
        }


def _loads(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _dumps(value: Any) -> str:
    return json.dumps(dict(value or {}), ensure_ascii=False, sort_keys=True)


def _row_to_ticket(row: sqlite3.Row) -> Ticket:
    return Ticket(
        id=row["id"],
        parent_agent=row["parent_agent"],
        leg=row["leg"],
        goal=row["goal"],
        scope=_loads(row["scope_json"]),
        policy_version=row["policy_version"],
        triggered_by=row["triggered_by"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        refs=_loads(row["refs_json"]),
        revoke_reason=row["revoke_reason"],
    )


class TicketLedger:
    """一次派工一条行；可查、可撤、可限权，重启后仍在。"""

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path is not None else None

    @property
    def path(self) -> Path:
        if self._path is not None:
            return self._path
        override = os.environ.get(DB_ENV, "").strip()
        if override:
            return Path(override)
        from plobi_constants import get_plobi_state_home

        return get_plobi_state_home() / DB_FILENAME

    def _connect(self) -> sqlite3.Connection:
        target = self.path
        target.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(target), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
        return conn

    # -- write ---------------------------------------------------------

    def open_ticket(
        self,
        *,
        parent_agent: str,
        leg: str,
        goal: str = "",
        scope: Optional[dict[str, Any]] = None,
        triggered_by: str = "",
        refs: Optional[dict[str, Any]] = None,
        status: str = OBSERVED,
        policy_version: str = POLICY_VERSION,
    ) -> Ticket:
        if status not in VALID_STATUSES:
            raise ValueError(f"unknown ticket status: {status!r}")
        now = time.time()
        ticket = Ticket(
            id="tkt_" + uuid.uuid4().hex[:12],
            parent_agent=str(parent_agent or ""),
            leg=str(leg or ""),
            goal=str(goal or "")[:500],
            scope=dict(scope or {}),
            policy_version=policy_version,
            triggered_by=str(triggered_by or ""),
            status=status,
            created_at=now,
            updated_at=now,
            refs=dict(refs or {}),
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO tickets (id, parent_agent, leg, goal, scope_json, "
                "policy_version, triggered_by, status, created_at, updated_at, "
                "refs_json, revoke_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '')",
                (
                    ticket.id, ticket.parent_agent, ticket.leg, ticket.goal,
                    _dumps(ticket.scope), ticket.policy_version, ticket.triggered_by,
                    ticket.status, ticket.created_at, ticket.updated_at,
                    _dumps(ticket.refs),
                ),
            )
        return ticket

    def _transition(self, ticket_id: str, status: str, *, only_from: Optional[Iterable[str]] = None,
                    reason: str = "", refs: Optional[dict[str, Any]] = None) -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
            if row is None:
                return False
            if only_from is not None and row["status"] not in set(only_from):
                return False
            merged = _loads(row["refs_json"])
            if refs:
                merged.update(refs)
            conn.execute(
                "UPDATE tickets SET status = ?, updated_at = ?, refs_json = ?, "
                "revoke_reason = ? WHERE id = ?",
                (
                    status, time.time(), _dumps(merged),
                    reason if status == REVOKED else row["revoke_reason"], ticket_id,
                ),
            )
            return True

    def activate(self, ticket_id: str) -> bool:
        """observed → active。只此一扇门能让票变得可执行。"""
        return self._transition(ticket_id, ACTIVE, only_from=(OBSERVED,))

    def finish(self, ticket_id: str, *, ok: bool = True, refs: Optional[dict[str, Any]] = None) -> bool:
        return self._transition(ticket_id, DONE if ok else FAILED, only_from=(ACTIVE,), refs=refs)

    def revoke(self, ticket_id: str, *, reason: str = "") -> bool:
        return self._transition(ticket_id, REVOKED, reason=str(reason or "")[:300])

    def record_offload(self, result: Any, *, brief: str = "") -> str:
        """外包腿的**追溯型**入账：一笔已发生的活落一条终态票。

        显式 ``result.ticket_id`` 优先（那时是预授权，本函数只补关联键与终态）；
        没带就新建一条，让 ``RoundLedger`` 从此每笔都有可查的授权记录，而不是
        等调用方改完才生效。refs 只放关联键，不复制 brief 全文或回包正文。
        """
        explicit = str(getattr(result, "ticket_id", "") or "").strip()
        refs = {
            "job": str(getattr(result, "job", "") or ""),
            "external_session": str(getattr(result, "external_session", "") or ""),
            "reply_to": str(getattr(result, "reply_to", "") or ""),
        }
        refs = {k: v for k, v in refs.items() if v}
        ok = not bool(getattr(result, "failure", ""))
        if explicit:
            self.finish(explicit, ok=ok, refs=refs)
            return explicit
        ticket = self.open_ticket(
            parent_agent=str(getattr(result, "agent", "") or ""),
            leg="offload",
            goal=str(brief or "")[:120],
            triggered_by="offload_ledger",
            refs=refs,
            status=DONE if ok else FAILED,
        )
        return ticket.id

    def record_trigger(
        self,
        *,
        route: str,
        event_type: str,
        prompt: str,
        delivery_id: str = "",
        reason: str = "",
        parent_agent: str = "l1",
    ) -> str:
        """外部事件只记「本来会接」（裁定 215 #35）。

        Gate 规则门（HMAC／限流／幂等／events 白名单）过了才到这里；默认
        ``observed``，**不起 Agent 轮、不预生成产物**。第二级语义判断由人放行后
        接手的域 Router 承担，webhook 进程不烧 token，也不假装自己判过。
        """
        refs = {
            "event_type": str(event_type or "")[:80],
            "delivery_id": str(delivery_id or "")[:120],
            "prompt": str(prompt or "")[:4000],
            "prompt_excerpt": str(prompt or "")[:120],
            "route": str(route or "")[:120],
            "reason": str(reason or "")[:120],
        }
        ticket = self.open_ticket(
            parent_agent=parent_agent,
            leg="trigger",
            goal=f"{route}/{event_type}"[:160],
            scope={"allowed_capabilities": [], "route": str(route or "")},
            triggered_by=f"webhook:{route}",
            refs={k: v for k, v in refs.items() if v},
            status=OBSERVED,
        )
        return ticket.id

    def record_router(
        self,
        *,
        sender: str,
        recipient: str,
        mode: str,
        goal: str = "",
        session_id: str = "",
        message_id: str = "",
        reply_to: str = "",
    ) -> Ticket:
        """Router→Router 那一跳的**派发前**预授权票（裁定 216）。

        与专家腿／外包腿同形：人或域 Router 主动调 `say`/`ask` = 已放行，
        直接 ``ACTIVE``；票只负责留痕、限权与可撤销。授权层读写失败由调用方
        旁路，绝不能挡住说话这条路。

        ``reply_to`` 从「引用单条消息」升级为 **Thread 续接**：拿它找回上一张
        票，继承同一个 ``thread_id``；若那张票已被撤销，新票同样开成 ``REVOKED``
        ——这就是 Mandate 的「后续消息不增加权限」在代码里的样子。
        """
        prior = self.find_by_ref("message_id", reply_to) if reply_to else None
        thread_id = str((prior.refs.get("thread_id") if prior else "") or "").strip()
        if not thread_id:
            thread_id = "thr_" + uuid.uuid4().hex[:12]
        refs = {
            "recipient": str(recipient or ""),
            "mode": str(mode or ""),
            "thread_id": thread_id,
            "session_id": str(session_id or ""),
            "message_id": str(message_id or ""),
            "reply_to": str(reply_to or ""),
        }
        if prior is not None:
            refs["inherited_from"] = prior.id
        ticket = self.open_ticket(
            parent_agent=str(sender or ""),
            leg="router",
            goal=str(goal or "")[:120],
            scope={
                "allowed_capabilities": ["say", "ask", "delegate"],
                "recipient": str(recipient or ""),
            },
            triggered_by=f"{mode}:router",
            refs={k: v for k, v in refs.items() if v},
            status=ACTIVE,
        )
        if prior is not None and prior.status == REVOKED:
            self.revoke(ticket.id, reason=f"thread {thread_id} was revoked")
            ticket = self.get(ticket.id) or ticket
        return ticket

    def report_state(
        self, ticket_id: str, state: str, *, note: str = "", reporter: str = ""
    ) -> bool:
        """结构化六态汇报；终态同时推进票的生命周期。"""
        state = str(state or "").strip().lower()
        if state not in REPORT_STATES:
            return False
        ticket = self.get(ticket_id)
        if ticket is None:
            return False
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO ticket_reports (ticket_id, state, note, reporter, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (ticket_id, state, str(note or "")[:500],
                 str(reporter or "")[:120], time.time()),
            )
        if state == "ready":
            self.finish(ticket_id, ok=True)
        elif state == "failed":
            self.finish(ticket_id, ok=False)
        elif state == "dropped":
            self.revoke(ticket_id, reason=str(note or "worker dropped the ticket")[:300])
        return True

    # -- read ----------------------------------------------------------

    def get(self, ticket_id: str) -> Optional[Ticket]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        return _row_to_ticket(row) if row is not None else None

    def list_tickets(
        self, *, status: str = "", parent_agent: str = "", leg: str = "",
        limit: int = 100,
    ) -> list[Ticket]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if parent_agent:
            clauses.append("parent_agent = ?")
            params.append(parent_agent)
        if leg:
            clauses.append("leg = ?")
            params.append(leg)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(max(1, int(limit)))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tickets" + where + " ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [_row_to_ticket(row) for row in rows]

    def find_by_ref(self, key: str, value: str) -> Optional[Ticket]:
        """按 ``refs`` 里的关联键找回最近那张票（Thread 续接用）。

        优先走 SQLite 的 ``json_extract``；某些精简构建没有 JSON1 时退化成
        内存里扫最近 500 张票——宁可慢一点，也不把整张表拉进上下文。
        """
        key = str(key or "").strip()
        value = str(value or "").strip()
        if not key or not value or not key.replace("_", "").isalnum():
            return None
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM tickets WHERE json_extract(refs_json, ?) = ? "
                    "ORDER BY created_at DESC LIMIT 1",
                    (f"$.{key}", value),
                ).fetchone()
            if row is not None:
                return _row_to_ticket(row)
        except sqlite3.Error:
            logger.debug("ticket json_extract unavailable, falling back", exc_info=True)
        for ticket in self.list_tickets(limit=500):
            if str(ticket.refs.get(key) or "") == value:
                return ticket
        return None

    def latest_report(self, ticket_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM ticket_reports WHERE ticket_id = ? "
                "ORDER BY created_at DESC, id DESC LIMIT 1",
                (ticket_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def list_reports(self, ticket_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM ticket_reports WHERE ticket_id = ? "
                "ORDER BY created_at ASC, id ASC LIMIT ?",
                (ticket_id, max(1, int(limit))),
            ).fetchall()
        return [dict(row) for row in rows]

    def is_executable(self, ticket_id: str) -> bool:
        ticket = self.get(ticket_id)
        return bool(ticket and ticket.executable)

    def check_scope(self, ticket_id: str, capability: str) -> bool:
        ticket = self.get(ticket_id)
        return bool(ticket and ticket.executable and ticket.allows(capability))
