"""Pluggable collector surface (WP-COLLECTOR-ICS).

Every source — WeChat chatlog today, 学在浙大 and 教务 tomorrow — implements
:class:`Collector` and yields :class:`Occurrence` rows. **Landing is the pool**:
:func:`land_occurrences` — the **one** place in the tree that writes pool rows
(rulings 186 / 193 —「全部都要直接进池，不能直接落别的库」). ``ingest_occurrences``
stays as the agenda-side seam it always was, but no automatic path may call it:
an agent reads the pool and decides what becomes a schedule row.

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
    #: **正文**（消息全文／邮件正文／事件说明）。
    #:
    #: 为什么单独一格而不是塞进 `evidence`：池里 `body` 那一列就是「这一条说了什么」，
    #: 而 `evidence` 是**证据**（来源、身份、时间戳那些）。微信那条正文能到好几千字，
    #: 塞进证据格里会把那一格变成一坨 JSON，也读不出来。
    #:
    #: 空串是一个**读数**：这一族没带正文（课表那种只有标题的），不是「正文丢了」。
    body: str = ""
    #: 这一条发生在**哪一扇会话**里（会话号）。空串＝这一族没有会话概念。
    #: 落进池的 `conversation` 列 ⇒ Agent 能按会话筛、按群分（裁 187① 那一对列）。
    conversation: str = ""
    #: 这一扇是群还是私聊（`group`／`private`）。空串＝不知道，不许猜。
    visibility: str = ""


@runtime_checkable
class Collector(Protocol):
    """Structural contract every source collector satisfies.

    Implementations do NOT write the agenda — :meth:`pull` only reads the
    source. Landing is :func:`land_occurrences` — **the one place in the tree
    that writes pool rows** (ruling 186 / ``Docs/specs/source-pool.md``:
    采集器只写信息池，池→日程由 Agent 读了自己整合). ``ingest_occurrences``
    stays as the seam it always was, but no automatic path may call it any more.
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

    **会话那两列照 ``Occurrence`` 上带的抄**（`conversation`／`visibility`）：这一族没有
    会话概念就**留空**——空是「没有」，不是「猜一个」（裁 187 那条"别拿猜的去填"）。
    旧形状里这两格是这一层**故意不填**的（那时只有微信那族自己填），钉钉改带之后那句话
    就过期了；而 `sweep.py` 里那份内联副本**已经**在带——两份映射就此漂移。现在只有这一份。
    """
    from plobi.pool import PoolItem

    return [
        PoolItem(
            source=source or occurrence.source,
            external_id=occurrence.external_id,
            kind=occurrence.kind or "note",
            title=occurrence.title or "",
            # 正文：``Occurrence.body`` 优先；没有就退回证据格里那个 ``text``（邮件／钉钉那族
            # 现在的写法）。两处都没有就是空串——**不是**「正文丢了」，是这一族没带正文。
            body=occurrence.body or str((occurrence.evidence or {}).get("text") or ""),
            context=dict(occurrence.evidence or {}),
            occurred_at=occurrence.start_at,
            conversation=occurrence.conversation or "",
            visibility=occurrence.visibility or "",
        )
        for occurrence in occurrences
    ]


def land_occurrences(
    occurrences: list[Occurrence],
    *,
    source: Optional[str] = None,
    db_path: Optional[Any] = None,
) -> dict[str, Any]:
    """落池。**全仓唯一一处**把 ``Occurrence`` 写进池的代码。

    为什么要有这一层（2026-11-17 用户拍「落点收成一处」）：从前**落池有两条路**——
    微信／教务／浙大那几条**自己**调 ``pool.land``，其余走 ``sweep`` 那一层；而那条分岔
    是 ``getattr(collector, "collect", None)`` **运行时探测**出来的隐藏协议（``collect()``
    根本不在 :class:`Collector` 契约里）⇒ 照契约写新腿走公共落点、照老腿写自己落，
    **落池语义取决于你抄了谁**。同一个池里还因此长出**两份映射代码**（``sweep.py`` 内联
    的那份与 :func:`pool_items_of`），且已经漂移（前者带会话两列、后者不带）。

    现在：落点只有这一处，差别只剩「谁触发、单腿还是整轮」。

    ``db_path`` 是给测试钉落点用的（不传就会写真的共享池）——**这不是第二个落点**，
    只是把同一个写入口指向另一个文件。
    """
    # 函数内 import（不是模块头）：`tests/plobi/test_collect_sweep.py` 会在 `plobi.pool`
    # 模块上打桩 `land`，模块头绑死的那一份就看不见那个桩了。
    from plobi.pool import land as pool_land

    items = pool_items_of(occurrences, source=source)
    result = pool_land(items, db_path=db_path) if db_path is not None else pool_land(items)
    created = int(result.get("created") or 0)
    updated = int(result.get("updated") or 0)
    # `pool.land` 自己那一格 `unchanged` 恒为 0（它数的是「没动」的那次 continue），
    # 所以这里按总数倒推——**那一格是读数，不许并成 0**。
    return {
        "created": created,
        "updated": updated,
        "unchanged": max(len(items) - created - updated, 0),
    }
