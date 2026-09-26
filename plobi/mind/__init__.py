"""Mind vault access — knowledge and profile, not runtime state.

Split by concurrency, not by taste:

- **reads** are direct and unrestricted; every L2 agent may call them
- **writes** funnel through :class:`MindWriter`, one at a time, with no model
  in the path (docs/adr/0011-three-tier-agents-and-model-routing.md)

Runtime state (agenda, DDLs) lives in SQLite instead — see ADR-0007.
"""

from .paths import (
    AI_WRITE_ROOT,
    SAFE_PREFIXES,
    MindUnavailable,
    UnsafeMindPath,
    is_safe_relative,
    resolve_root,
)
from .reader import MindContext, MindReader, ProjectBrief, get_reader, set_reader
from .writer import MindWriter, WriteRequest, WriteResult, get_writer, set_writer

__all__ = [
    "AI_WRITE_ROOT",
    "MindContext",
    "MindReader",
    "MindUnavailable",
    "MindWriter",
    "ProjectBrief",
    "SAFE_PREFIXES",
    "UnsafeMindPath",
    "WriteRequest",
    "WriteResult",
    "get_reader",
    "get_writer",
    "is_safe_relative",
    "resolve_root",
    "set_reader",
    "set_writer",
]
