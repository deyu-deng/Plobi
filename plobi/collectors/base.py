"""Pluggable collector surface (WP-COLLECTOR-ICS).

Every source — WeChat chatlog today, 学在浙大 and 教务 tomorrow — implements
:class:`Collector` and yields :class:`Occurrence` rows. **Landing is the pool**:
:func:`pool_items_of` + ``plobi.pool.land()`` (rulings 186 / 193 —「全部都要直接进池，
不能直接落别的库」). ``ingest_occurrences`` stays as the agenda-side seam it always
was, but no automatic path may call it: an agent reads the pool and decides what
becomes a schedule row.

An ``Occurrence`` is one *concrete* event: a weekly course repeated 16 times
is 16 occurrences, each with its own stable ``external_id``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable


@dataclass(frozen=True)
class Occurrence:
    """One concrete event a collector pulled from a source.

    ``source``       — one of ``plobi.agenda.store.allowed_sources()``（由
                       ``plobi.collectors.registry`` 派生，不是硬编 tuple）；never ``manual``.
    ``external_id``  — stable dedupe key (ics UID / WeChat msg_id / …); must
                       survive re-pulls of the same source unchanged.
    ``start_at``     — local naive ISO (``YYYY-MM-DDTHH:MM:SS``), matching how
                       the events table stores timestamps.
    ``evidence``     — source-specific provenance; for chatlog this is the
                       snippet-only privacy contract, for ics the UID/teacher.
    """

    source: str
    external_id: str
    title: str
    start_at: str
    end_at: Optional[str] = None
    kind: str = "task"
    location: Optional[str] = None
    evidence: dict = field(default_factory=dict)


@runtime_checkable
class Collector(Protocol):
    """Structural contract every source collector satisfies.

    Implementations do NOT write the agenda — :meth:`pull` only reads the
    source. Landing is :func:`pool_items_of` + ``plobi.pool.land()``
    (ruling 186 / ``Docs/specs/source-pool.md``: 采集器只写信息池，池→日程由
    Agent 读了自己整合). ``ingest_occurrences`` stays as the seam it always was,
    but no automatic path may call it any more.
    """

    source_id: str

    def health(self) -> dict:
        """``{"ok": bool, "detail": str}`` — is this source reachable?"""
        ...

    def pull(self, *, since: Any = None) -> list[Occurrence]:
        """Full or incremental pull. Pure read: never writes the agenda."""
        ...


def pool_items_of(occurrences: list[Occurrence], *, source: Optional[str] = None) -> list[Any]:
    """One :class:`Occurrence` → one ``PoolItem``. **The only place that mapping exists.**

    Why a shared helper instead of a block in each collector: the mapping carries two
    decisions that must not drift per source — ``occurred_at`` is the item's **own**
    time (ruling 189：微信那条把会议时间写进 occurred_at 就是错在这), and the raw
    ``evidence`` rides along as ``context`` so the provenance survives. A third collector
    writing its own copy is a third answer to "什么算这条的时间".

    ``visibility`` / ``conversation`` stay unset on purpose: they are **窗口**概念（一条
    消息属于哪扇会话、给谁看），从学校接口或 .ics 拉来的条目没有这个概念，填一个猜的值
    比填空更糟（裁 187 那条"别拿猜的去填"）。
    """
    from plobi.pool import PoolItem

    return [
        PoolItem(
            source=source or occurrence.source,
            external_id=occurrence.external_id,
            kind=occurrence.kind or "note",
            title=occurrence.title or "",
            body=str((occurrence.evidence or {}).get("text") or ""),
            context=dict(occurrence.evidence or {}),
            occurred_at=occurrence.start_at,
        )
        for occurrence in occurrences
    ]
