"""Offline indexing: markdown pages → one event + lightweight entities.

Entity来源按用户 11-13 拍的「wikilink 打底 + 模型补抽」：本刀只做打底
（frontmatter tags + ``[[wikilink]]`` 目标 + 页标题），零模型、零网络、可重放；
模型补抽留 ``extract_page(text, extra_entities=...)`` 这个显式注入点，不在试点
里默认开启。

``index_tree`` 是通用的：它只认 ``(source, text)``，不知道 Mind 的存在。
``build_from_mind`` 才是第一个 source 的适配器。
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)")
_NAV_NAMES = frozenset({"INDEX.md", "index.md", "hot.md", "log.md", "overview.md", "README.md"})


def split_frontmatter(text: str) -> Tuple[str, str]:
    if not text.startswith("---"):
        return "", text
    end = text.find("\n---", 3)
    if end < 0:
        return "", text
    return text[3:end].strip("\n"), text[end + 4:].lstrip("\n")


def _meta_value(meta: str, key: str) -> str:
    for line in meta.splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        if k.strip().lower() == key:
            return v.strip()
    return ""


def _values(raw: str) -> List[str]:
    return [p.strip().strip("'\"") for p in raw.strip().strip("[]").split(",") if p.strip()]


def _title(meta: str, body: str) -> str:
    value = _meta_value(meta, "title").strip("'\"")
    if value:
        return value
    for line in body.splitlines():
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return ""


def _abstract(body: str, limit: int = 200) -> str:
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith(">"):
            continue
        return line[:limit]
    return ""


def extract_page(text: str, *, extra_entities: Iterable[str] = ()) -> dict:
    """Pure extraction. ``extra_entities`` is the model top-up injection point."""
    meta, body = split_frontmatter(text)
    title = _title(meta, body)
    entities = {title} if title else set()
    entities.update(_values(_meta_value(meta, "tags")))
    entities.update(m.strip() for m in _WIKILINK_RE.findall(body) if m.strip())
    entities.update(str(e).strip() for e in extra_entities if str(e).strip())
    return {
        "title": title,
        "abstract": _abstract(body),
        "entities": sorted({e.lower() for e in entities}),
        "meta": meta,
        "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def index_tree(
    pages: Sequence[Tuple[str, str]],
    store,
    *,
    dead_sources: Iterable[str] = (),
) -> dict:
    """Upsert every live page; drop rows for pages that vanished.

    Returns a small report so callers can prove incremental behaviour.
    """
    dead = set(dead_sources)
    live: List[str] = []
    changed = 0
    for source, text in sorted(pages):
        if source in dead:
            continue
        page = extract_page(text)
        live.append(source)
        if store.upsert_event(
            source, page["title"], page["abstract"], page["entities"], page["content_hash"]
        ):
            changed += 1
    removed = store.remove_missing(live)
    return {"pages": len(live), "changed": changed, "removed": removed}


def collect_pages(roots: Sequence[Tuple[str, Path]]) -> List[Tuple[str, str]]:
    """Read ``*.md`` under (logical prefix, directory) pairs. Read-only on disk."""
    pages: List[Tuple[str, str]] = []
    for prefix, base in roots:
        if not base.is_dir():
            continue
        for md in sorted(base.rglob("*.md")):
            if md.name in _NAV_NAMES:
                continue
            try:
                raw = md.read_bytes()
            except OSError:
                continue
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("gbk", errors="replace")
            pages.append((f"{prefix}/{md.relative_to(base).as_posix()}", text))
    return pages


def dead_sources(pages: Sequence[Tuple[str, str]]) -> List[str]:
    """First刀的失效语义 here too: a replaced page must not be indexed at all."""
    from plobi.mind import recall_bm25

    by_source = {source: split_frontmatter(text)[0] for source, text in pages}
    targeted = {
        target
        for meta in by_source.values()
        for target in recall_bm25.superseded_targets(meta)
    }
    dead = []
    for source, meta in by_source.items():
        if recall_bm25.page_is_dead(meta):
            dead.append(source)
            continue
        stem = source.rsplit("/", 1)[-1].removesuffix(".md")
        if any(
            source.endswith(t) or stem == t or stem == t.rsplit("/", 1)[-1]
            for t in targeted
        ):
            dead.append(source)
    return dead


def build_from_mind(mind_root: str | Path, db_path: str | Path):
    """Mind is the first mounted source. Human-readable surface is NOT indexed."""
    from plobi.mind.paths import MEMORY_ZONES, real_target
    from plobi.sag.store import SagStore

    root = Path(mind_root)
    roots = [(zone, real_target(zone, root)) for zone in MEMORY_ZONES]
    pages = collect_pages(roots)
    store = SagStore(db_path)
    report = index_tree(pages, store, dead_sources=dead_sources(pages))
    return store, report
