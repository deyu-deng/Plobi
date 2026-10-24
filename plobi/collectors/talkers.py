r"""按信源分键的**会话准入名册**（用户 10-24：「把微信和钉钉的会话采集都做成做到UI里面去」）。

这一档只给"自己没有名册的那一族"用。今天它是钉钉那一条；**微信不是**——微信那份
`TalkerStore`（`chatlog/state.py`）早就是它自己的真源，还带 `first_seen_at` / `review_done`
这些它自己才懂的列，把它搬过来既动别线的档、又换不掉任何东西，所以这一族只在自己的包里
加一只适配器去读那一份（见 `chatlog/__init__.py`）。**一条会话的准入状态只 live 在一个地方**
比"全场共用一张表"值钱（裁定 94 那条：一处真源，但一处真源可以是"每一族一处"）。

口径与微信同一条（裁定 50／100 一）：**只有 `excluded` 拒绝**。没定过的行不落表——
落一行 `pending` 只是把"用户还没看过"这件事写成一堆行，而那件事由"名单里有没有这一条"就能问出来。

落点：`$PLOBI_HOME/plobi/talkers.db`（与 chatlog 那份 state 库同一个根、同一族落点，
D 盘；测试用 `PLOBI_TALKERS_DB` 钉进 tmp）。
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

#: 闭集。`pending` 是**界面查表用的那一档**（"没定过"），它不落表——见模块头那句。
STATUSES = ("known", "excluded", "pending")


def talkers_db_path() -> Path:
    override = (os.environ.get("PLOBI_TALKERS_DB") or "").strip()
    if override:
        return Path(override)
    from plobi_constants import get_plobi_home

    return get_plobi_home() / "plobi" / "talkers.db"


class Ledger:
    """`(source, talker) → status`。每个方法自己开连接：这一族没有"进程内单例"这件事。"""

    def __init__(self, path: Optional[Path | str] = None) -> None:
        self.path = Path(path) if path else talkers_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS talker_admissions (
                    source     TEXT NOT NULL,
                    talker     TEXT NOT NULL,
                    status     TEXT NOT NULL CHECK (status IN ('known','excluded')),
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (source, talker)
                )
                """
            )

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        """开连接、**提交**、关掉。

        这里曾经漏过一次 `commit()`：默认隔离级别下 `INSERT` 会隐式开事务，
        只 `close()` 就等于回滚——写进去的排除名单**静默消失**，界面上看像是"按了没反应"，
        而 `pull()` 照旧采（因为读的是那张空表）。这一族里"写了读不到"是最难查的一种坏，
        所以它有一条专门的测试（`test_source_conversations_api.py`），不是靠人记得提交。
        """
        conn = sqlite3.connect(str(self.path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def status_of(self, source: str, talker: str) -> str:
        """`excluded` / `known` / `pending`。没定过就是 `pending`，而 `pending` **等于采**（裁定 100 一）。"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT status FROM talker_admissions WHERE source = ? AND talker = ?",
                (source, talker)).fetchone()
        return str(row["status"]) if row else "pending"

    def excluded(self, source: str) -> set[str]:
        """这一条被排除掉的那些会话号——**采集器只需要这一格**：默认采，那就只有排除名单要查。
        （把整张名册搬回采集器会变成第二处判"采不采"。）"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT talker FROM talker_admissions WHERE source = ? AND status = 'excluded'",
                (source,)).fetchall()
        return {str(row["talker"]) for row in rows}

    def counts(self, source: str) -> dict[str, int]:
        """界面上那一行汇总用的：各档多少扇。`decided` 是"用户真定过"的那几扇。"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT status, count(*) AS n FROM talker_admissions WHERE source = ? GROUP BY status",
                (source,)).fetchall()
        by = {str(row["status"]): int(row["n"]) for row in rows}
        return {"known": by.get("known", 0), "excluded": by.get("excluded", 0),
                "decided": sum(by.values())}

    def set_status(self, source: str, talker: str, status: str) -> str:
        """写一档决定。回到 `pending` = 把那行删掉（"没定过"不是一行状态，是一行都不该在）。"""
        if status not in STATUSES:
            raise ValueError(f"不认识的准入档位 {status!r}：{STATUSES}")
        talker = str(talker).strip()
        if not talker:
            raise ValueError("会话号不能是空串")
        with self._conn() as conn:
            if status == "pending":
                conn.execute("DELETE FROM talker_admissions WHERE source = ? AND talker = ?",
                             (source, talker))
            else:
                conn.execute(
                    "INSERT INTO talker_admissions (source, talker, status, updated_at)"
                    " VALUES (?, ?, ?, ?)"
                    " ON CONFLICT(source, talker) DO UPDATE SET status = excluded.status,"
                    " updated_at = excluded.updated_at",
                    (source, talker, status, datetime.now().isoformat(timespec="seconds")))
        return status
