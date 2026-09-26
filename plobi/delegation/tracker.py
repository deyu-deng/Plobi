"""Subagent lifecycle tracker — the data source behind the L2 left rail (B4).

Fed by the ``subagent_start`` / ``subagent_stop`` hooks that
``tools/delegate_tool.py`` already fires. The shape it emits is deliberately
pinned to ``AgentSubagent`` in ``apps/desktop/src/app/console/types.ts``
(``{id, name, status}``) — that file is the contract's source of truth, so §5
can serve this straight through without a translation layer.

Status vocabulary is the four states the left rail can render:
``idle | working | awaiting_approval | error``.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Mirrors AgentStatus in console/types.ts. Defined here as plain strings so the
# Python side fails loudly (KeyError/ValueError) rather than silently emitting
# a state the UI cannot render.
STATUS_IDLE = "idle"
STATUS_WORKING = "working"
STATUS_AWAITING_APPROVAL = "awaiting_approval"
STATUS_ERROR = "error"

AGENT_STATUSES = (
    STATUS_IDLE,
    STATUS_WORKING,
    STATUS_AWAITING_APPROVAL,
    STATUS_ERROR,
)

# child_status values that mean the child did not finish cleanly.
_FAILURE_TOKENS = ("error", "fail", "timeout", "abort", "cancel", "interrupt")


@dataclass
class SubagentRecord:
    child_id: str
    name: str
    status: str
    agent_id: str
    session_id: str
    goal: str = ""
    role: str = ""
    started_at: float = 0.0
    ended_at: Optional[float] = None
    detail: str = ""

    def as_dict(self) -> dict:
        """§5 shape — exactly the AgentSubagent fields, nothing more."""
        return {"id": self.child_id, "name": self.name, "status": self.status}


def _label(goal: str, child_id: str, role: str = "") -> str:
    """Short human label for the left rail.

    Goals arrive as free-form model text, so collapse whitespace and truncate;
    the UI has one line to work with. Fall back to role, then to the id.
    """
    text = " ".join((goal or "").split())
    if text:
        return text[:24] + ("…" if len(text) > 24 else "")
    if role:
        return role
    return child_id or "subagent"


class SubagentTracker:
    """Per-L2 view of L3 children. In-memory and process-local by design.

    Nothing here survives a restart, which is correct: a stale "working" child
    from a previous process would be a lie in the UI. Core's own durability
    rule says the same — background delegation is process-local.
    """

    def __init__(self, *, clock=time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._records: Dict[str, SubagentRecord] = {}
        self._bindings: Dict[str, str] = {}

    # -- session → L2 agent binding ------------------------------------

    def bind_session(self, session_id: str, agent_id: str) -> None:
        """Map a runtime session onto its resident L2 agent id (B2 registry).

        Hook payloads only carry ``parent_session_id``, while the workbench
        asks by agent id — this is the bridge. B2's spawn path (or §5) calls
        it once per resident agent.
        """
        if not session_id or not agent_id:
            return
        with self._lock:
            self._bindings[session_id] = agent_id

    def agent_for_session(self, session_id: str) -> str:
        with self._lock:
            return self._bindings.get(session_id, session_id)

    # -- lifecycle -----------------------------------------------------

    @staticmethod
    def _key(child_session_id: str, child_subagent_id: str) -> str:
        """``child_session_id`` is the only id present in BOTH hooks.

        ``subagent_start`` carries ``child_subagent_id`` too, but
        ``subagent_stop`` does not — so keying on the subagent id would leave
        every record stuck at ``working`` because the stop never matches.
        """
        return child_session_id or child_subagent_id or ""

    def _lookup(self, child_id: str, child_subagent_id: str = ""):
        record = self._records.get(child_id)
        if record is not None:
            return record
        if child_subagent_id and child_subagent_id != child_id:
            return self._records.get(child_subagent_id)
        return None

    def on_start(
        self,
        *,
        child_session_id: str = "",
        child_subagent_id: str = "",
        parent_session_id: str = "",
        child_role: str = "",
        child_goal: str = "",
        agent_id: str = "",
    ) -> SubagentRecord:
        child_id = self._key(child_session_id, child_subagent_id)
        owner = agent_id or self.agent_for_session(parent_session_id)
        record = SubagentRecord(
            child_id=child_id,
            name=_label(child_goal, child_id, child_role),
            status=STATUS_WORKING,
            agent_id=owner,
            session_id=parent_session_id,
            goal=child_goal or "",
            role=child_role or "",
            started_at=self._clock(),
        )
        with self._lock:
            self._records[child_id] = record
        return record

    def on_stop(
        self,
        *,
        child_session_id: str = "",
        child_subagent_id: str = "",
        child_status: str = "",
        child_summary: str = "",
        duration_ms: int = 0,
    ) -> Optional[SubagentRecord]:
        with self._lock:
            record = self._lookup(self._key(child_session_id, child_subagent_id), child_subagent_id)
            if record is None:
                return None
            record.ended_at = self._clock()
            record.status = self._resolve_status(child_status)
            record.detail = (child_summary or "")[:200]
        return record

    def mark_awaiting_approval(self, child_id: str) -> Optional[SubagentRecord]:
        """Reserved for the approval gate (nothing wires it yet).

        The four-state vocabulary includes ``awaiting_approval`` and the rail
        can render it, but the subagent hooks carry no approval signal — so
        this stays an explicit opt-in instead of a guess.
        """
        with self._lock:
            record = self._records.get(child_id)
            if record is None:
                return None
            record.status = STATUS_AWAITING_APPROVAL
            return record

    @staticmethod
    def _resolve_status(child_status: str) -> str:
        text = (child_status or "").strip().lower()
        if not text:
            return STATUS_IDLE
        if any(token in text for token in _FAILURE_TOKENS):
            return STATUS_ERROR
        return STATUS_IDLE

    # -- queries -------------------------------------------------------

    def subagents(self, agent_id: str) -> List[dict]:
        """§5 ``GET /api/agents/:id/subagents`` payload for one L2."""
        with self._lock:
            rows = [
                record
                for record in self._records.values()
                if record.agent_id == agent_id
            ]
        rows.sort(key=lambda record: record.started_at)
        return [record.as_dict() for record in rows]

    def record(self, child_id: str) -> Optional[SubagentRecord]:
        with self._lock:
            return self._records.get(child_id)

    def snapshot(self) -> Dict[str, List[dict]]:
        """Every L2's children, keyed by agent id — handy for debugging."""
        with self._lock:
            agents = {record.agent_id for record in self._records.values()}
        return {agent_id: self.subagents(agent_id) for agent_id in sorted(agents)}

    def clear_session(self, session_id: str) -> int:
        with self._lock:
            stale = [
                child_id
                for child_id, record in self._records.items()
                if record.session_id == session_id
            ]
            for child_id in stale:
                del self._records[child_id]
            self._bindings.pop(session_id, None)
            return len(stale)


_DEFAULT_TRACKER: Optional[SubagentTracker] = None
_DEFAULT_LOCK = threading.Lock()


def get_tracker() -> SubagentTracker:
    global _DEFAULT_TRACKER
    if _DEFAULT_TRACKER is None:
        with _DEFAULT_LOCK:
            if _DEFAULT_TRACKER is None:
                _DEFAULT_TRACKER = SubagentTracker()
    return _DEFAULT_TRACKER


def set_tracker(tracker: Optional[SubagentTracker]) -> None:
    global _DEFAULT_TRACKER
    _DEFAULT_TRACKER = tracker


def subagents_for_agent(agent_id: str) -> List[dict]:
    """Data source for §5 ``GET /api/agents/:id/subagents``.

    B4 owns this function but not the route — mounting it belongs to §5.
    """
    return get_tracker().subagents(agent_id)
