"""Deterministic BM25 scoring + page invalidation for Mind recall.

Two borrowed shapes, both from ``claude-obsidian`` (MIT, pinned ``32ac5a02…``):

* a **local, deterministic, offline** sparse ranker (``Docs/specs/memory-adapter.md``
  §11.2 — the P1 debt this module closes);
* the **status vocabulary / supersession** idea (§12.6), so a memory that has
  been replaced or rejected stops entering context.

Deliberately NOT borrowed: its write transactions, advisory locks and ``--apply``
path (native Windows is ``UNSUPPORTED_PLATFORM`` there). Mind keeps writing
through ``plobi/mind/writer.py`` + the L1 approval gate.

Design notes (read before "improving" this):

* The ranker works on the **bounded over-fetch candidate pool**, not a full-corpus
  index: ``df`` and ``avgdl`` are computed over the pages that already matched at
  least one query term. That keeps the existing "a big grep must not turn recall
  into a full-tree read" guarantee and costs no extra I/O. The trade-off is that
  IDF is a local estimate; it is still deterministic and query-stable for a fixed
  pool, and length normalisation / tf saturation are exact BM25.
* Chinese is matched as a substring (``low.count(term)``), exactly as the old
  keyword ranker did. No tokenizer dependency, no model, no network.
* Every function here is pure. Same input ⇒ byte-identical output.
"""
from __future__ import annotations

import math
from typing import Dict, List, Mapping, Sequence, Tuple

# Standard Okapi BM25 damping. Exposed as module constants so tests can pin them.
K1 = 1.2
B = 0.75

# A closed vocabulary borrowed from claude-obsidian's SOURCE_STATUSES /
# CLAIM_ASSESSMENTS, collapsed onto the two states recall cares about.
# Unknown / missing status stays ALIVE on purpose: the 53 existing wiki pages
# have no status field at all, and a new gate must not silently hide them.
_DEAD_STATUSES = frozenset({"superseded", "rejected", "deprecated", "contested"})
_STATUS_KEY = "status"
_SUPERSEDES_KEY = "supersedes"
_SUPERSEDED_BY_KEY = "superseded_by"


def parse_meta(meta: str) -> Dict[str, str]:
    """Read top-level ``key: value`` pairs without a YAML dependency.

    Frontmatter here is deliberately tiny (title / tags / status / supersedes).
    A strict YAML parser would be a new dependency for a recall hot path; the
    few list forms we accept are normalised in :func:`_values` instead.
    """
    fields: Dict[str, str] = {}
    for line in (meta or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip().lower()
        if key and key not in fields:
            fields[key] = value.strip()
    return fields


def _values(raw: str) -> Tuple[str, ...]:
    """Normalise ``a`` / ``a, b`` / ``[a, b]`` / ``- a`` into clean targets."""
    if not raw:
        return ()
    cleaned = raw.strip().strip("[]")
    parts: List[str] = []
    for part in cleaned.split(","):
        part = part.strip().strip("'\"").lstrip("-").strip().replace("\\", "/")
        if not part:
            continue
        if part.endswith(".md"):
            part = part[:-3]
        parts.append(part.strip("/"))
    return tuple(parts)


def page_is_dead(meta: str) -> bool:
    """True when a page declares itself replaced / rejected / expired."""
    fields = parse_meta(meta)
    if fields.get(_STATUS_KEY, "").strip().lower() in _DEAD_STATUSES:
        return True
    return bool(_values(fields.get(_SUPERSEDED_BY_KEY, "")))


def superseded_targets(meta: str) -> Tuple[str, ...]:
    """Pages this one replaces, as normalised logical-path fragments."""
    return _values(parse_meta(meta).get(_SUPERSEDES_KEY, ""))


def _matches_target(rel: str, target: str) -> bool:
    """A target may name a bare stem (``储能``) or a logical path (``…/储能.md``)."""
    if not rel or not target:
        return False
    full = rel.replace("\\", "/")
    if full.endswith(".md"):
        full = full[:-3]
    stem = full.rsplit("/", 1)[-1]
    return full.endswith(target) or stem == target or stem == target.rsplit("/", 1)[-1]


def prune_dead(candidates: List[dict]) -> List[dict]:
    """Drop self-declared dead pages, then drop pages another page supersedes.

    ``supersedes`` is collected from *all* candidates (including dead ones): a
    page that has itself been superseded can still validly declare which older
    page it replaced — that is exactly how a replacement chain is expressed.
    """
    targeted = {t for hit in candidates for t in superseded_targets(str(hit.get("meta", "")))}
    kept: List[dict] = []
    for hit in candidates:
        if page_is_dead(str(hit.get("meta", ""))):
            continue
        rel = str(hit.get("rel", ""))
        if any(_matches_target(rel, target) for target in targeted):
            continue
        kept.append(hit)
    return kept


def bm25(
    term_freqs: Sequence[Mapping[str, int]],
    doc_lengths: Sequence[int],
) -> List[float]:
    """Okapi BM25 over a bounded candidate pool.

    ``term_freqs[i][t]`` is the raw count of query term ``t`` in page ``i``;
    ``doc_lengths[i]`` is that page's token count. Returns one score per page,
    in the same order. Uses the always-positive Robertson IDF form so a term
    present in every page contributes a small weight instead of a negative one.
    """
    n = len(term_freqs)
    if n == 0:
        return []
    if len(doc_lengths) != n:
        raise ValueError("term_freqs and doc_lengths must have the same length")

    terms = tuple(dict.fromkeys(t for tf in term_freqs for t in tf))
    lengths = [max(1, int(length)) for length in doc_lengths]
    avgdl = sum(lengths) / n or 1.0
    df = {t: sum(1 for tf in term_freqs if tf.get(t, 0) > 0) for t in terms}

    scores: List[float] = []
    for tf, dl in zip(term_freqs, lengths):
        score = 0.0
        for term in terms:
            freq = int(tf.get(term, 0))
            if freq <= 0:
                continue
            idf = math.log(1.0 + (n - df[term] + 0.5) / (df[term] + 0.5))
            denominator = freq + K1 * (1.0 - B + B * dl / avgdl)
            score += idf * (freq * (K1 + 1.0)) / denominator
        scores.append(score)
    return scores


def rank(candidates: List[dict], terms: Sequence[str]) -> List[dict]:
    """Attach BM25 scores, drop dead pages, sort by ``(-score, rel)``.

    Each candidate must carry ``tf`` (term → count), ``doc_len``, ``meta`` and
    ``rel``. Pages with a zero score are dropped — a page that matched no term
    must never be injected just because the pool was small.
    """
    alive = prune_dead(candidates)
    if not alive:
        return []
    scores = bm25([hit["tf"] for hit in alive], [hit["doc_len"] for hit in alive])
    for hit, score in zip(alive, scores):
        hit["score"] = score
    alive = [hit for hit in alive if hit["score"] > 0]
    alive.sort(key=lambda hit: (-hit["score"], hit["rel"]))
    return alive
