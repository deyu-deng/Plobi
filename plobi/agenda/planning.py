"""Nightly next-day planner: read events, mark overlaps, write an evidenced plan.

Zero LLM. No commute/homework invention. Every ``plan_items`` row must carry
evidence (store refuses empty). Empty windows write an honest empty plan.
Confirmed plans are not overwritten. See Docs/DESIGN-M2-PLANNING.md §1–2.
"""

from __future__ import annotations

from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any, Iterable, Optional

from . import store
from .store import AgendaValidationError, DailyPlan, Event, PlanItem, RoutineTemplate

EMPTY_PLAN_SUMMARY = "明天没有日程。今夜计划为空，不是没跑。"
EMPTY_PLAN_PUSH = "明天没有日程，计划为空"
_MESSAGE_EVIDENCE_KEYS = ("msg_id", "talker", "sent_at", "snippet")

# C4: anchor instances carry this marker in plan_items.evidence — never in the
# events vocabulary (meeting/ddl/class/task stays frozen).
ROUTINE_EVIDENCE_KIND = "routine"

# C5 project pace blocks: deterministic rules only, zero LLM.
PROJECT_BLOCK_EVIDENCE_KIND = "project_block"
MAX_BLOCK_MINUTES = 120  # 每块 ≤2h
MIN_BLOCK_MINUTES = 30  # 不足 30 分钟的零头空窗不排（无意义碎片）
DDL_LOOKAHEAD_DAYS = 14  # DDL 临近优先的排序窗口


def _as_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        moment = value
    else:
        moment = datetime.fromisoformat(str(value))
    if moment.tzinfo is not None:
        moment = moment.replace(tzinfo=None)
    return moment.replace(microsecond=0)


