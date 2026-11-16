"""人对一次记忆写入的表态事件（规格 ``specs/memory-consent-learning.md`` §3.1，P1）。

与 ``plobi/mind/proposal.py`` 的 ``TrustLedger`` **分开**，两者回答的不是一个问题：

- ``TrustLedger`` 存的是门槛的**当前值**（streak，会涨会归零，取最新一条状态）；
- 这一张存的是**发生过什么**（一次表态一条行，只追加、不改写）。

流水在，门槛随时能重算；门槛在，流水重不出来。所以两本都在，谁也不替代谁。

落点（用户 2026-11-16 21:4x 交互式拍定，规格 §8 第 4 条）：``PLOBI_CONSENT_DB`` 或
``get_plobi_state_home()/consent.db``——同一家、独立文件、独立 schema。这样删掉一份
就等于重置学习，不动票库与会话库。票面那条「决定必须和它评判的票同生共死」
（``plobi/delegation/decision_ledger.py``）在这里不适用：偏好与 policy 都是**可派生的
解释层**（规格 §6），删了只丢学习成果，不改写入闸的行为。

形状照 ``decision_ledger``：``Path`` 可注入（测试用临时库）、``record`` 一次一行、
``as_dict`` 给读口。**不在这里做任何判断**——该不该学、能不能静默，都不归账本管。
"""

from __future__ import annotations

import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

#: 三种表态。``edited`` 现在没有产出方（还没有「人改完再点头」的流程），
#: 但形状先钉住：将来那条路落地时不必再改表。
ACTIONS = ("adopted", "rejected", "edited")

DB_ENV = "PLOBI_CONSENT_DB"
DB_FILENAME = "consent.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS consent_events (
  id          TEXT PRIMARY KEY,
  ts          REAL NOT NULL,
  target      TEXT NOT NULL,
  category    TEXT NOT NULL,
  content_sha TEXT NOT NULL DEFAULT '',
  action      TEXT NOT NULL,
  comment     TEXT NOT NULL DEFAULT '',
  context_ref TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_consent_events_category
  ON consent_events(category, ts);
CREATE INDEX IF NOT EXISTS idx_consent_events_target
  ON consent_events(target, ts);
