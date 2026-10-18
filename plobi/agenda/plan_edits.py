"""对话改计划：挪**一块**，不是重排整份（R-105 / `WP-PLAN-CHAT-ADJUST`；R-106 生活块那一半）。

用户要的是「晚上有课，把七点那块挪到九点」之后**再读那份计划，那块真在九点**。
四件事写死在这里：

1. **只动被点名的那一行**。其余行原样写回（同一批 `PlanItem` 对象，不重新生成），
   所以不会出现「顺手把整份重排了一遍」——那正是判据②禁的静默整份重排。
2. **边界复用排程那台校验器**（:func:`plobi.agenda.llm_planning.validate_proposal`）。
   对话改和 L1 排必须过同一道闸：睡眠与三餐不许被挤掉、同一时段不许双订、
   总量不许超当天清醒窗、占满空档的通勤块不许存在。**两套规则迟早各说各话**。
3. **改不动就给一句人话**（材料冲突 / 时间不够 / 那块找不到），
   并且**一个字节都不写**。假成功比失败难查得多。
4. **生活块改的是模板，不是计划行**（R-106）。吃饭／睡觉这类作息的真源在
   `routine_templates`，计划里那一行是它派生出来的；只改计划行就是造第二个真源，
   明晚 20:00 一跑会被抹掉。所以认出作息行之后一律改模板，再把这一天的那一行跟着走，
   并**照实说这是「以后每天」的改动**——「只挪明天一次」不是这条规则的意思。

每块被改过时在自己的 `evidence` 里留 `edited_by` / `edited_at` / `moved_from` / `edit_reason`
——面上「改了哪一块、谁改的」读的就是这一格。完成态那格的语义没碰（`WP-PLAN-COMPLETION-ACK`
落在 events 上，这里只写 plan_items 与 routine_templates）。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any, Iterable, Optional

from . import llm_planning, planning, store
from .store import PlanItem

#: 读**库里已有的时间**只走排程那台这一个解析口（裁定 94：一条事实不许两套读法）。
#: 入参那串 ISO 在 :func:`_read_span` 里校验，那是「这条命令读不读得懂」，不是同一件事。
_dt = llm_planning._dt

#: 一句「改不动」必须能被用户读懂，所以原因串里带钟点与块名，不带内部术语。
NOT_FOUND = "那份计划里找不到这块（说一下大概的钟点或名字，我再找一次）"


def _hhmm(value: Any) -> str:
    """把库里那个时间读成「HH:MM」——面上一句人话要的就是这个。"""
    parsed = _dt(value)
    return parsed.strftime("%H:%M") if parsed is not None else ""


def find_item(items: Iterable[PlanItem], ref: str) -> Optional[PlanItem]:
    """按 id / event_id / 标题 / 起始钟点找那一块。

    只给一个「七点那块」这种钟点时，**必须唯一命中**才算找到——两处都从七点开始就回
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