def _as_dt_opt(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    return _as_dt(value)


def intervals_overlap(a_start: Any, a_end: Any, b_start: Any, b_end: Any) -> bool:
    """True when two events collide. Half-open [start, end); missing end is a point."""
    a_s = _as_dt(a_start)
    b_s = _as_dt(b_start)
    a_e = _as_dt_opt(a_end)
    b_e = _as_dt_opt(b_end)

    if a_e is None and b_e is None:
        return a_s == b_s
    if a_e is None:
        return b_s <= a_s < b_e
    if b_e is None:
        return a_s <= b_s < a_e
    return a_s < b_e and b_s < a_e


def _as_for_date(value: Any) -> str:
    if value is None:
        return (datetime.now().date() + timedelta(days=1)).isoformat()
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    day = str(value).strip()
    if not day:
        raise AgendaValidationError("for_date is required")
    return day[:10]


def _day_window(day: str) -> tuple[datetime, datetime]:
    parsed = date.fromisoformat(day)
    start = datetime.combine(parsed, datetime.min.time())
    end = datetime.combine(parsed, datetime.max.time()).replace(microsecond=0)
    return start, end


def _item_evidence(event: Event) -> dict:
    payload: dict[str, Any] = {"kind": "event", "event_id": event.id}
    extra = event.evidence if isinstance(event.evidence, dict) else {}
    for key in _MESSAGE_EVIDENCE_KEYS:
        if extra.get(key) is not None:
            payload[key] = extra[key]
    return payload


def _conflict_map(events: list[Event]) -> dict[str, list[str]]:
    mapping: dict[str, list[str]] = {event.id: [] for event in events}
    for index, left in enumerate(events):
        for right in events[index + 1 :]:
            if intervals_overlap(left.start_at, left.end_at, right.start_at, right.end_at):
                mapping[left.id].append(right.id)
                mapping[right.id].append(left.id)
    return mapping


# --- routine anchors (C4) -----------------------------------------------------


def _hhmm_on(day: str, hhmm: str) -> datetime:
    hour, minute = (int(part) for part in hhmm.split(":"))
    return datetime.combine(date.fromisoformat(day), dt_time(hour, minute))


def anchor_span(template: RoutineTemplate, day: str) -> tuple[datetime, datetime]:
    """Template instantiated on ``day``. ``end < start`` ⇒ ends next day."""
    start = _hhmm_on(day, template.start_time)
    end = _hhmm_on(day, template.end_time)
    if end <= start:
        end += timedelta(days=1)
    return start, end


def enabled_anchors_for_day(conn: Any, day: str) -> list[tuple[RoutineTemplate, datetime, datetime]]:
    """Enabled templates whose weekday matches ``day``, instantiated in order.

    没有模板就没有锚点——这里只读 ``routine_templates``，绝不凭空生成。
    """
    weekday = date.fromisoformat(day).weekday()
    anchors: list[tuple[RoutineTemplate, datetime, datetime]] = []
    for template in store.list_routine_templates(conn, enabled_only=True):
        if template.weekdays and weekday not in template.weekdays:
            continue
        start, end = anchor_span(template, day)
        anchors.append((template, start, end))
    anchors.sort(key=lambda entry: (entry[1], entry[0].id))
    return anchors


def _is_flexible(template: RoutineTemplate) -> bool:
    """窗三件套齐 = 弹性锚点；否则钉死（重叠即让位，不滑）。"""
    return bool(template.window_start and template.window_end and template.duration_min)


def _anchor_conflicts_with(start: datetime, end: datetime, events: list[Event]) -> list[str]:
    return sorted(
        event.id
        for event in events
        if intervals_overlap(start, end, event.start_at, event.end_at)
    )


def _first_blocker_title(
    start: datetime, end: datetime, events: list[Event], placed: list[tuple]
) -> str:
    """首选段撞上的第一个占用者（无 end_at 的 DDL 不占窗，与 C5 一致）。"""
    for event in sorted(events, key=lambda e: e.start_at):
        if not event.end_at:
            continue
        if intervals_overlap(start, end, event.start_at, event.end_at):
            return event.title
    for _template, other_start, other_end in placed:
        if intervals_overlap(start, end, other_start, other_end):
            return "已排定的安排"
    return "日程"


def _place_flexible_anchor(
    conn: Any,
    day: str,
    template: RoutineTemplate,
    events: list[Event],
    placed: list[tuple[RoutineTemplate, datetime, datetime]],
) -> Optional[tuple[RoutineTemplate, datetime, datetime]]:
    """弹性落位：① 首选 → ② 窗内第一段够长的空档 → ③ None（让位）。

    占用集 = 当天 timed events（无 ``end_at`` 的 DDL 不占窗）+ 已落下的锚点
    （含先落的钉死锚点与前一餐）+ 昨夜锚点跨午夜溢出。窗与时长是人授权的
    事实：只找空档、绝不编钟点，也不把时长补成 1 小时。
    """
    try:
        duration = timedelta(minutes=int(template.duration_min or 0))
        win_start = _hhmm_on(day, template.window_start)
        win_end = _hhmm_on(day, template.window_end)
    except (TypeError, ValueError):
        return None  # 坏窗按钉死处理（store 层已拒绝，这里只防御）
    if duration <= timedelta(0) or win_end <= win_start:
        return None

    window_from, window_to = _day_window(day)
    occupied = _occupied_intervals(conn, day, events, placed, window_from, window_to)

    pref_start = _hhmm_on(day, template.start_time)
    pref_end = _hhmm_on(day, template.end_time)
    if not any(
        intervals_overlap(pref_start, pref_end, o_start, o_end)
        for o_start, o_end in occupied
    ):
        return (template, pref_start, pref_end)

    for gap_start, gap_end in _gaps_in_window(occupied, win_start, win_end):
        if gap_end - gap_start >= duration:
            return (template, gap_start, gap_start + duration)
    return None


def _gaps_in_window(
    occupied: list[tuple[datetime, datetime]],
    window_start: datetime,
    window_end: datetime,
) -> list[tuple[datetime, datetime]]:
    """窗内空档（占用集先裁进窗），按时间先后。"""
    clipped = sorted(
        (max(start, window_start), min(end, window_end))
        for start, end in occupied
    )
    gaps: list[tuple[datetime, datetime]] = []
    cursor = window_start
    for start, end in clipped:
        if end <= start:
            continue
        if start > cursor:
            gaps.append((cursor, min(start, window_end)))
        cursor = max(cursor, end)
        if cursor >= window_end:
            break
    if cursor < window_end:
        gaps.append((cursor, window_end))
    return [(start, end) for start, end in gaps if end > start]


def _moved_sentence(template: RoutineTemplate, start: datetime, blocker: str) -> str:
    """「午餐因电工电子学让到 12:25」——只说落下的钟点，不编原因。"""
    return f"{template.title}因{blocker}让到 {start.strftime('%H:%M')}"


def _place_anchors(
    conn: Any,
    day: str,
    anchors: list[tuple[RoutineTemplate, datetime, datetime]],
    events: list[Event],
) -> tuple[list[tuple[RoutineTemplate, datetime, datetime]], list[dict], list[str]]:
    """钉死锚点先行（重叠让位），弹性锚点按序落位（WP-MEALS-FLEX）。

    Returns ``(kept, yielded, moved)``：``kept`` 供 C5 与 plan_items 使用；
    ``yielded`` 沿用原 dict 形状（conflicts_with 仍是事件 id）；``moved`` 是
    弹性挪位的一句话事实。后一餐把前一餐已落下的段当占用。
    """
    ordered = [a for a in anchors if not _is_flexible(a[0])] + [
        a for a in anchors if _is_flexible(a[0])
    ]
    kept: list[tuple[RoutineTemplate, datetime, datetime]] = []
    yielded: list[dict] = []
    moved: list[str] = []
    for template, start, end in ordered:
        if not _is_flexible(template):
            hits = _anchor_conflicts_with(start, end, events)
            if hits:
                yielded.append(
                    {
                        "template_id": template.id,
                        "title": template.title,
                        "start_at": start.isoformat(),
                        "end_at": end.isoformat(),
                        "conflicts_with": hits,
                    }
                )
            else:
                kept.append((template, start, end))
            continue

        slot = _place_flexible_anchor(conn, day, template, events, kept)
        if slot is None:
            yielded.append(
                {
                    "template_id": template.id,
                    "title": template.title,
                    "start_at": start.isoformat(),
                    "end_at": end.isoformat(),
                    "conflicts_with": _anchor_conflicts_with(start, end, events),
                }
            )
            continue
        kept.append(slot)
        if (slot[1], slot[2]) != (start, end):
            moved.append(
                _moved_sentence(
                    template,
                    slot[1],
                    _first_blocker_title(start, end, events, kept[:-1]),
                )
            )
    return kept, yielded, moved


def _anchor_item(
    template: RoutineTemplate, start: datetime, end: datetime, *, flex: bool = False
) -> dict:
    evidence: dict[str, Any] = {
        "kind": ROUTINE_EVIDENCE_KIND,
        "template_id": template.id,
    }
    if flex:
        evidence["flex"] = True  # 弹性挪过位：落下的钟点是人授权窗内的事实
    return {
        "event_id": f"routine:{template.id}",
        "title": template.title,
        "start_at": start.isoformat(),
        "end_at": end.isoformat(),
        "kind": "task",  # plan_items.kind stays in the event vocabulary
        "conflict_with": [],
        "evidence": evidence,
    }


# --- project pace blocks (C5) --------------------------------------------------


def paced_projects() -> list[tuple[Any, float]]:
    """``(entry, weekly_hours)`` for enabled role=l2_project entries with pace.

    pace 存在注册表（projects.yaml）而非 agenda.db：pace 是项目（agent 条目）
    的属性，与 provider/model 覆盖同类，注册表是项目清单唯一真源；agenda.db
    保持日程域内聚，不引入第二份项目身份副本。
    """
    from plobi.agents.registry import load_registry

    out: list[tuple[Any, float]] = []
    try:
        entries = load_registry().entries()
    except Exception:
        return out
    for entry in entries:
        if entry.role != "l2_project" or entry.weekly_hours is None:
            continue
        out.append((entry, entry.weekly_hours))
    out.sort(key=lambda pair: pair[0].name)
    return out


def _avoid_intervals_for_day(
    conn: Any,
    day: str,
    window_start: datetime,
    window_end: datetime,
) -> list[tuple[datetime, datetime]]:
    """WP-PLAN-PREFS：人确认过的 avoid 窗裁到当天日窗内。

    跨午夜窗（如 22:00–02:00）按「当天日窗起点 → end」「次日日窗起点 → 第二天
    end」两段展开；这里只关心**当天**，所以把跨午夜窗剪到当天的部分；次日由
    次日的生成自然处理。窗本身是 ``HH:MM`` 字符串，规划器日窗也是
    ``00:00–23:59:59``，拼装成 datetime 即可。
    """
    from plobi.agenda import store as _store

    out: list[tuple[datetime, datetime]] = []
    try:
        windows = _store.list_active_avoid_windows(conn)
    except Exception:  # 锁表 / 损坏等不挡规划
        return out
    for win in windows:
        try:
            start = _hhmm_on(day, str(win.get("start_time") or ""))
            end = _hhmm_on(day, str(win.get("end_time") or ""))
        except ValueError:
            continue
        if end <= start:
            continue
        clipped_start = max(start, window_start)
        clipped_end = min(end, window_end)
        if clipped_end > clipped_start:
            out.append((clipped_start, clipped_end))
    return out


def _occupied_intervals(
    conn: Any,
    day: str,
    events: list[Event],
    kept_anchors: list[tuple[RoutineTemplate, datetime, datetime]],
    window_start: datetime,
    window_end: datetime,
) -> list[tuple[datetime, datetime]]:
    """Everything blocking the day: timed events + anchors (incl. spillover).

    无 ``end_at`` 的事件（DDL 点）不占窗；前一天跨午夜锚点溢出的清晨段算占用。
    """
    occupied: list[tuple[datetime, datetime]] = []
    for event in events:
        if not event.end_at:
            continue
        start = max(_as_dt(event.start_at), window_start)
        end = min(_as_dt(event.end_at), window_end)
        if end > start:
            occupied.append((start, end))
    for _, start, end in kept_anchors:
        start = max(start, window_start)
        end = min(end, window_end)
        if end > start:
            occupied.append((start, end))
    prev_day = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
    for _, start, end in enabled_anchors_for_day(conn, prev_day):
        if end > window_start:  # 跨午夜溢出段
            occupied.append((window_start, min(end, window_end)))
    return occupied


def _free_windows(
    occupied: list[tuple[datetime, datetime]],
    window_start: datetime,
    window_end: datetime,
) -> list[list[datetime]]:
    """Chronological free gaps ≥ MIN_BLOCK_MINUTES, as mutable [start, end]."""
    clipped = sorted((s, e) for s, e in occupied if e > s)
    free: list[list[datetime]] = []
    cursor = window_start
    for start, end in clipped:
        if start > cursor:
            free.append([cursor, min(start, window_end)])
        cursor = max(cursor, end)
        if cursor >= window_end:
            break
    if cursor < window_end:
        free.append([cursor, window_end])
    return [gap for gap in free if gap[1] - gap[0] >= timedelta(minutes=MIN_BLOCK_MINUTES)]


def _next_ddl(conn: Any, entry_name: str, window_from: datetime) -> Optional[datetime]:
    """该项目的下一个 DDL 事件（kind=ddl 且标题含项目名），确定性启发式。

    events 表没有 project 外键，标题包含项目名是 MVP 能落地的确定性关联；
    匹配不到 = 无 DDL（排序垫底，按名称稳定）。
    """
    events = store.list_events(conn, start_from=window_from.isoformat())
    needle = entry_name.lower()
    best: Optional[datetime] = None
    for event in events:
        if event.kind != "ddl":
            continue
        if needle not in event.title.lower():
            continue
        moment = _as_dt(event.start_at)
        if best is None or moment < best:
            best = moment
    return best


def _project_block_items(
    conn: Any,
    day: str,
    events: list[Event],
    kept_anchors: list[tuple[RoutineTemplate, datetime, datetime]],
    window_start: datetime,
    window_end: datetime,
) -> tuple[list[dict], list[dict]]:
    """Fill free windows with project blocks: DDL-nearest first, ≤2h each,
    total ≤ weekly_hours/7. Overflow lands in ``unscheduled``.

    WP-PLAN-PREFS（裁定 34.1）：人确认过的 avoid 窗（[[start,end]]）对该窗
    视为占用——只挡**项目推进块**，**不挡**已确认事件 / 作息锚点 / 弹性餐
    （那三类按现有逻辑继续走）。所以 avoid 窗的区间**不**进 ``_occupied_
    intervals``（那是事件 + 锚点占用的入口），只在分块前临时合并进 ``free``
    候选集——避免以后改 ``_occupied_intervals`` 时把弹性餐挡掉。
    """
    paced = paced_projects()
    if not paced:
        return [], []

    occupied = _occupied_intervals(conn, day, events, kept_anchors, window_start, window_end)
    avoid_intervals = _avoid_intervals_for_day(
        conn, day, window_start, window_end
    )
    free = _free_windows(occupied + avoid_intervals, window_start, window_end)

    ddl_limit = window_start + timedelta(days=DDL_LOOKAHEAD_DAYS)

    def _sort_key(pair):
        ddl = _next_ddl(conn, pair[0].name, window_from=window_start)
        if ddl is not None and ddl > ddl_limit:
            ddl = None  # lookahead 之外的 DDL 视同无 DDL
        return (ddl is None, ddl or window_start, pair[0].name)

    ranked = sorted(paced, key=_sort_key)

    blocks: list[dict] = []
    unscheduled: list[dict] = []
    for entry, weekly_hours in ranked:
        budget = weekly_hours / 7.0 * 3600.0  # 秒
        for gap in free:
            if budget <= 0:
                break
            available = (gap[1] - gap[0]).total_seconds()
            if available <= 0:
                continue
            take = min(budget, available, MAX_BLOCK_MINUTES * 60.0)
            if take < MIN_BLOCK_MINUTES * 60.0:
                continue  # 剩余预算不足一个最小块，留给下一个项目/放弃
            start = gap[0]
            end = start + timedelta(seconds=take)
            blocks.append(
                {
                    "event_id": f"project:{entry.name}",
                    "title": f"推进 · {entry.name}",
                    "start_at": start.isoformat(),
                    "end_at": end.isoformat(),
                    "kind": "task",
                    "conflict_with": [],
                    "evidence": {
                        "kind": PROJECT_BLOCK_EVIDENCE_KIND,
                        "project_id": entry.name,
                        "pace": f"weekly_hours:{weekly_hours:g}",
                    },
                }
            )
            gap[0] = end  # 该空窗被消耗
            budget -= take
        if budget > 0:
            unscheduled.append(
                {
                    "project": entry.name,
                    "missing_hours": round(budget / 3600.0, 1),
                }
            )
    return blocks, unscheduled


def _pending_summary(event_count: int, conflict_count: int) -> str:
    if conflict_count:
        return f"明天有{event_count}项日程，其中{conflict_count}处时间重叠。"
    return f"明天有{event_count}项日程，时间无重叠。"


def _plan_summary(
    event_count: int,
    pair_count: int,
    kept_anchors: list,
    yielded: list[dict],
    blocks: Optional[list[dict]] = None,
    unscheduled: Optional[list[dict]] = None,
    moved: Optional[list[str]] = None,
) -> str:
    """One paragraph for L1 / DingTalk (L1 cost discipline unchanged)."""
    blocks = blocks or []
    unscheduled = unscheduled or []
    moved = moved or []
    if not event_count:
        # No events — anchors / blocks alone are still a real plan.
        parts: list[str] = []
        if kept_anchors:
            parts.append(f"按作息模板安排了{len(kept_anchors)}项锚点")
        if blocks:
            parts.append(f"排了{len(blocks)}项项目推进块")
        if parts:
            text = "明天没有日程，" + "，".join(parts) + "。"
        else:
            text = EMPTY_PLAN_SUMMARY
    else:
        text = _pending_summary(event_count, pair_count)
        if kept_anchors:
            text += f"含{len(kept_anchors)}项作息锚点。"
        if blocks:
            text += f"另排{len(blocks)}项项目推进块。"
    if yielded:
        titles = "、".join(str(entry.get("title") or "") for entry in yielded)
        text += f"{len(yielded)}项锚点（{titles}）因与日程冲突让位。"
    if moved:
        # 弹性锚点在窗内挪位是办成了的事，单独一句事实（用落下的钟点）。
        text += "；".join(moved) + "。"
    if unscheduled:
        misses = "；".join(
            f"{entry['project']}（缺{entry['missing_hours']:g}小时）"
            for entry in unscheduled
        )
        text += f"未能安排：{misses}。"
    return text


def format_evening_plan_text(plan: DailyPlan, *, summary: Optional[str] = None) -> str:
    """DingTalk body. Empty plans always include ``EMPTY_PLAN_PUSH``."""
    text = plan.summary if summary is None else summary
    lines = [f"[Plobi] 明日计划 {plan.for_date}", text]
    if plan.status == "empty" and EMPTY_PLAN_PUSH not in text:
        lines.append(EMPTY_PLAN_PUSH)
    elif plan.status == "pending" and plan.confirm_seq is not None:
        lines.append(f"回复：确认{plan.confirm_seq} / 忽略{plan.confirm_seq}")
    return "\n".join(lines)


def generate_evening_plan(
    for_date: Any = None,
    *,
    db_path: Path | str | None = None,
) -> DailyPlan:
    """Snapshot tomorrow (or ``for_date``) from events + routine anchors.

    Three-bucket model (DESIGN-M2-ROUTINE-ANCHORS-proposal §2): event items
    first, then user-authored routine anchors (C4). A pinned anchor (no
    window) overlapping any event yields — it is not written and is counted
    in ``conflict_count``. A flexible anchor (WP-MEALS-FLEX: window +
    duration, human-authored) first tries its preferred slot, else slides to
    the first gap in its window that fits ``duration_min``; only when the
    whole window is too busy does it yield and get counted.
    ``event_count == 0`` with no surviving anchors → ``status=empty``; anchors
    alone make a real (``pending``) plan — templates are user-authorised fact,
    not invention. A ``confirmed`` row is returned unchanged.
    """
    day = _as_for_date(for_date)
    window_from, window_to = _day_window(day)
    conn = store.connect(db_path)
    try:
        existing = store.get_daily_plan(conn, day)
        if existing is not None and existing.status == "confirmed":
            return existing

        events = store.list_events(conn, start_from=window_from, start_to=window_to)
        anchors = enabled_anchors_for_day(conn, day)
        kept_anchors, yielded, moved = _place_anchors(conn, day, anchors, events)
        blocks, unscheduled = _project_block_items(
            conn, day, events, kept_anchors, window_from, window_to
        )

        if not events and not kept_anchors and not blocks:
            plan = store.upsert_daily_plan(
                conn,
                for_date=day,
                status="empty",
                summary=EMPTY_PLAN_SUMMARY,
                conflict_count=0,
                event_count=0,
                confirm_seq=None,
                confirm_seq_at=None,
                evidence={
                    "kind": "empty_window",
                    "from": window_from.isoformat(),
                    "to": window_to.isoformat(),
                    "event_count": 0,
                },
            )
            store.replace_plan_items(conn, plan.id, [])
            stored = store.get_daily_plan(conn, day)
            assert stored is not None
            return stored

        conflicts = _conflict_map(events)
        pair_count = sum(len(ids) for ids in conflicts.values()) // 2
        # C4: a yielded anchor is a real conflict the user should see in the
        # header — the anchor gave way, so it cannot speak for itself.
        conflict_count = pair_count + len(yielded)
        seq = store.next_confirm_seq(conn)
        seq_at = store.now_iso()
        summary = _plan_summary(
            len(events), pair_count, kept_anchors, yielded, blocks, unscheduled, moved
        )
        evidence: Optional[dict] = None
        if kept_anchors or yielded or blocks or unscheduled:
            evidence = {
                "kind": "evening_plan",
                "anchor_count": len(kept_anchors),
                "block_count": len(blocks),
            }
            if yielded:
                evidence["yielded_anchors"] = yielded
            if moved:
                evidence["moved_anchors"] = moved
            if unscheduled:
                evidence["unscheduled"] = unscheduled
        plan = store.upsert_daily_plan(
            conn,
            for_date=day,
            status="pending",
            summary=summary,
            conflict_count=conflict_count,
            event_count=len(events),
            confirm_seq=seq,
            confirm_seq_at=seq_at,
            evidence=evidence,
        )
        items = []
        for index, event in enumerate(events):
            evidence = _item_evidence(event)
            if not evidence:
                raise AgendaValidationError("plan_items evidence is required")
            items.append(
                {
                    "event_id": event.id,
                    "title": event.title,
                    "start_at": event.start_at,
                    "end_at": event.end_at,
                    "kind": event.kind,
                    "sort_order": index,
                    "conflict_with": conflicts[event.id],
                    "evidence": evidence,
                }
            )
        for template, start, end in kept_anchors:
            flex = _is_flexible(template) and (start, end) != (
                _hhmm_on(day, template.start_time),
                _hhmm_on(day, template.end_time),
            )
            items.append(_anchor_item(template, start, end, flex=flex))
        items.extend(blocks)
        items.sort(key=lambda item: str(item["start_at"]))
        for index, item in enumerate(items):
            item["sort_order"] = index
        store.replace_plan_items(conn, plan.id, items)
        stored = store.get_daily_plan(conn, day)
        assert stored is not None
        return stored
    finally:
        conn.close()


def l1_plan_view(
    for_date: Any,
    current_event_ids: Iterable[str],
    *,
    db_path: Path | str | None = None,
) -> dict:
    """L1-safe plan header. Never returns ``plan_items``."""
    day = _as_for_date(for_date)
    current = {str(item) for item in current_event_ids}
    conn = store.connect(db_path)
    try:
        plan = store.get_daily_plan(conn, day)
        if plan is None:
            return {
                "status": "missing",
                "summary": "昨夜未生成计划",
                "for_date": day,
                "event_count": 0,
                "conflict_count": 0,
                "stale": False,
                "empty": False,
            }
        planned = {item.event_id for item in store.list_plan_items(conn, plan.id)}
        return {
            "status": plan.status,
            "summary": plan.summary,
            "for_date": plan.for_date,
            "event_count": plan.event_count,
            "conflict_count": plan.conflict_count,
            "stale": planned != current,
            "empty": plan.status == "empty",
        }
    finally:
        conn.close()


# --- WP-DAY-SURFACE: 白天「今天」也要看到饭/觉（与晚间计划同一管线） ---


def place_anchors_for_day(
    conn: Any,
    day: str,
    events: list[Event],
) -> tuple[list[tuple[RoutineTemplate, datetime, datetime]], list[dict], list[str]]:
    """``_place_anchors`` 的公开封装。晚间 / 日间同一份算法。

    Returns ``(kept, yielded, moved)``，与 :func:`_place_anchors` 一致；
    ``kept`` 已按 ``start_at`` 升序（来自 ``enabled_anchors_for_day``）。
    """
    anchors = enabled_anchors_for_day(conn, day)
    return _place_anchors(conn, day, anchors, events)


def kept_anchors_as_dicts(
    kept: list[tuple[RoutineTemplate, datetime, datetime]],
) -> list[dict]:
    """把 ``kept`` 落成 day 接口的契约字典（不带 conflict_with）。

    ``flex`` 表示这一锚点是**窗内挪过的**（首选段空 → 不打 flex）。
    """
    rows: list[dict] = []
    for template, start, end in kept:
        flex = _is_flexible(template) and (start, end) != (
            _hhmm_on_day(start.date().isoformat(), template.start_time),
            _hhmm_on_day(start.date().isoformat(), template.end_time),
        )
        rows.append(
            {
                "id": f"routine:{template.id}",
                "title": template.title,
                "start_at": start.isoformat(),
                "end_at": end.isoformat(),
                "template_id": template.id,
                "flex": flex,
            }
        )
    return rows


def _hhmm_on_day(day: str, hhmm: str) -> datetime:
    """``day`` 上的 HH:MM 实例——公开 anchor_span 子集。"""
    return _hhmm_on(day, hhmm)


# --- WP-DAY-SURFACE: 重叠判定 + plan_items 直读 ------------------------------


def compute_event_overlaps(events: list[Event]) -> tuple[dict[str, list[str]], list[dict]]:
    """两边都 ``end_at`` 且区间相交 → 互相 ``overlaps``，并各贡献一行 ``conflicts``。

    无 ``end_at`` 的点（DDL / 提醒）不算占满一小时，**不**制造冲突——与
    C5、WP-DAY-SURFACE 31.1 同口径。空 ``end_at`` 的事件因此 ``overlaps=[]``。
    """
    overlaps: dict[str, list[str]] = {event.id: [] for event in events}
    conflicts: list[dict] = []
    timed = [event for event in events if event.end_at]
    for index, left in enumerate(timed):
        for right in timed[index + 1 :]:
            if intervals_overlap(left.start_at, left.end_at, right.start_at, right.end_at):
                overlaps[left.id].append(right.id)
                overlaps[right.id].append(left.id)
                # 区间交点 = 双方最大开始 → 最小结束。
                start = max(left.start_at, right.start_at)
                end = min(left.end_at, right.end_at)
                conflicts.append(
                    {
                        "left_id": left.id,
                        "right_id": right.id,
                        "start_at": start,
                        "end_at": end,
                    }
                )
    # 无 end_at 事件单独确保 key 存在（空 list）。
    for event in events:
        overlaps.setdefault(event.id, [])
    return overlaps, conflicts


def plan_items_for_day(
    conn: Any, day: str
) -> tuple[Optional[DailyPlan], list[PlanItem]]:
    """当日 daily_plan（无则 None）+ plan_items 列表。"""
    plan = store.get_daily_plan(conn, day)
    if plan is None:
        return None, []
    return plan, store.list_plan_items(conn, plan.id)


def _dedupe_routine_plan_items(
    anchors: list[dict],
    plan_items: list[PlanItem],
) -> list[PlanItem]:
    """同一餐不出现两次（WP-MEAL-DEDUPE）。

    策略（选更少代码的）：anchors 保留现场落位（实时，反映今天 events 后的
    挪位结果），plan_items 里凡是 ``evidence.kind == "routine"`` 且与某
    anchor **同 template_id** 或 **时间重叠** → 丢掉。project_block 项
    一律保留。无 daily_plan 时上层 plan_items 已是 ``[]``，本函数无活干。
    """
    if not plan_items:
        return plan_items
    anchor_template_ids = {a.get("template_id") for a in anchors if a.get("template_id")}

    def _intervals_overlap(a_start: str, a_end: str, b_start: str, b_end: str) -> bool:
        # 半开 [start, end) 区间相交——与 intervals_overlap 一致。
        from datetime import datetime

        a_s, a_e = datetime.fromisoformat(a_start), datetime.fromisoformat(a_end)
        b_s, b_e = datetime.fromisoformat(b_start), datetime.fromisoformat(b_end)
        return a_s < b_e and b_s < a_e

    def _is_meal_anchor(plan_item: PlanItem) -> bool:
        evidence = plan_item.evidence or {}
        if evidence.get("kind") != "routine":
            return False
        template_id = evidence.get("template_id")
        # 1) 同 template_id → 重复
        if template_id in anchor_template_ids:
            return True
        # 2) 同名 + 时间重叠 → 也算重复（用户编辑了作息 + 旧 plan 残留）
        for anchor in anchors:
            if anchor.get("title") != plan_item.title:
                continue
            if _intervals_overlap(
                anchor["start_at"], anchor["end_at"], plan_item.start_at, plan_item.end_at
            ):
                return True
        return False

    return [item for item in plan_items if not _is_meal_anchor(item)]


def _avoid_windows_for_day(conn: Any, day: str) -> list[dict]:
    """当天 confirmed 的 avoid 窗（WP-MEAL-DEDUPE 顺手）。pending 不出现。"""
    try:
        from .store import list_active_avoid_windows
    except ImportError:
        return []
    rows = list_active_avoid_windows(conn)
    if not rows:
        return []
    out: list[dict] = []
    for row in rows:
        start_time = row.get("start_time")
        end_time = row.get("end_time")
        if not start_time or not end_time:
            continue
        # 接到目标日（避免 row 里只有 HH:MM，路径里要 ISO 形式才能被右栏直接用）。
        out.append(
            {
                "start_at": f"{day}T{start_time}:00",
                "end_at": f"{day}T{end_time}:00",
                "card_id": row.get("card_id"),
            }
        )
    return out


def day_surface(
    conn: Any,
    day: str,
    events: list[Event],
) -> dict:
    """白天看板用的「今天」快照：events（含 location/overlaps）+ anchors + 可选 plan_items + conflicts。

    与晚间 20:00 的 generate_evening_plan 不同：**不**写库、不要求已有计划；
    anchors 每次按当天 events 现场计算，DDL 点事件不占窗。

    WP-MEAL-DEDUPE：plan_items 已去掉与 anchor 同 template_id / 同名时间
    重叠的 routine 项——同一餐只在 anchors 出现一次（现场落位为真源）。新
    增 ``avoid_windows`` 字段带当天 confirmed avoid 窗（缺省 ``[]``）。
    """
    kept, _yielded, _moved = place_anchors_for_day(conn, day, events)
    plan, items = plan_items_for_day(conn, day)
    overlaps, conflicts = compute_event_overlaps(events)
    anchors_rows = kept_anchors_as_dicts(kept)
    items_dedup = _dedupe_routine_plan_items(anchors_rows, items)
    return {
        "date": day,
        "kept_anchors": anchors_rows,
        "plan_items": [_plan_item_dict(item) for item in items_dedup],
        "overlaps": overlaps,
        "conflicts": conflicts,
        "plan_status": plan.status if plan is not None else None,
        "avoid_windows": _avoid_windows_for_day(conn, day),
    }


def _plan_item_dict(item: PlanItem) -> dict:
    return {
        "id": item.id,
        "title": item.title,
        "start_at": item.start_at,
        "end_at": item.end_at,
        "kind": item.kind,
        "evidence": item.evidence,
    }
