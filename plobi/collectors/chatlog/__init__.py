"""chatlog collector — WeChat messages into agenda candidates.

Public surface:

- :class:`ChatlogPipeline` — run a sweep or handle one pushed message
- :class:`CollectorConfig` — whitelist + endpoint configuration
- :class:`IngestReport` — what a sweep did

``client`` / ``state`` / ``timeparse`` / ``rules`` are internals. Nothing in
this package may send more than the matched snippet off-device
(docs/adr/0010-collection-whitelist-privacy-boundary.md).
"""

from .client import ChatlogUnavailable, ChatMessage
from .config import CollectorConfig
from .confirm import Candidate, Confirmer, ConfirmContext, HeuristicConfirmer, NullConfirmer
from .pipeline import ChatlogDead, ChatlogPipeline, IngestReport

__all__ = [
    "Candidate",
    "ChatMessage",
    "ChatlogDead",
    "ChatlogPipeline",
    "ChatlogUnavailable",
    "CollectorConfig",
    "Confirmer",
    "ConfirmContext",
    "HeuristicConfirmer",
    "IngestReport",
    "NullConfirmer",
]
