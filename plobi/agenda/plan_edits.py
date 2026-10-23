"""对话改日程：挪**一条**，不是重排整份（R-105 / `WP-PLAN-CHAT-ADJUST`）。

用户要的是「晚上有课，把七点那块挪到九点」之后**再读那天，它真在九点**。
计划层 10-22 整层退役后，被点名的那块就是 `events` 里真实的一行。三件事写死在这里：

1. **只动被点名的那一行**。其余行原样不动，所以不会出现「顺手把整份重排了一遍」——
   那正是判据②禁的静默整份重排。
2. **边界复用排程那台校验器**（:func:`plobi.agenda.llm_planning.validate_proposal`）。
   对话改和 L1 排必须过同一道闸：同一时段不许双订、不许复述、避开时段不许占。
   只拒**这次挪动新带进来**的问题——当天原本就有的重叠不该让一条无关的挪动失败。
3. **改不动就给一句人话**（冲突 / 时间不够 / 那块找不到），并且**一个字节都不写**。
   假成功比失败难查得多。

被改过的那一行在自己的 `evidence` 里留 `edited_by` / `edited_at` / `moved_from` / `edit_reason`
——面上「改了哪一块、谁改的」读的就是这一格。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Iterable, Optional

from . import llm_planning, planning, store
from .store import Event

#: 读**库里已有的时间**只走排程那台这一个解析口（裁定 94：一条事实不许两套读法）。
#: 入参那串 ISO 在 :func:`_read_span` 里校验，那是「这条命令读不读得懂」，不是同一件事。
_dt = llm_planning._dt

#: 一句「改不动」必须能被用户读懂，所以原因串里带钟点与块名，不带内部术语。
NOT_FOUND = "那天找不到这块（说一下大概的钟点或名字，我再找一次）"


def _hhmm(value: Any) -> str:
    """把库里那个时间读成「HH:MM」——面上一句人话要的就是这个。"""
    parsed = _dt(value)
    return parsed.strftime("%H:%M") if parsed is not None else ""


def find_event(events: Iterable[Event], ref: str) -> Optional[Event]:
    """按 id / 标题 / 起始钟点找那一行。

    只给一个「七点那块」这种钟点时，**必须唯一命中**才算找到——两处都从七点开始就回
    None 让人说清楚，不替用户猜（猜错了他会以为改好了）。
    """
    text = str(ref or "").strip()
    if not text:
        return None
    for event in events:
        if text in (event.id, event.title):
            return event
    hits = [event for event in events if _hhmm(event.start_at) == text]
    return hits[0] if len(hits) == 1 else None


def _read_span(to_start: Any, to_end: Any) -> tuple[Optional[datetime], Optional[datetime], str]:
    """读入参给的新钟点。`to_end` 空 ⇒ 返回 ``None``，由调用方按「保留原时长」处理。"""
    try:
        start = datetime.fromisoformat(str(to_start))
    except ValueError:
        return None, None, "新时间读不出来（要给带日期的 ISO，例如 2026-10-17T21:00:00）。"
    if not to_end or not str(to_end).strip():
        return start, None, ""
    try:
        end = datetime.fromisoformat(str(to_end))
    except ValueError:
        return None, None, "新结束时间读不出来（带日期的 ISO）。"
    if end <= start:
        return None, None, "结束时间必须晚于开始时间，这块我不写。"
    return start, end, ""


def _span_row(event: Event) -> dict[str, Any]:
    """校验器认的形状（`title` + `start_at` + `end_at`）。"""
    return {
        "title": event.title,
        "start_at": event.start_at,
        "end_at": event.end_at,
    }


def move_plan_item(
    conn: Any,
    for_date: str,
    *,
    ref: str,
    to_start: str,
    to_end: Optional[str] = None,
    by: str,
    reason: str = "",
) -> dict[str, Any]:
    """把那一行挪到 `to_start`（–`to_end`）。返回 ``{ok, ...}``，**失败时一个字节都不写**。

    `to_end` 不给 ⇒ **保留这块原来的时长**。「把七点那块挪到九点」说的是同一件事挪个
    钟点，不是把一小时改成两小时——时长是块自己的属性，不是这里编出来的。
    """
    events = store.list_events(
        conn,
        start_from=f"{for_date}T00:00:00",
        start_to=f"{for_date}T23:59:59",
    ) or []
    if not events:
        return {"ok": False, "reason": f"{for_date} 还没有日程，没有可改的东西。"}

    target = find_event(events, ref)
    if target is None:
        return {"ok": False, "reason": NOT_FOUND, "for_date": for_date}

    start, end, error = _read_span(to_start, to_end)
    if error:
        return {"ok": False, "reason": error}

    new_end = end or _preserve_length(target, start)
    moved_from = f"{_hhmm(target.start_at)}-{_hhmm(target.end_at)}"
    before_rows = [_span_row(event) for event in events]
    after_rows = []
    for event in events:
        row = _span_row(event)
        if event.id == target.id:
            row["start_at"] = start.isoformat()
            row["end_at"] = new_end.isoformat()
        after_rows.append(row)

    # 同一道闸：挪前 / 挪后各校一次，只拒这次挪动新带进来的问题。
    avoid = planning.avoid_windows_for_day(conn, for_date)
    before = llm_planning.validate_proposal(before_rows, avoid=avoid)
    problems = [
        problem for problem in llm_planning.validate_proposal(after_rows, avoid=avoid)
        if problem not in before
    ]
    if problems:
        return {
            "ok": False,
            "reason": "这块挪不过去：" + "；".join(problems[:3]) + "。原日程一个字没改。",
            "violations": problems,
            "for_date": for_date,
        }

    evidence = dict(target.evidence or {})
    evidence.update({
        "edited_by": by,
        "edited_at": datetime.now().isoformat(timespec="seconds"),
        "moved_from": moved_from,
        "edit_reason": reason,
    })
    updated = store.update_event(
        conn,
        target.id,
        start_at=start.isoformat(),
        end_at=new_end.isoformat(),
        evidence=evidence,
    )
    if updated is None:
        return {"ok": False, "reason": NOT_FOUND, "for_date": for_date}

    changed = {
        "title": target.title,
        "from": moved_from,
        "to": f"{start.strftime('%H:%M')}-{new_end.strftime('%H:%M')}",
        "start_at": start.isoformat(),
        "end_at": new_end.isoformat(),
        "by": by,
        "reason": reason,
    }
    return {
        "ok": True,
        "event_id": target.id,
        "for_date": for_date,
        "changed": changed,
        "item_count": len(events),
        # 判据①要「再读那天」能复跑，所以把读法一起交回去。
        "read_back": "cd Code && .venv/Scripts/python.exe -c \""
                     "import sys;sys.path.insert(0,'.');from plobi.agenda import store;"
                     "c=store.connect(None);"
                     f"print([(e.title,str(e.start_at)[11:16],str(e.end_at)[11:16]) "
                     f"for e in store.list_events(c,start_from='{for_date}T00:00:00',"
                     f"start_to='{for_date}T23:59:59')])\"",
    }


def _preserve_length(event: Event, start: datetime) -> datetime:
    """没给新结束时间 ⇒ 沿用这块原来的时长（挪钟点≠改长度）。"""
    prev_start, prev_end = _dt(event.start_at), _dt(event.end_at)
    if prev_start is None or prev_end is None:
        return start + timedelta(hours=1)
    return start + (prev_end - prev_start)