"""


@dataclass(frozen=True)
class Event:
    id: str
    ts: float
    target: str
    category: str
    content_sha: str
    action: str
    comment: str
    context_ref: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "ts": self.ts,
            "target": self.target,
            "category": self.category,
            "content_sha": self.content_sha,
            "action": self.action,
            "comment": self.comment,
            "context_ref": self.context_ref,
        }


def _row(row: sqlite3.Row) -> Event:
    return Event(
        id=row["id"],
        ts=float(row["ts"]),
        target=str(row["target"] or ""),
        category=str(row["category"] or "other"),
        content_sha=str(row["content_sha"] or ""),
        action=str(row["action"] or ""),
        comment=str(row["comment"] or ""),
        context_ref=str(row["context_ref"] or ""),
    )


class ConsentLedger:
    """一次表态一条行；只追加，不修改——人要改主意就**再写一条**。"""

    def __init__(self, path: Optional[Path | str] = None) -> None:
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

    @contextmanager
    def _session(self) -> Iterator[sqlite3.Connection]:
        """一次事务一个连接，用完就关。

        ``with sqlite3.Connection`` 只管**事务**、不管**句柄**：拿它当连接管理器会让
        文件一直开着，Windows 上连重命名这份库都会被拒（这条是测试先撞上的，不是理论洁癖）。
        """
        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    # -- 写 ---------------------------------------------------------------

    def record(
        self,
        target: str,
        category: str,
        action: str,
        *,
        content_sha: str = "",
        comment: str = "",
        context_ref: str = "",
        ts: Optional[float] = None,
    ) -> Event:
        act = str(action or "").strip().lower()
        if act not in ACTIONS:
            raise ValueError(f"unknown consent action: {action!r}")
        stamp = time.time() if ts is None else float(ts)
        event = Event(
            id="cev_" + uuid.uuid4().hex[:12],
            ts=stamp,
            target=str(target or "").strip(),
            category=str(category or "other").strip() or "other",
            content_sha=str(content_sha or "").strip(),
            action=act,
            # 原话不截断：规格 §3.1「不许截断丢弃」。上限只在写入口那边（1000 字符）。
            comment=str(comment or ""),
            context_ref=str(context_ref or "").strip(),
        )
        with self._session() as conn:
            conn.execute(
                "INSERT INTO consent_events"
                " (id, ts, target, category, content_sha, action, comment, context_ref)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (event.id, event.ts, event.target, event.category,
                 event.content_sha, event.action, event.comment, event.context_ref),
            )
        return event

    def record_plan(
        self,
        plan,
        action: str,
        *,
        pending_id: str = "",
        comment: str = "",
    ) -> List[Event]:
        """一张写脑计划 → 每个对象一条事件。

        一档一条（不是一个计划一条）：偏好要从**对象类别**上长出来，把三条写进
        「wiki」一格和把三条分别记清，对下游是两件事。``category`` 优先取计划里
        已经派生好的那份（``Plan.categories``，由 ``proposal.py`` 按路径算），
        缺了才现算——**不由模型猜**。
        """
        from plobi.mind.proposal import category_of

        context = f"pending:{pending_id}" if pending_id else ""
        out: List[Event] = []
        for change in getattr(plan, "changes", ()) or ():
            logical = str(getattr(change, "logical_path", "") or "")
            cat = str((getattr(plan, "categories", {}) or {}).get(logical) or category_of(logical))
            out.append(self.record(
                logical,
                cat,
                action,
                content_sha=str((getattr(plan, "new_shas", {}) or {}).get(logical) or ""),
                comment=comment,
                context_ref=context,
            ))
        return out

    # -- 读 ---------------------------------------------------------------

    def recent(
        self,
        limit: int = 50,
        *,
        category: str = "",
        action: str = "",
    ) -> List[Event]:
        sql = "SELECT * FROM consent_events"
        where, args = [], []
        if category:
            where.append("category = ?")
            args.append(str(category))
        if action:
            where.append("action = ?")
            args.append(str(action))
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY ts DESC, id DESC LIMIT ?"
        args.append(max(1, min(int(limit), 500)))
        try:
            with self._session() as conn:
                return [_row(r) for r in conn.execute(sql, args).fetchall()]
        except sqlite3.Error:
            return []

    def summary(self) -> Dict[str, Dict[str, Any]]:
        """按类计数：``{category: {"adopted": n, "rejected": n, "edited": n, "last_ts": t}}``。

        只给事实，不给「该不该静默」的结论——那是 P2 用这些计数去判的事。
        """
        out: Dict[str, Dict[str, Any]] = {}
        try:
            with self._session() as conn:
                rows = conn.execute(
                    "SELECT category, action, COUNT(*) AS n, MAX(ts) AS last_ts"
                    " FROM consent_events GROUP BY category, action"
                ).fetchall()
        except sqlite3.Error:
            return out
        for row in rows:
            bucket = out.setdefault(
                str(row["category"]), {"adopted": 0, "rejected": 0, "edited": 0, "last_ts": 0.0}
            )
            act = str(row["action"] or "")
            if act in ("adopted", "rejected", "edited"):
                bucket[act] = int(row["n"])
            bucket["last_ts"] = max(float(bucket.get("last_ts") or 0.0), float(row["last_ts"] or 0.0))
        return out


def comments_for(pending_id: str, limit: int = 50) -> List[str]:
    """这张纸上人写过的原话（含纸被删后仍留在独立篮里的那些）。

    评论是学习信号的原料（规格 §3.2）：事件落盘时把原话带上，将来抽偏好的人
    不用再回头找纸——纸早在点头那一刻被 ``discard_pending`` 删了。
    """
    from plobi.mind.pending_feedback import pending_feedback

    pid = str(pending_id or "")
    if not pid:
        return []
    out = [row["comment"] for row in pending_feedback() if row.get("pending_id") == pid and row.get("comment")]
    return out[-max(1, int(limit)):]
