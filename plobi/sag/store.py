"""SQLite-backed **derived** event-entity index for SAG.

This is a rebuildable index, never a second source of truth. Markdown pages in
the brain are canonical; deleting this database and re-running the indexer must
produce byte-identical retrieval results.

One page → one semantically complete ``event`` + a set of indexing ``entities``.
Relations are NOT stored: hyperedges are instantiated at query time by joining
on shared entities, so incremental writes never rebuild a global graph.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    source       TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    abstract     TEXT NOT NULL,
    content_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS entities (
    name TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS event_entities (
    source TEXT NOT NULL,
    entity TEXT NOT NULL,
    PRIMARY KEY (source, entity)
);
CREATE INDEX IF NOT EXISTS idx_ee_entity ON event_entities(entity);
"""


class SagStore:
    """A thin, deterministic wrapper over one SQLite database."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "SagStore":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def upsert_event(
        self,
        source: str,
        title: str,
        abstract: str,
        entities: Iterable[str],
        content_hash: str,
    ) -> bool:
        """Insert or refresh one page. Returns False when nothing changed.

        The hash check is what makes incremental ingestion cheap: an unchanged
        page costs one SELECT and writes nothing, so a growing brain never
        triggers a global re-index.
        """
        row = self.conn.execute(
            "SELECT content_hash FROM events WHERE source = ?", (source,)
        ).fetchone()
        names = sorted({str(e).strip().lower() for e in entities if str(e).strip()})
        if row and row[0] == content_hash:
            existing = {r[0] for r in self.conn.execute(
                "SELECT entity FROM event_entities WHERE source = ?", (source,)
            )}
            if existing == set(names):
                return False

        self.conn.execute(
            "INSERT OR REPLACE INTO events(source, title, abstract, content_hash) "
            "VALUES (?, ?, ?, ?)",
            (source, title, abstract, content_hash),
        )
        self.conn.execute("DELETE FROM event_entities WHERE source = ?", (source,))
        for name in names:
            self.conn.execute(
                "INSERT OR IGNORE INTO entities(name) VALUES (?)", (name,)
            )
            self.conn.execute(
                "INSERT OR IGNORE INTO event_entities(source, entity) VALUES (?, ?)",
                (source, name),
            )
        self.conn.commit()
        return True

    def remove_missing(self, live_sources: Sequence[str]) -> int:
        """Drop index rows for pages that no longer exist on disk."""
        live = set(live_sources)
        stale = [r[0] for r in self.conn.execute("SELECT source FROM events")
                 if r[0] not in live]
        for source in stale:
            self.conn.execute("DELETE FROM events WHERE source = ?", (source,))
            self.conn.execute("DELETE FROM event_entities WHERE source = ?", (source,))
        if stale:
            self.conn.commit()
        return len(stale)

    def all_sources(self) -> List[str]:
        return [r[0] for r in self.conn.execute(
            "SELECT source FROM events ORDER BY source"
        )]

    def event(self, source: str) -> dict | None:
        row = self.conn.execute(
            "SELECT source, title, abstract FROM events WHERE source = ?", (source,)
        ).fetchone()
        if not row:
            return None
        return {"source": row[0], "title": row[1], "abstract": row[2]}

    def seed_sources(self, terms: Sequence[str]) -> List[str]:
        """Lexical entry points: pages whose title or abstract mentions a term."""
        if not terms:
            return []
        clauses = []
        params: List[str] = []
        for term in dict.fromkeys(t.lower() for t in terms):
            clauses.append("(lower(title) LIKE ? OR lower(abstract) LIKE ?)")
            like = f"%{term}%"
            params.extend((like, like))
        sql = "SELECT source FROM events WHERE " + " OR ".join(clauses) + " ORDER BY source"
        return [r[0] for r in self.conn.execute(sql, params)]

    def expand(
        self,
        seeds: Sequence[str],
        *,
        hops: int = 1,
        frontier_limit: int = 50,
    ) -> Tuple[List[str], List[dict]]:
        """Instantiate local hyperedges: hop between events along shared entities.

        Only the entities adjacent to the current frontier are considered, so
        this is a *local* index for this query — nothing is pre-built globally.
        Returns (new sources in discovery order, per-hop trace).
        """
        seen = set(seeds)
        frontier = list(seeds)
        discovered: List[str] = []
        trace: List[dict] = []
        for hop in range(max(0, hops)):
            if not frontier:
                break
            marks = ",".join("?" for _ in frontier)
            rows = self.conn.execute(
                "SELECT ee1.entity AS bridge, ee2.source AS target "
                "FROM event_entities ee1 "
                "JOIN event_entities ee2 ON ee1.entity = ee2.entity "
                f"WHERE ee1.source IN ({marks}) "
                "ORDER BY bridge, target",
                tuple(frontier),
            ).fetchall()
            by_bridge: dict[str, List[str]] = {}
            for bridge, target in rows:
                by_bridge.setdefault(bridge, []).append(target)
            next_frontier: List[str] = []
            for bridge in sorted(by_bridge):
                added = []
                for target in by_bridge[bridge][:frontier_limit]:
                    if target in seen:
                        continue
                    seen.add(target)
                    discovered.append(target)
                    next_frontier.append(target)
                    added.append(target)
                if added:
                    trace.append({"hop": hop + 1, "bridge": bridge, "added": added})
            frontier = next_frontier
        return discovered, trace
