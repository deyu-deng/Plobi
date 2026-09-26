"""Agenda: runtime schedule state for the AI-secretary MVP (milestone M1).

Public surface — import from here, not from submodules:

- :class:`AgendaService` / :func:`get_service` — all business operations
- :class:`Event` — the record shape returned to callers
- error types for callers that need to branch

``store`` (SQLite), ``rules`` (local keyword filter) and ``router`` (HTTP) are
implementation details. Neither ``store`` nor ``rules`` may call a model.

Spec: docs/specs/agenda-board.md
"""

from .service import (
    AgendaError,
    AgendaService,
    ConfirmSeqExpired,
    EventNotFound,
    IngestResult,
    get_service,
)
from .store import AgendaValidationError, Event

__all__ = [
    "AgendaError",
    "AgendaService",
    "AgendaValidationError",
    "ConfirmSeqExpired",
    "Event",
    "EventNotFound",
    "IngestResult",
    "get_service",
]
