"""Retrieval over the Mind vault (keyword, offline-first).

Real implementation (P0):
  - case-insensitive keyword grep across every ``.md`` file inside
    ``SAFE_PREFIXES`` (Vault/projects/Plobi, Vault/meta, Vault/notes,
    Loom/wiki/*, Loom/raw/chat-logs/*, ...).
  - Each hit is scored by total keyword occurrence count and rendered as a
    bounded markdown snippet (file header + surrounding lines of the first
    match), capped so one call can never flood the model context.

Optional P2 upgrade: local ``sentence-transformers`` embeddings + a tiny
vector index for semantic recall (offline-first, no external service).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List

from plobi.mind.paths import SAFE_PREFIXES

MAX_SNIPPET_CHARS = 1200
MAX_TOTAL_CHARS = 6000
SNIPPET_CONTEXT_LINES = 3

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


def search(mind_root: Path, query: str, limit: int = 5) -> List[str]:
    """Return relevant Mind markdown snippets for ``query``.

    Scans ``SAFE_PREFIXES`` under ``mind_root``, scores by keyword
    occurrence, and returns the top-``limit`` formatted snippets (each is a
    markdown heading with the vault-relative path + a bounded excerpt).
    """
    root = Path(mind_root)
    if not root.is_dir():
        return []

    terms = _tokens(query)
    if not terms:
        return []

    hits: List[tuple[int, str, str]] = []  # (score, relative_path, snippet)
    for prefix in SAFE_PREFIXES:
        base = root / prefix
        if not base.is_dir():
            continue
        for md in sorted(base.rglob("*.md")):
            try:
                text = _read_md(md)
            except OSError:
                continue
            low = text.lower()
            score = sum(low.count(t) for t in terms)
            if score == 0:
                continue
            rel = md.relative_to(root).as_posix()
            hits.append((score, rel, _snippet_for(text, terms)))

    hits.sort(key=lambda h: (-h[0], h[1]))

    results: List[str] = []
    total = 0
    for score, rel, snippet in hits[:limit]:
        block = f"### Mind: {rel}\n\n{snippet}"
        total += len(block)
        if total > MAX_TOTAL_CHARS:
            break
        results.append(block)
    return results