# --------------------------------------------------------------------------- #
# ① 挪一块（项目块／作业块／生活块都从这一个入口进）
# --------------------------------------------------------------------------- #

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
    点到的若是**生活块**（作息派生的那几行），转 :func:`_move_life_block` 去改模板。
    """
    plan = store.get_daily_plan(conn, for_date)
    if plan is None:
        return {"ok": False, "reason": f"{for_date} 还没有计划，没有可改的东西。"}

    items = store.list_plan_items(conn, plan.id)
    target = find_item(items, ref)
    if target is None:
        return {"ok": False, "reason": NOT_FOUND, "plan_id": plan.id}

    start, end, error = _read_span(to_start, to_end)
    if error:
        return {"ok": False, "reason": error}

    template = _routine_template(conn, target)
    if template is not None:
        return _move_life_block(conn, for_date, template=template,
                                start=start, end=end, by=by, reason=reason)

    new_end = end or _preserve_length(target, start)
    moved_from = f"{_hhmm(target.start_at)}-{_hhmm(target.end_at)}"
    rows: list[dict] = []
    for item in items:
        row = item.to_dict()
        if item.id == target.id:
            row["start_at"] = start.isoformat()
            row["end_at"] = new_end.isoformat()
            evidence = dict(row.get("evidence") or {})
            evidence.update({
                "edited_by": by,
                "edited_at": datetime.now().isoformat(timespec="seconds"),
                "moved_from": moved_from,
                "edit_reason": reason,
            })
            row["evidence"] = evidence
        rows.append(row)

    # 同一道闸：把整份计划当成一次提案校，任何一条越界就整笔不写。
    anchors = _anchors_for(conn, for_date)
    waking = llm_planning._waking_window(conn, for_date)
    problems = llm_planning.validate_proposal(
        rows, anchors=anchors,
        waking=(datetime.fromisoformat(waking[0]), datetime.fromisoformat(waking[1])),
    )
    if problems:
        return {
            "ok": False,
            "reason": "这块挪不过去：" + "；".join(problems[:3]) + "。原计划一个字没改。",
            "violations": problems,
            "plan_id": plan.id,
        }

    stored = store.replace_plan_items(conn, plan.id, [PlanItem(**row) for row in rows])
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
        "plan_id": plan.id,
        "for_date": for_date,
        "changed": changed,
        "item_count": len(stored),
        # 判据①要「再读那份计划」能复跑，所以把读法一起交回去。
        "read_back": "cd Code && .venv/Scripts/python.exe -c \""
                     "import sys;sys.path.insert(0,'.');from plobi.agenda import store;"
                     f"c=store.connect(None);p=store.get_daily_plan(c,'{for_date}');"
                     "print([(i.title,str(i.start_at)[11:16],str(i.end_at)[11:16]) "
                     "for i in store.list_plan_items(c,p.id)])\"",
    }


def _preserve_length(item: PlanItem, start: datetime) -> datetime:
    """没给新结束时间 ⇒ 沿用这块原来的时长（挪钟点≠改长度）。"""
    prev_start, prev_end = _dt(item.start_at), _dt(item.end_at)
    if prev_start is None or prev_end is None:
        return start + timedelta(hours=1)
    return start + (prev_end - prev_start)


def _anchors_for(conn: Any, day: str) -> list[dict]:
    """作息锚点（睡眠／三餐）——校验器拿它们当「不许被挤掉」的那几条。"""
    kept, _yielded, _moved = planning.place_anchors_for_day(conn, day, [])
    return planning.kept_anchors_as_dicts(kept)


# --------------------------------------------------------------------------- #
# ② 生活块（R-106）：真源是 routine_templates，计划行只是它的投影
# --------------------------------------------------------------------------- #

def _routine_template(conn: Any, item: PlanItem):
    """这一行是不是**生活块**（作息锚点落进计划的那几行）；是就回它的模板。

    生活块的真源在 `routine_templates`，不在 `plan_items`——只改计划行等于造第二个真源，
    明晚 20:00 一跑就被抹掉（裁定 94／151：一条事实一个地方）。
    """
    if str(item.event_id or "").startswith("routine:"):
        tid = str((item.evidence or {}).get("template_id") or "").strip()
        return store.get_routine_template(conn, tid or str(item.event_id).split(":", 1)[1])
    evidence = item.evidence or {}
    if str(evidence.get("kind") or "") == planning.ROUTINE_EVIDENCE_KIND:
        return store.get_routine_template(conn, str(evidence.get("template_id") or ""))
    return None


def _anchor_rows(conn: Any, day: str, override: Optional[Any] = None) -> list[dict]:
    """当天启用作息锚点的契约字典。`override` = 「假如这条改成那样」的假想模板。"""
    rows: list[dict] = []
    for template, start, end in planning.enabled_anchors_for_day(conn, day):
        cand = override if (override is not None and template.id == override.id) else template
        if cand is not template:
            start, end = planning.anchor_span(cand, day)
        rows.append({
            "id": f"routine:{cand.id}", "template_id": cand.id, "title": cand.title,
            "start_at": start.isoformat(), "end_at": end.isoformat(),
        })
    return rows


def _sleep_spans(conn: Any, day: str, override: Optional[Any] = None) -> list[tuple[datetime, datetime]]:
    """清醒窗要用的那几段睡眠——同样支持假想值（挪睡眠块时窗口自己也跟着变）。"""
    spans: list[tuple[datetime, datetime]] = []
    for probe in (llm_planning._shift_day(day, -1), day):
        for template, start, end in planning.enabled_anchors_for_day(conn, probe):
            if llm_planning._is_sleep(template):
                if override is not None and template.id == override.id:
                    start, end = planning.anchor_span(override, probe)
                spans.append((start, end))
    return spans


def _other_rows(conn: Any, day: str) -> list[dict]:
    """这一天的**非作息**行：有计划取计划里那些；没计划取当天事件。"""
    plan = store.get_daily_plan(conn, day)
    if plan is not None:
        rows = [item.to_dict() for item in store.list_plan_items(conn, plan.id)
                if not str(item.event_id or "").startswith("routine:")]
        if rows or plan.status in ("pending", "confirmed"):
            return rows
    return [
        {"id": event.id, "title": event.title, "start_at": event.start_at, "end_at": event.end_at}
        for event in (store.list_events(
            conn, start_from=f"{day}T00:00:00", start_to=f"{day}T23:59:59") or [])
    ]


def _move_life_block(
    conn: Any,
    day: str,
    *,
    template: Any,
    start: datetime,
    end: Optional[datetime],
    by: str,
    reason: str,
) -> dict[str, Any]:
    """改**模板**（真源），再把这一天的那一行跟着走。改不动就一个字都不写。

    这条改的是「以后每天」的 standing 规则，不是明天一天的临时偏移——`standing` 那一格
    就是给面上说的用的，不许让用户以为只挪了明天一次。
    """
    new_start = start.strftime("%H:%M")
    if end is None:
        cur_start, cur_end = planning.anchor_span(template, day)
        end = start + (cur_end - cur_start)
    new_end = end.strftime("%H:%M")

    # 弹性窗是人授权过的（WP-MEALS-FLEX）：越过窗就不动它，要连窗一起改由用户自己说。
    if template.window_start and template.window_end:
        win_from = planning._hhmm_on_day(day, template.window_start)
        win_to = planning._hhmm_on_day(day, template.window_end)
        if not (win_from <= start and end <= win_to):
            return {
                "ok": False,
                "reason": (
                    f"「{template.title}」是条有弹性窗的作息（{template.window_start}–"
                    f"{template.window_end} 里任选），你要的 {new_start}–{new_end} 在窗外面。"
                    f"我不替你改窗——要连窗一起改，说一句「把「{template.title}」的窗也改成…」。"
                    "作息模板和计划都没动。"
                ),
            }

    candidate = replace(template, start_time=new_start, end_time=new_end)
    anchors = _anchor_rows(conn, day, override=candidate)
    waking = llm_planning._waking_from_spans(_sleep_spans(conn, day, override=candidate), day)
    problems = llm_planning.validate_proposal(
        anchors + _other_rows(conn, day), anchors=anchors,
        waking=(datetime.fromisoformat(waking[0]), datetime.fromisoformat(waking[1])),
    )
    if problems:
        return {
            "ok": False,
            "reason": f"「{template.title}」挪不过去：" + "；".join(problems[:3]) + "。作息模板和计划都没动。",
            "violations": problems,
        }

    kwargs: dict[str, Any] = {}
    if template.window_start and template.window_end and template.duration_min:
        # 窗三件套要么全传要么全不传（半套 store 直接拒）；不传 = 保持原值。
        kwargs = {"window_start": template.window_start, "window_end": template.window_end,
                  "duration_min": template.duration_min}
    store.upsert_routine_template(
        conn,
        template_id=template.id,
        title=template.title,
        start_time=new_start,
        end_time=new_end,
        weekdays=list(template.weekdays),
        enabled=True,
        **kwargs,
    )
    changed = {
        "title": template.title,
        "from": f"{template.start_time}-{template.end_time}",
        "to": f"{new_start}-{new_end}",
        "start_at": start.isoformat(),
        "end_at": end.isoformat(),
        "by": by,
        "reason": reason,
    }
    synced = _sync_routine_row(conn, day, template.id, changed)
    return {
        "ok": True,
        "life_block": True,
        "standing": True,
        "for_date": day,
        "changed": changed,
        "synced_plan_row": synced,
    }


def _sync_routine_row(conn: Any, day: str, template_id: str, changed: dict) -> bool:
    """把这一天的那条作息行跟着模板走。这一天还没生成计划就什么都不做（明晚自会生成）。"""
    plan = store.get_daily_plan(conn, day)
    if plan is None:
        return False
    span = None
    for template, start, end in planning.enabled_anchors_for_day(conn, day):
        if template.id == template_id:
            span = (start, end)
            break
    if span is None:
        return False

    rows: list[dict] = []
    hit = False
    for item in store.list_plan_items(conn, plan.id):
        row = item.to_dict()
        if str(row.get("event_id")) == f"routine:{template_id}":
            hit = True
            row["start_at"] = span[0].isoformat()
            row["end_at"] = span[1].isoformat()
            evidence = dict(row.get("evidence") or {})
            evidence.update({"edited_by": changed["by"],
                             "edited_at": datetime.now().isoformat(timespec="seconds"),
                             "moved_from": changed["from"],
                             "edit_reason": changed["reason"]})
            row["evidence"] = evidence
        rows.append(row)
    if not hit:
        # 这条作息当天被让位了（没写成行）——现在它有位置了就把行补上，当天面上看得见。
        rows.append({
            "id": f"pit_{template_id}",
            "plan_id": plan.id,
            "event_id": f"routine:{template_id}",
            "title": changed["title"],
            "start_at": span[0].isoformat(),
            "end_at": span[1].isoformat(),
            "kind": "task",
            "evidence": {"kind": planning.ROUTINE_EVIDENCE_KIND, "template_id": template_id,
                         "edited_by": changed["by"], "edit_reason": changed["reason"]},
        })
    rows.sort(key=lambda row: str(row.get("start_at") or ""))
    for index, row in enumerate(rows):
        row["sort_order"] = index
    store.replace_plan_items(conn, plan.id, [PlanItem(**row) for row in rows])
    return True


def add_life_block(
    conn: Any,
    day: str,
    *,
    title: str,
    start_at: str,
    end_at: Optional[str] = None,
    by: str,
    reason: str = "",
) -> dict[str, Any]:
    """用户自己加一条生活块（裁定 148「生活项要能自己加」）。写进**模板**，不是往计划里塞一行。

    同一张嘴、同一道闸：新块与已有作息／事件撞了就一句人话加零写入；同名块已存在就让他
    说清楚是要改那条（`move_block`），不静默并出第二条同名的。
    """
    title = str(title or "").strip()
    if not title:
        return {"ok": False, "reason": "这条生活块没名字，我不写。"}
    existing = [t for t in store.list_routine_templates(conn) if t.title == title]
    if existing:
        return {
            "ok": False,
            "reason": f"「{title}」已经是一条作息了（{existing[0].start_time}–{existing[0].end_time}）。"
                      f"要改钟点说一句「把「{title}」挪到几点」就行，我不再建第二条同名的。",
        }
    start, end, error = _read_span(start_at, end_at)
    if error:
        return {"ok": False, "reason": error}
    if end is None:
        return {"ok": False, "reason": f"新块「{title}」到几点结束？我不替它编时长。"}

    anchors = _anchor_rows(conn, day) + [{
        "id": "routine:new", "template_id": "new", "title": title,
        "start_at": start.isoformat(), "end_at": end.isoformat(),
    }]
    waking = llm_planning._waking_window(conn, day)
    problems = llm_planning.validate_proposal(
        anchors + _other_rows(conn, day), anchors=anchors,
        waking=(datetime.fromisoformat(waking[0]), datetime.fromisoformat(waking[1])),
    )
    if problems:
        return {
            "ok": False,
            "reason": f"「{title}」这个时段放不下：" + "；".join(problems[:3]) + "。什么都没写。",
            "violations": problems,
        }

    template = store.upsert_routine_template(
        conn, template_id=f"life-{datetime.now().strftime('%Y%m%d%H%M%S')}",
        title=title, start_time=start.strftime("%H:%M"), end_time=end.strftime("%H:%M"),
        weekdays=[], enabled=True,
    )
    changed = {"title": title, "from": "（原来没有这条）",
               "to": f"{template.start_time}-{template.end_time}",
               "start_at": start.isoformat(), "end_at": end.isoformat(),
               "by": by, "reason": reason}
    synced = _sync_routine_row(conn, day, template.id, changed)
    return {
        "ok": True,
        "life_block": True,
        "added": True,
        "standing": True,
        "template_id": template.id,
        "for_date": day,
        "changed": changed,
        "synced_plan_row": synced,
    }
