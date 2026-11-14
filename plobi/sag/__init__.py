"""SAG — SQL-Retrieval Augmented Generation, as a reusable core capability.

Not a plugin under ``plugins/memory/``. Mind is the first mounted source; a
future AI-readable knowledge base mounts as a second source through the same
``SagStore`` / ``index_tree`` / ``recall`` surface.

The SQLite database is a **derived** index. Markdown in the brain stays
canonical and writes still go through the L1 approval gate.
"""
from __future__ import annotations

from plobi.sag.indexer import build_from_mind, collect_pages, extract_page, index_tree
from plobi.sag.retrieval import RecallResult, query_terms, recall as recall_events
from plobi.sag.router import FAST, MULTI, choose_mode, default_db_path, recall
from plobi.sag.store import SagStore

__all__ = [
    "FAST", "MULTI", "RecallResult", "SagStore",
    "build_from_mind", "choose_mode", "collect_pages", "default_db_path",
    "extract_page", "index_tree", "query_terms", "recall", "recall_events",
]
