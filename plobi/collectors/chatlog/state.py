"""Collector bookkeeping: the dedupe ledger and the talker status table.

The webhook and the 10-minute sweep both deliver the same message, so every
ingest path checks the dedupe ledger first. Kept in its own database: the
collector's bookkeeping is not agenda state.

The talker status table (ADR-0010, blacklist mode) is the admission ledger:

* ``known``    — reviewed, collected by default
* ``excluded`` — reviewed, never collected
* ``pending``  — fail-closed: a talker we have not reviewed is never
                 collected; it surfaces here for a one-click collect/exclude
                 on the board.

``review_done`` records whether the first-run review of existing sessions has
completed. Nothing is collected in blacklist mode until it is true.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator, Optional

# Long enough to cover a weekend of downtime, short enough to stay small.
RETENTION_DAYS = 30


def state_db_path() -> Path:
    override = os.environ.get("PLOBI_CHATLOG_STATE_DB", "").strip()
    if override:
        return Path(override)
    try:
        from plobi_constants import get_plobi_home

        root = get_plobi_home() / "plobi"
    except Exception:
        root = Path.home() / ".plobi" / "plobi"
    return root / "chatlog_state.db"


class SeenStore:
    def __init__(self, db_path: Path | str | None = None):
        self.path = Path(db_path) if db_path else state_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS seen (
                    msg_id  TEXT PRIMARY KEY,
                    seen_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_seen_at ON seen(seen_at);
                """
            )

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def already_seen(self, msg_id: str) -> bool:
        with self._conn() as conn:
            row = conn.execute("SELECT 1 FROM seen WHERE msg_id = ?", (msg_id,)).fetchone()
        return row is not None

    def mark_seen(self, msg_id: str) -> bool:
        """Record the id. Returns False when it was already there (a duplicate)."""
        stamp = datetime.now().replace(microsecond=0).isoformat()
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO seen (msg_id, seen_at) VALUES (?, ?)", (msg_id, stamp)
            )
            conn.commit()
        return cur.rowcount > 0

    def prune(self, *, retention_days: int = RETENTION_DAYS) -> int:
        # Inclusive bound so `retention_days=0` means "clear everything" rather
        # than sparing rows written in the current second.
        cutoff = (datetime.now() - timedelta(days=retention_days)).replace(microsecond=0).isoformat()
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM seen WHERE seen_at <= ?", (cutoff,))
            conn.commit()
        return cur.rowcount


class TalkerStore:
    """Admission ledger for blacklist-mode collection (ADR-0010 revision).

    Same database as :class:`SeenStore`; a separate table so the two concerns
    (what we already saw vs. who we are allowed to see) stay independent.

    Fail-closed invariant: a talker whose status is not ``known`` is never
    collected. An unknown talker is recorded as ``pending`` — surfaced on the
    board, not ingested.
    """

    STATUSES = ("known", "excluded", "pending")
    REVIEW_DONE_KEY = "review_done"

    def __init__(self, db_path: Path | str | None = None):
        self.path = Path(db_path) if db_path else state_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS talkers (
                    talker        TEXT PRIMARY KEY,
                    status        TEXT NOT NULL
                                  CHECK (status IN ('known','excluded','pending')),
                    first_seen_at TEXT NOT NULL,
                    updated_at    TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS meta (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # --- status -------------------------------------------------------------

    def status_of(self, talker: str) -> Optional[str]:
        """Return one of known/excluded/pending, or ``None`` when unreviewed."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT status FROM talkers WHERE talker = ?", (talker,)
            ).fetchone()
        return row["status"] if row else None

    def set_status(self, talker: str, status: str) -> None:
        if status not in self.STATUSES:
            raise ValueError(f"invalid talker status: {status!r}")
        now = datetime.now().replace(microsecond=0).isoformat()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO talkers (talker, status, first_seen_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(talker) DO UPDATE SET
                    status = excluded.status,
                    updated_at = excluded.updated_at
                """,
                (talker, status, now, now),
            )
            conn.commit()

    def mark_known(self, talker: str) -> None:
        self.set_status(talker, "known")

    def mark_excluded(self, talker: str) -> None:
        self.set_status(talker, "excluded")

    def mark_pending(self, talker: str) -> None:
        self.set_status(talker, "pending")

    def by_status(self, status: str) -> set[str]:
        if status not in self.STATUSES:
            raise ValueError(f"invalid talker status: {status!r}")
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT talker FROM talkers WHERE status = ?", (status,)
            ).fetchall()
        return {row["talker"] for row in rows}

    def known(self) -> set[str]:
        return self.by_status("known")

    def excluded(self) -> set[str]:
        return self.by_status("excluded")

    def pending(self) -> set[str]:
        return self.by_status("pending")

    def list(self) -> list[dict]:
        """All rows, most recently updated first — for the board / review UI."""
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT talker, status, first_seen_at, updated_at
                FROM talkers
                ORDER BY updated_at DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    # --- first-run review gate ----------------------------------------------

    def set_review_done(self, done: bool) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO meta (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (self.REVIEW_DONE_KEY, "1" if done else "0"),
            )
            conn.commit()

    def review_done(self) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT value FROM meta WHERE key = ?", (self.REVIEW_DONE_KEY,)
            ).fetchone()
        return row is not None and row["value"] == "1"
