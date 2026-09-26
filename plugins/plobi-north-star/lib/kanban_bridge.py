"""Optional mirror into Plobi kanban — do not reinvent the board.

North Star keeps a small risk/stage overlay for night autonomy and Master
summaries. Execution collaboration still belongs to ``plobi_cli.kanban_db``
when available.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .queue import TaskRecord


def mirror_enqueue_to_kanban(rec: "TaskRecord") -> Optional[str]:
    """Best-effort create a kanban card. Returns kanban task id or None."""
    try:
        from plobi_cli import kanban_db
    except Exception:
        return None
    try:
        conn = kanban_db.connect()
        try:
            body = (
                f"plobi_task_id: {rec.id}\n"
                f"risk: {rec.risk}\n"
                f"domain: {rec.domain}\n"
                f"stage: {rec.stage}\n\n"
                f"{rec.summary or ''}"
            )
            # Prefer triage for high-risk so humans see it before dispatch.
            triage = str(rec.risk).startswith("L2") or str(rec.risk).startswith("L3") or str(
                rec.risk
            ).startswith("L4")
            kid = kanban_db.create_task(
                conn,
                title=rec.goal[:200] or rec.id,
                body=body,
                created_by="plobi-north-star",
                workspace_kind="scratch",
                triage=triage,
                idempotency_key=f"plobi:{rec.id}",
            )
            return kid
        finally:
            conn.close()
    except Exception as exc:
        logger.debug("kanban mirror skipped: %s", exc)
        return None
