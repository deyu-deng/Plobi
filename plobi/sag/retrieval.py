"""Query-time dynamic hyperedges (the SAG ``multi`` path).

No global graph, no PageRank: a SQL join on shared entities instantiates only
the local relationships relevant to *this* query, then the structure dissolves.
Every step of ``q → seeds → bridges → candidates`` is recorded in ``trace`` so a
miss can be pinned to a specific link instead of showing up as a vague score.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+")
_STOPWORDS = frozenset({
    "the", "and", "for", "are", "was", "you", "your", "our", "with", "that",
    "this", "from", "have", "has", "not", "but", "all", "can", "what", "when",
    "where", "who", "how", "why", "its", "were", "will", "would", "should",
})


def query_terms(query: str) -> List[str]:
    out: List[str] = []
    for raw in _TOKEN_RE.findall(query or ""):
        term = raw.lower()
        if term.isascii():
            if len(term) >= 3 and term not in _STOPWORDS:
                out.append(term)
        else:
            out.append(term)
    return list(dict.fromkeys(out))


@dataclass
class RecallResult:
    candidates: List[dict]
    trace: Dict


def recall(store, query: str, *, limit: int = 5, hops: int = 1,
           frontier_limit: int = 50) -> RecallResult:
    """Return ranked pages (mapped back to their source paths) plus a trace."""
    terms = query_terms(query)
    if not terms:
        return RecallResult([], {"query_terms": [], "seeds": [], "hops": [],
                                 "expanded": [], "candidates": []})

    seeds = store.seed_sources(terms)
    expanded, hop_trace = store.expand(
        seeds, hops=hops, frontier_limit=frontier_limit
    )

    # Seeds rank first (direct evidence); each bridge hop adds a smaller,
    # distance-decayed amount. Ties break on source path → byte-identical output.
    scores: Dict[str, float] = {}
    for source in seeds:
        event = store.event(source) or {}
        haystack = f"{event.get('title', '')} {event.get('abstract', '')}".lower()
        scores[source] = 10.0 + sum(1 for t in terms if t in haystack)
    for hop in hop_trace:
        for source in hop["added"]:
            scores[source] = scores.get(source, 0.0) + 1.0 / hop["hop"]

    ordered = sorted(scores, key=lambda s: (-scores[s], s))[:limit]
    candidates = []
    for source in ordered:
        event = store.event(source) or {}
        candidates.append({
            "source": source,
            "title": event.get("title", ""),
            "abstract": event.get("abstract", ""),
            "score": scores[source],
            "via": "seed" if source in seeds else "hyperedge",
        })
    trace = {
        "query_terms": terms,
        "seeds": seeds,
        "hops": hop_trace,
        "expanded": expanded,
        "candidates": [c["source"] for c in candidates],
    }
    return RecallResult(candidates, trace)
