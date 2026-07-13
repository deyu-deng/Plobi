"""Retrieval over the Mind vault (FRAMEWORK STUB).

Intended real implementation:
  - Keyword/FTS-style grep across all ``.md`` files inside SAFE_PREFIXES
    (Vault/projects/vaelis, Loom/wiki/concepts, Vault/meta, ...).
  - Optional P2 upgrade: local ``sentence-transformers`` embeddings + a tiny
    vector index for semantic recall (offline-first, no external service).

No logic yet — raise NotImplementedError so accidental calls fail loudly
rather than silently returning nothing.
"""
from __future__ import annotations

from pathlib import Path
from typing import List


def search(mind_root: Path, query: str, limit: int = 5) -> List[str]:
    """STUB: return relevant Mind markdown snippets for ``query``.

    TODO(implement): scan SAFE_PREFIXES, score by keyword/semantic match,
    return top-``limit`` formatted snippets.
    """
    raise NotImplementedError("Mind retrieval not implemented yet (framework stub)")
