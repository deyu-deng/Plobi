"""fast / multi dispatch for the official brain.

``fast`` is the first knife's deterministic BM25 — it keeps the per-turn prefix
cache and the 6000-character budget intact. ``multi`` is SAG's query-time
hyperedge path, paid only when the question looks relational.

This module is the seam, not the wiring: the per-turn ``prefetch`` hook lives in
``plobi/mind/provider.py``, which another knife currently owns. Wiring is a
separate commit; ``plobi.sag.recall`` is callable and fully testable on its own.
"""
from __future__ import annotations

from pathlib import Path

from plobi.sag.retrieval import query_terms

FAST = "fast"
MULTI = "multi"

_RELATIONAL_HINTS = (
    "关系", "相关", "关联", "为什么", "哪些", "影响", "之后", "导致",
    "之间", "连带", "跨", "和", "与", "取决于", "追溯",
)


def choose_mode(query: str, *, explicit: str | None = None) -> str:
    """Deterministic, cheap heuristic. Never inspects the brain or calls a model."""
    if explicit in (FAST, MULTI):
        return explicit
    if any(hint in (query or "") for hint in _RELATIONAL_HINTS):
        return MULTI
    if len(query_terms(query)) >= 4:
        return MULTI
    return FAST


def default_db_path() -> Path:
    from plobi_constants import get_plobi_home

    return Path(get_plobi_home()) / "sag" / "mind.db"


def recall(mind_root: str | Path, query: str, *, mode: str | None = None,
           db_path: str | Path | None = None, limit: int = 5,
           hops: int = 1) -> dict:
    """One entry point for both tiers. Returns a plain, inspectable dict."""
    chosen = choose_mode(query, explicit=mode)
    if chosen == FAST:
        from plobi.mind import retrieval as mind_retrieval

        return {
            "mode": FAST,
            "blocks": mind_retrieval.search(mind_root, query, limit=limit),
            "trace": None,
        }

    from plobi.sag.indexer import build_from_mind
    from plobi.sag.retrieval import recall as sag_recall

    db = Path(db_path) if db_path is not None else default_db_path()
    store, report = build_from_mind(mind_root, db)
    try:
        result = sag_recall(store, query, limit=limit, hops=hops)
    finally:
        store.close()
    return {
        "mode": MULTI,
        "candidates": result.candidates,
        "trace": result.trace,
        "index": report,
    }
