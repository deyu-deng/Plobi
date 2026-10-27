"""Retrieval over the Mind brain (keyword, offline-first).

What this does (P0 + the §12.7 第 3 条 upgrade):
  - case-insensitive keyword grep over every ``.md`` file inside the **memory
    zones only** (``Loom/wiki/*``; the human surface ``Vault/…`` deliberately left
    this list — user 2026-10-24/25「vault 不要进记忆系统」).
  - over-collect → score → de-duplicate by abstract → then fill the injection
    budget **tier by tier** (full / overview / abstract). A page that no longer
    fits its tier falls back to a smaller tier instead of being cut mid-sentence;
    the budget is projected *before* each append, never after.

That last part is the borrowed shape: openviking over-fetches ``limit*4``,
scores, dedupes by ``abstract:{category}:{abstract}``, has three content depths,
and checks ``projected_chars`` before every append. See
``Docs/specs/memory-adapter.md`` §12.6/§12.7.

Optional P2 upgrade: local ``sentence-transformers`` embeddings + a tiny
vector index for semantic recall (offline-first, no external service).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List

from plobi.mind.paths import MEMORY_ZONES, real_target

MAX_SNIPPET_CHARS = 1200
MAX_TOTAL_CHARS = 6000
SNIPPET_CONTEXT_LINES = 3

# Three depths, big → small. ``search`` never truncates a page mid-tier: it
# demotes the page to the next tier down and only stops when even the abstract
# no longer fits the remaining budget.
TIER_CHARS = {"full": MAX_SNIPPET_CHARS, "overview": 600, "abstract": 200}
OVERFETCH_FACTOR = 4
# How many of the top-ranked pages are allowed to come in at full depth.
FULL_DEPTH_SLOTS = 2

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+")

_STOPWORDS = {
    "the", "and", "for", "are", "was", "you", "your", "our", "with",
    "that", "this", "from", "have", "has", "not", "but", "all", "can",
    "just", "like", "they", "them", "their", "what", "when", "where",
    "who", "how", "why", "its", "were", "will", "would", "should",
    "such", "also", "more", "most", "some", "than", "into", "only",
    "even", "about", "after", "before", "between", "these", "those",
    "there", "here", "which", "while", "still", "might", "much",
}


def _tokens(query: str) -> List[str]:
    tokens = []
    for t in _TOKEN_RE.findall(query):
        low = t.lower()
        if not low:
            continue
        if low[0].isascii():
            # English tokens need real length to avoid noise hits ("no", "it")
            if len(low) >= 3 and low not in _STOPWORDS:
                tokens.append(low)
        else:
            tokens.append(low)
    return tokens


def _read_md(path: Path) -> str:
    """Read a markdown file, tolerating legacy GBK-encoded vault files."""
    raw = path.read_bytes()
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, ValueError):
            continue
    return raw.decode("utf-8", errors="replace")


def _snippet_for(text: str, terms: List[str], max_chars: int = MAX_SNIPPET_CHARS) -> str:
    """Return a bounded excerpt around the first keyword hit."""
    lines = text.splitlines()
    low_lines = [ln.lower() for ln in lines]
    first = -1
    for idx, ln in enumerate(low_lines):
        if any(t in ln for t in terms):
            first = idx
            break
    if first < 0:
        first = 0
    start = max(0, first - SNIPPET_CONTEXT_LINES)
    end = min(len(lines), first + SNIPPET_CONTEXT_LINES + 1)
    excerpt = "\n".join(lines[start:end]).strip()
    if len(excerpt) > max_chars:
        excerpt = excerpt[:max_chars].rstrip() + "\n…"
    return excerpt


def _frontmatter_and_body(text: str) -> tuple[str, str]:
    """Split a page into its leading ``---`` block and the rest."""
    if not text.startswith("---"):
        return "", text
    end = text.find("\n---", 3)
    if end < 0:
        return "", text
    return text[3:end].strip("\n"), text[end + 4:].lstrip("\n")


def _title_of(body: str, meta: str) -> str:
    for line in meta.splitlines():
        if line.strip().lower().startswith("title:"):
            return line.split(":", 1)[1].strip().strip("'\"")
    for line in body.splitlines():
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return ""


def _hit_line(body: str, terms: List[str]) -> str:
    for line in body.splitlines():
        low = line.lower()
        if any(t in low for t in terms):
            return line.strip().strip("#").strip()
    first = next((ln.strip() for ln in body.splitlines() if ln.strip()), "")
    return first


def _clip(text: str, max_chars: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip(" ,;:") + "…"


def _nav_names(root: Path) -> frozenset:
    """Skip-list for navigational pages, read from the same schema the gates use.

    ``index.md`` / ``hot.md`` / ``README.md`` are short, generic lists: they score
    well on any query (they name everything) and crowd the real pages out of the
    budget. The list lives in ``wiki/meta/schema.json`` so the vocabulary file stays
    the one source; if it cannot be read we fall back to the known five.
    """
    fallback = frozenset({"INDEX.md", "index.md", "hot.md", "log.md", "overview.md", "README.md"})
    try:
        from plobi.mind.schema import load_schema

        names = load_schema(root).get("skip_names") or []
    except Exception:  # noqa: BLE001 - recall must never die on a metadata read
        return fallback
    return frozenset(str(n) for n in names) | fallback if names else fallback


def _tiers_for(text: str, terms: List[str]) -> Dict[str, str]:
    """The same page at three depths, each independently capped.

    ``abstract`` is one line (title + the hitting line) — cheap enough that a
    long tail of weak matches can still be named instead of dropped; ``overview``
    is the tight window; ``full`` is what recall used to return for everything.
    """
    meta, body = _frontmatter_and_body(text)
    title = _title_of(body, meta)
    hit = _hit_line(body, terms)
    abstract = _clip(f"{title} — {hit}" if title and hit else (title or hit), TIER_CHARS["abstract"])
    overview = _clip(_snippet_for(body, terms, TIER_CHARS["overview"] * 3), TIER_CHARS["overview"])
    full = _snippet_for(body, terms, TIER_CHARS["full"])
    return {"full": full, "overview": overview, "abstract": abstract}


def _dedupe_by_abstract(hits: List[Dict]) -> List[Dict]:
    """Two pages saying the same thing count once (openviking's abstract dedupe)."""
    seen: set[str] = set()
    kept: List[Dict] = []
    for hit in hits:
        key = hit["tiers"]["abstract"].lower()
        if key and key in seen:
            continue
        seen.add(key)
        kept.append(hit)
    return kept


def search(mind_root: Path, query: str, limit: int = 5) -> List[str]:
    """Return relevant Mind markdown snippets for ``query``, within one budget.

    Scans the memory zones (:data:`plobi.mind.paths.MEMORY_ZONES` — the brain's
    ``Loom/wiki/*``; the human surface ``Vault/…`` is not recalled), scores by
    keyword occurrence, de-duplicates, and returns at most ``limit`` blocks whose
    combined length never exceeds :data:`MAX_TOTAL_CHARS`. Pages past
    :data:`FULL_DEPTH_SLOTS` come in as overviews; anything that stops fitting is
    demoted a tier rather than cut off.
    """
    root = Path(mind_root)
    if not root.is_dir():
        return []

    terms = _tokens(query)
    if not terms:
        return []

    collected: List[Dict] = []
    overfetch = max(limit * OVERFETCH_FACTOR, limit)
    nav = _nav_names(root)
    for prefix in MEMORY_ZONES:
        if len(collected) >= overfetch:
            break
        base = real_target(prefix, root)
        if not base.is_dir():
            continue
        for md in sorted(base.rglob("*.md")):
            if md.name in nav:
                continue      # 目录页与门面页不是知识：它们短、泛指，挤掉真页的预算
            try:
                text = _read_md(md)
            except OSError:
                continue
            low = text.lower()
            score = sum(low.count(t) for t in terms)
            if score == 0:
                continue
            collected.append({
                "score": score,
                "rel": f"{prefix}/{md.relative_to(base).as_posix()}",
                "tiers": _tiers_for(text, terms),
            })
            if len(collected) >= overfetch:
                # over-fetch a bounded multiple, then stop — a big grep must not
                # turn recall into a full-tree read.
                break

    collected.sort(key=lambda h: (-h["score"], h["rel"]))
    ranked = _dedupe_by_abstract(collected)[:limit]

    results: List[str] = []
    total = 0
    for idx, hit in enumerate(ranked):
        rel = hit["rel"]
        tiers = hit["tiers"]
        order = ("full", "overview", "abstract") if idx < FULL_DEPTH_SLOTS else ("overview", "abstract")
        for tier in order:
            body = tiers.get(tier) or ""
            if not body:
                continue
            block = f"### Mind: {rel}\n\n{body}"
            projected = total + len(block) + (2 if results else 0)
            if projected > MAX_TOTAL_CHARS:
                continue  # demote: try the next, smaller depth instead of truncating
            if results:
                total += 2
            results.append(block)
            total = projected
            break
    return results
