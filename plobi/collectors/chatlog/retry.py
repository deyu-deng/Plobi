# MediaRetryTracker - retry logic with backoff for absent WeChat media

"""Track and manage retry attempts for media that couldn't be retrieved.

Design notes:
- WeChat checkpoint lag causes media to be "absent" from index even though it exists
- Without backoff, these absent items fill up the per-round quota and block new media
- This module implements exponential backoff with max retries to give up on unretrievable media
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# Max retry attempts before giving up on a media item
# Chosen to cover most checkpoint windows while preventing permanent queue blocking
MAX_RETRIES = 7

"""
This module implements a backoff strategy for media items that are marked as
"absent" (missing from the engine's checkpointed index). Instead of retrying
every round and filling up the quota, we track attempt counts and progressively
back off until we give up after N max retries.
"""
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


# Max retry attempts before giving up on a media item
# Chosen to cover most checkpoint windows while preventing permanent queue blocking
MAX_RETRIES = 7


@dataclass
class RetryRecord:
    """A single retry record for a media item."""

    external_id: str
    attempt_count: int
    last_attempt_at: datetime
    next_retry_at: Optional[datetime] = None

    @property
    def should_retry(self) -> bool:
        """Check if this record is due for another retry attempt."""
        if self.next_retry_at is None:
            return True
        return datetime.now() >= self.next_retry_at


class MediaRetryTracker:
    """Track retry attempts for absent media with exponential backoff.

    Implements:
    - Attempt counting per media item (external_id)
    - Exponential backoff: 1 round → 2 rounds → 4 rounds → ...
    - Max retries threshold (default 7) after which we give up
    - SQL storage in pool_items table's seen_attempts table
    """

    def __init__(self, db_path: Path):
        """Initialize tracker with database path.

        Args:
            db_path: Path to the pool database (agenda.db or similar)
        """
        self.db_path = db_path
        self._init_schema()

    def _init_schema(self) -> None:
        """Create seen_attempts table if not exists."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS seen_attempts (
                    source TEXT NOT NULL,
                    external_id TEXT NOT NULL,
                    attempt_count INTEGER DEFAULT 0,
                    last_attempt_at TEXT NOT NULL,
                    next_retry_at TEXT,
                    PRIMARY KEY (source, external_id)
                )
            """)
            conn.commit()
        finally:
            conn.close()

    def get_record(self, source: str, external_id: str) -> Optional[RetryRecord]:
        """Get existing retry record for a media item."""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                """
                SELECT source, external_id, attempt_count, last_attempt_at, next_retry_at
                FROM seen_attempts
                WHERE source = ? AND external_id = ?
                """,
                (source, external_id),
            )
            row = cursor.fetchone()
            if row is None:
                return None

            return RetryRecord(
                external_id=row[1],
                attempt_count=row[2],
                last_attempt_at=datetime.fromisoformat(row[3]),
                next_retry_at=(
                    datetime.fromisoformat(row[4]) if row[4] else None
                ),
            )
        finally:
            conn.close()

    def record_attempt(
        self,
        source: str,
        external_id: str,
        result: str,  # 'saved', 'absent', 'failed', 'malformed', 'dropped'
        now: Optional[datetime] = None,
    ) -> RetryRecord:
        """Record an attempt and return updated record.

        Args:
            source: Source identifier (e.g., 'wechat')
            external_id: Message ID or media ID
            result: One of 'saved', 'absent', 'failed', 'malformed', 'dropped'
            now: Current time (for testing)

        Returns:
            Updated RetryRecord with new attempt count and backoff schedule
        """
        if now is None:
            now = datetime.now()

        record = self.get_record(source, external_id)

        if result == 'saved':
            # Success: clear retry tracking
            self._delete_record(source, external_id)
            return RetryRecord(
                external_id=external_id,
                attempt_count=0,
                last_attempt_at=now,
            )

        if record is None:
            # First attempt
            record = RetryRecord(
                external_id=external_id,
                attempt_count=0,
                last_attempt_at=now,
            )

        # Increment attempt count
        record.attempt_count += 1
        record.last_attempt_at = now

        # Calculate backoff based on attempt count
        if result in ('absent', 'failed') and record.attempt_count < MAX_RETRIES:
            # Exponential backoff: 2^attempt_count rounds
            backoff_rounds = 2 ** record.attempt_count
            # Convert to time (assuming 1 round = 1 collection cycle, e.g., daily)
            # For simplicity, use hours here; actual implementation may vary
            backoff_hours = min(backoff_rounds * 4, 24 * 7)  # Cap at 7 days
            record.next_retry_at = now + timedelta(hours=backoff_hours)
        else:
            # Max retries exceeded or other results
            record.next_retry_at = None

        # Save/update record
        self._upsert_record(record)

        return record

    def should_process(self, source: str, external_id: str) -> bool:
        """Check if this media item should be processed this round.

        Returns False if:
        - No previous record (should always process)
        - Backoff period hasn't expired yet
        """
        record = self.get_record(source, external_id)

        if record is None:
            return True

        return record.should_retry

    def _upsert_record(self, record: RetryRecord) -> None:
        """Insert or update a retry record."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                INSERT INTO seen_attempts (source, external_id, attempt_count, last_attempt_at, next_retry_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source, external_id) DO UPDATE SET
                    attempt_count = excluded.attempt_count,
                    last_attempt_at = excluded.last_attempt_at,
                    next_retry_at = excluded.next_retry_at
                """,
                (
                    "wechat",  # hardcoded for now; could be passed in
                    record.external_id,
                    record.attempt_count,
                    record.last_attempt_at.isoformat(),
                    record.next_retry_at.isoformat() if record.next_retry_at else None,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _delete_record(self, source: str, external_id: str) -> None:
        """Delete a retry record (e.g., after successful retrieval)."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "DELETE FROM seen_attempts WHERE source = ? AND external_id = ?",
                (source, external_id),
            )
            conn.commit()
        finally:
            conn.close()

    def get_stats(self) -> dict:
        """Get statistics about current retry state."""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                """
                SELECT 
                    COUNT(*) as total_tracked,
                    SUM(CASE WHEN next_retry_at IS NULL THEN 1 ELSE 0 END) as max_retries_exceeded,
                    AVG(attempt_count) as avg_attempts
                FROM seen_attempts
                """
            )
            row = cursor.fetchone()
            return {
                "total_tracked": row[0],
                "max_retries_exceeded": row[1],
                "avg_attempts": row[2],
            }
        finally:
            conn.close()
