"""对话改计划：挪**一块**，不是重排整份（R-105 / `WP-PLAN-CHAT-ADJUST`）。

用户要的是「晚上有课，把七点那块挪到九点」之后**再读那份计划，那块真在九点**。
三件事写死在这里：

1. **只动被点名的那一行**。其余行原样写回（同一批 `PlanItem` 对象，不重新生成），
   所以不会出现"顺手把整份重排了一遍"——那正是判据②禁的静默整份重排。
2. **边界复用排程那台校验器**（:func:`plobi.agenda.llm_planning.validate_proposal`）。
   对话改和 L1 排必须过同一道闸：睡眠与三餐不许被挤掉、同一时段不许双订、
   总量不许超当天清醒窗、占满空档的通勤块不许存在。**两套规则迟早各说各话**。
3. **改不动就给一句人话**（材料冲突 / 时间不够 / 那块找不到），
   并且**一个字节都不写**。假成功比失败难查得多。

每块被改过时在自己的 `evidence` 里留 `edited_by` / `edited_at` / `moved_from` / `edit_reason`
——面上"改了哪一块、谁改的"读的就是这一格。完成态那格的语义没碰（`WP-PLAN-COMPLETION-ACK`
落在 events 上，这里只写 plan_items）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Optional

from . import llm_planning, store
from .store import PlanItem

#: 读**库里已有的时间**只走排程那台这一个解析口（裁定 94：一条事实不许两套读法）。
#: 入参给的那串 ISO 在 `move_plan_item` 里做格式校验，那是"这条命令能不能读懂"，不是同一件事。
_dt = llm_planning._dt

#: 一句"改不动"必须能被用户读懂，所以原因串里带钟点与块名，不带内部术语。
NOT_FOUND = "那份计划里找不到这块（说一下大概的钟点或名字，我再找一次）"


def _hhmm(value: Any) -> str:
    """把库里那个时间读成「HH:MM」——面上一句人话要的就是这个。"""
    parsed = _dt(value)
    return parsed.strftime("%H:%M") if parsed is not None else ""


def find_item(items: Iterable[PlanItem], ref: str) -> Optional[PlanItem]:
    """按 id / event_id / 标题 / 起始钟点找那一块。

    只给一个"七点那块"这种钟点时，**必须唯一命中**才算找到——两处都从七点开始就回
    None 让人说清楚，不替用户猜（猜错了他会以为改好了）。
    """
    text = str(ref or "").strip()
    if not text:
        return None
    for item in items:
        if text in (item.id, item.event_id, item.title):
            return item
    hits = [item for item in items if _hhmm(item.start_at) == text]
    return hits[0] if len(hits) == 1 else None


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
    """把那一块挪到 `to_start`（–`to_end`）。返回 ``{ok, ...}``，**失败时一个字节都不写**。

    `to_end` 不给 ⇒ **保留这块原来的时长**。「把七点那块挪到九点」说的是同一件事挪个
    钟点，不是把一小时改成两小时——时长是块自己的属性，不是这里编出来的。
    """
    plan = store.get_daily_plan(conn, for_date)
    if plan is None:
        return {"ok": False, "reason": f"{for_date} 还没有计划，没有可改的东西。"}

    items = store.list_plan_items(conn, plan.id)
    target = find_item(items, ref)
    if target is None:
        return {"ok": False, "reason": NOT_FOUND, "plan_id": plan.id}

    try:
        start = datetime.fromisoformat(str(to_start))
    except ValueError:
        return {"ok": False, "reason": "新时间读不出来（要给带日期的 ISO，例如 2026-10-17T21:00:00）。"}
    if to_end is None or not str(to_end).strip():
        prev_start = _dt(target.start_at)
        prev_end = _dt(target.end_at)
        if prev_start is None or prev_end is None:
            return {"ok": False, "reason": f"「{target.title}」没有结束时间，我不替它编一个；说一下挪到哪一刻结束。"}
        end = start + (prev_end - prev_start)
    else:
        try:
            end = datetime.fromisoformat(str(to_end))
        except ValueError:
            return {"ok": False, "reason": "新结束时间读不出来（带日期的 ISO）。"}
    if end <= start:
        return {"ok": False, "reason": "结束时间必须晚于开始时间，这块我不写。"}

    moved_from = f"{_hhmm(target.start_at)}-{_hhmm(target.end_at)}"
    proposed = []
    for item in items:
        row = item.to_dict()
        if item.id == target.id:
            row["start_at"] = start.isoformat()
            row["end_at"] = end.isoformat()
            evidence = dict(row.get("evidence") or {})
            evidence.update({
                "edited_by": by,
                "edited_at": datetime.now().isoformat(timespec="seconds"),
                "moved_from": moved_from,
                "edit_reason": reason,
            })
            row["evidence"] = evidence
        proposed.append(row)

    # 同一道闸：把整份计划当成一次提案校，任何一条越界就整笔不写。
    anchors = _anchors_for(conn, for_date, proposed)
    waking = llm_planning._waking_window(conn, for_date)
    problems = llm_planning.validate_proposal(
        proposed, anchors=anchors,
        waking=(datetime.fromisoformat(waking[0]), datetime.fromisoformat(waking[1])),
    )
    if problems:
        return {
            "ok": False,
            "reason": "这块挪不过去：" + "；".join(problems[:3]) + "。原计划一个字没改。",
            "violations": problems,
            "plan_id": plan.id,
        }

    stored = store.replace_plan_items(conn, plan.id, [PlanItem(**row) for row in proposed])
    moved_to = f"{start.strftime('%H:%M')}-{end.strftime('%H:%M')}"
    return {
        "ok": True,
        "plan_id": plan.id,
        "for_date": for_date,
        "changed": {
            "title": target.title,
            "from": moved_from,
            "to": moved_to,
            "start_at": start.isoformat(),
            "end_at": end.isoformat(),
            "by": by,
            "reason": reason,
        },
        "item_count": len(stored),
        # 判据①要"再读那份计划"能复跑，所以把读法一起交回去。
        "read_back": "cd Code && .venv/Scripts/python.exe -c \""
                     "import sys;sys.path.insert(0,'.');from plobi.agenda import store;"
                     f"c=store.connect(None);p=store.get_daily_plan(c,'{for_date}');"
                     "print([(i.title,str(i.start_at)[11:16],str(i.end_at)[11:16]) "
                     "for i in store.list_plan_items(c,p.id)])\"",
    }


def _anchors_for(conn: Any, day: str, rows: list[dict]) -> list[dict]:
    """作息锚点（睡眠/三餐）——校验器拿它们当"不许被挤掉"的那几条。

    已经被写进计划里的锚点行**不算挤掉自己**：校验器按 `template_id`/`id` 认，
    所以这里把锚点的身份带上。
    """
    kept, _yielded, _moved = llm_planning.planning.place_anchors_for_day(conn, day, [])
    anchors = llm_planning.planning.kept_anchors_as_dicts(kept)
    return anchors
