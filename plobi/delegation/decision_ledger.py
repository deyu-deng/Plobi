"""人对一条汇报的表态账：接纳 / 拒绝 / 评论（裁定 226）。

与 ``ticket_reports`` **分开**：那张表是收件方 Agent 写的六态回执，这一张是
**人**写的决定。两者互不相改——把人的原话混进 Agent 回执，就等于让模型替
用户表态（裁定 199「不许模型自评」的另一半）。

落点跟授权票同一份 sqlite（``PLOBI_TICKET_DB`` 或 ``get_plobi_state_home()``），
只多一张表：决定必须和它评判的那张票同生共死，不另起一处真源。
"""

from __future__ import annotations

import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

#: 三种表态；``comment`` 带着用户的原话，是 lesson 的原料。
KINDS = ("accept", "reject", "comment")

DB_ENV = "PLOBI_TICKET_DB"
DB_FILENAME = "delegation_tickets.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ticket_decisions (
  id          TEXT PRIMARY KEY,
  ticket_id   TEXT NOT NULL,
  kind        TEXT NOT NULL,
  comment     TEXT NOT NULL DEFAULT '',
  source      TEXT NOT NULL DEFAULT 'desktop',
  created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ticket_decisions_ticket
  ON ticket_decisions(ticket_id, created_at);
"""


@dataclass(frozen=True)
class Decision:
    id: str
    ticket_id: str
    kind: str
    comment: str
    source: str
    created_at: float

    def as_dict(self) -> dict:
        return {
            "id": self.id, "ticket_id": self.ticket_id, "kind": self.kind,
            "comment": self.comment, "source": self.source, "created_at": self.created_at,
        }


def _row(row: sqlite3.Row) -> Decision:
    return Decision(
        id=row["id"], ticket_id=row["ticket_id"], kind=row["kind"],
        comment=row["comment"], source=row["source"], created_at=row["created_at"],
    )


class DecisionLedger:
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

    def record(
        self, ticket_id: str, kind: str, *, comment: str = "", source: str = "desktop"
    ) -> Decision:
        kind = str(kind or "").strip().lower()
        if kind not in KINDS:
            raise ValueError(f"unknown decision kind: {kind!r}")
        row = Decision(
            id="dec_" + uuid.uuid4().hex[:12],
            ticket_id=str(ticket_id or "").strip(),
            kind=kind,
            comment=str(comment or "")[:1000],
            source=str(source or "desktop")[:80],
            created_at=time.time(),
        )
        if not row.ticket_id:
            raise ValueError("ticket_id 不能空")
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO ticket_decisions (id, ticket_id, kind, comment, source, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (row.id, row.ticket_id, row.kind, row.comment, row.source, row.created_at),
            )
        return row

    def get(self, decision_id: str) -> Optional[Decision]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM ticket_decisions WHERE id = ?", (str(decision_id or ""),)
            ).fetchone()
        return _row(row) if row is not None else None

    def list_for_ticket(self, ticket_id: str, *, limit: int = 50) -> list[Decision]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM ticket_decisions WHERE ticket_id = ? "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (str(ticket_id or ""), max(1, int(limit))),
            ).fetchall()
        return [_row(r) for r in rows]

    def list_recent(self, *, limit: int = 50) -> list[Decision]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM ticket_decisions ORDER BY created_at DESC, id DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [_row(r) for r in rows]

    def counts(self, ticket_id: str) -> dict[str, int]:
        out = {kind: 0 for kind in KINDS}
        for decision in self.list_for_ticket(ticket_id, limit=500):
            if decision.kind in out:
                out[decision.kind] += 1
        return out
