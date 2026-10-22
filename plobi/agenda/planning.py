"""Nightly next-day planner: read events, mark overlaps, write an evidenced plan.

Zero LLM. No commute/homework invention. Every ``plan_items`` row must carry
evidence (store refuses empty). Empty windows write an honest empty plan.
Confirmed plans are not overwritten. See Docs/DESIGN-M2-PLANNING.md §1–2.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from . import store
from .store import AgendaValidationError, DailyPlan, Event, PlanItem

EMPTY_PLAN_SUMMARY = "明天没有日程。今夜计划为空，不是没跑。"
EMPTY_PLAN_PUSH = "明天没有日程，计划为空"
_MESSAGE_EVIDENCE_KEYS = ("msg_id", "talker", "sent_at", "snippet")

# C5 project pace blocks: deterministic rules only, zero LLM.
PROJECT_BLOCK_EVIDENCE_KIND = "project_block"
# 无主 DDL（没挂上任何 pace 项目的截止事件）自己拿一块——用户那句
# 「未提交作业要排进写作业时间」要的就是这块。它不扩 events 词表（kind 仍是 task），
# 只在 plan_items.evidence.kind 上标出来，与 project_block 同一手法。
DDL_BLOCK_EVIDENCE_KIND = "ddl_block"
MAX_BLOCK_MINUTES = 120  # 每块 ≤2h
MIN_BLOCK_MINUTES = 30  # 不足 30 分钟的零头空窗不排（无意义碎片）
DDL_LOOKAHEAD_DAYS = 14  # DDL 临近优先的排序窗口
# 裁定 121 一（用户 10-11 拍）：作业块**当日时长合计**上限，单位分钟。用户给的数，不是我编的。
DDL_DAILY_BUDGET_MINUTES = 240


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


def _ddl_candidates(conn: Any, window_from: datetime, window_to: datetime) -> list[Event]:
    """窗口内的截止事件，按到期时刻排序。机器采的与手写的走同一条口径。"""
    events = [
        event
        for event in store.list_events(
            conn, start_from=window_from.isoformat(), start_to=window_to.isoformat(),
            include_completed=False,      # WP-PLAN-COMPLETION-ACK：承认完成的不再进作业池
        )
        if event.kind == "ddl"
    ]
    events.sort(key=lambda event: (_as_dt(event.start_at), event.title))
    return events


def _matches_project(event: Event, entry_name: str, *, other_names: Iterable[str] = (),
                     course_ids: Iterable[str] = ()) -> bool:
    """这条 DDL 属不属于该项目 —— 两条确定性关联口径，都不许按信源名字分支。

    1. 标题含项目名（C5 原口径：events 表没有 project 外键，标题是唯一手写锚点）。
    2. ``evidence`` 里的**归属字段**等于项目名：采集来的 DDL（课表作业那类）标题是站方给的
       课件名，项目名压根不在里面 ⇒ 只认口径 1 就永远挂不上。这里读的是通用字段名
       ``course``/``project``（哪个信源给的都一样），**看的不是这条数据由哪个信源采来**（判据 4）。

    ``other_names`` 是名册其余条目名。简称仍然可以命中全称（名册里的项目名常是简称），
    **但名册里已经有人跟这条归属字段全等时，简称不许再来抢**：2026-10-15 在这台真库上实测，
    「电工电子学」靠子串把「电工电子学实验」的 2 条作业也认领走（``evt_1842479535ce`` /
    ``evt_3fd8c0dedcf3``），于是那两门课的「下一个 DDL」是同一块数据 —— 145①(b)
    「某门课的作业必须派给那门课」在这形状下必然挂错课。查询侧
    （:mod:`plobi.agenda.course_records` 的 ``_match_class_titles``）用的是同一条**单向**规则
    （全等存在就不许再拿前缀去够别的课），两侧共用一份判据，不各说一套。
    """
    needle = course_key(entry_name)
    if not needle:
        return False
    if needle in course_key(event.title):
        return True
    extra = event.evidence if isinstance(event.evidence, dict) else {}
    # `other_names` 留着给调用方（名册参照），归属这一侧不再需要它：子串匹配已经被
    # `matches_course` 的「全等 + ID 级」替掉，简称抢不走别人的课。
    return matches_course(event, needle, course_ids=course_ids,
                          evidence=extra, other_names=other_names)


_COURSE_SUFFIX = re.compile(r"(?:19|20)\d{2}.*$")
#: 课程文件夹那一层的名字把学期写在**前面**：`2026-2027秋冬-大学物理（甲）II`。
#: 现核这台 8 门课的 `project_path` 末段全是这一形 ⇒ 只截后缀不截前缀，
#: 「教材在哪个文件夹」与「作业归哪门课」两头永远对不上（架构师 10-16 点名的串课第二形）。
_SEMESTER_PREFIX = re.compile(r"^\d{4}\s*-\s*\d{4}\s*[春夏秋冬]*\s*[-—_]")

#: 罗马数字**单字符**（U+2160–U+216B）⇒ ASCII 拼法。名册里同一门课同时存在两种写法：
#: `project_path` 末段是 `大学物理（甲）II`（两个字母 I），`display_name` 是 `大学物理（甲）Ⅱ`（U+2161）。
#: 不折这一层，真源里它们是**两个 key**——站方给的作业标题按一种写法来、名册按另一种写法存，
#: 于是"认不上"退成无主、或"抢认"退成串到隔壁课。架构师 10-16 现核点名要求收在这里。
_ROMAN_TO_ASCII = {
    "Ⅰ": "I", "Ⅱ": "II", "Ⅲ": "III", "Ⅳ": "IV", "Ⅴ": "V", "Ⅵ": "VI", "Ⅶ": "VII",
    "Ⅷ": "VIII", "Ⅸ": "IX", "Ⅹ": "X", "Ⅺ": "XI", "Ⅻ": "XII",
    "ⅰ": "I", "ⅱ": "II", "ⅲ": "III", "ⅳ": "IV", "ⅴ": "V", "ⅵ": "VI", "ⅶ": "VII",
    "ⅷ": "VIII", "ⅸ": "IX", "ⅹ": "X", "ⅺ": "XI", "ⅻ": "XII",
}


def course_key(text: str) -> str:
    """课名比较用的**唯一**归一化：罗马数字字符折成 ASCII、去首尾空白、小写。

    只折**字符等值**这一层——不做子串、不做模糊、不截后缀（那是 `course_alias` 的活，
    它只在别名全等那一步出现）。归一化只用来比较，不改写存进库里的原文。
    """
    return "".join(_ROMAN_TO_ASCII.get(ch, ch) for ch in (text or "")).strip().lower()


def course_alias(name: str) -> str:
    """名册全名 / 课程文件夹末名 ⇒ 课表与站方用的短名：**两头都截，仍只做全等比较**。

    - 后缀那段：从年份起整段截掉（年份 / 周几 / 节次 / 班号都在这段里），
      `大学物理实验2026周四345` ⇒ `大学物理实验`。
    - 前缀那段：学期整段截掉，`2026-2027秋冬-大学物理（甲）II` ⇒ `大学物理（甲）II`
      ——这台 8 门课的 `project_path` 末段全是这一形（教材就在那些文件夹里）。
    **别名只做全等比较，不许当子串用**——子串就是本刀要杀的串台根因。
    两头都没有时原样返回。
    """
    text = (name or "").strip()
    text = _SEMESTER_PREFIX.sub("", text).strip()

    return _COURSE_SUFFIX.sub("", text).strip() or text


def course_id_map(conn: Any) -> dict[str, tuple[str, ...]]:
    """名册课名 ⇒ 该课全部的 `evidence.course_id`。**桥只有全等，之后一律按 ID 走**。

    不在名册里另存一份 id：那是第二真源（站方改课名或合班时两边打架）。
    一次窗口查询建好整张表，供这一轮排程复用，不在每个事件上再问一次。
    """
    out: dict[str, set[str]] = {}
    for row in conn.execute(
        "SELECT json_extract(evidence, '$.course') AS course, json_extract(evidence, '$.course_id') AS cid"
        " FROM events WHERE kind = 'ddl' AND course IS NOT NULL AND course <> ''"
    ):
        name = course_key(str(row["course"] or ""))
        cid = str(row["cid"] or "").strip()
        if name and cid:
            out.setdefault(name, set()).add(cid)

    return {name: tuple(sorted(ids)) for name, ids in out.items()}


def matches_course(event: Event, course_name: str, *, course_ids: Iterable[str] = (),
                   evidence: Optional[dict] = None,
                   other_names: Iterable[str] = ()) -> bool:
    """**两侧归属规则收在这一个函数里**（`WP-COURSE-MATCH-ONE-SOURCE`），两条都不许退回子串。

    - 作业（ddl）：先按 `evidence.course_id` 等值（ID 级）；名册与站方之间那座桥只能是
      `evidence.course` 与条目名**全等」。治理线在真库上实测：「电工电子学」靠子串把
      「电工电子学实验」的 2 条作业也认领走（`evt_1842479535ce` / `evt_3fd8c0dedcf3`）——
      就是这个函数要挡住的现场。
    - 课表（class）：按**标题**全等，或别名全等（见 `course_alias`）。
      ⚠️ **不能用 `evidence.calendar_name` 当归属**：现核那 240 条的 `calendar_name` 全是
      `浙大课程表-2026-2027秋冬`——那是**那只日历的名字**，一门课都不指。按它全等会把 240 条
      一起挂给每一门课，比子串严重一个量级。判据那句「课表按 `calendar_name` 全等」按这条读数更正。

    `other_names` 只做一件事：站方这条归属已经有名册里的人**全等**认领时，简称与别名都不许抢。
    """
    needle = course_key(course_name)
    if not needle:
        return False

    extra = event.evidence if evidence is None else evidence
    extra = extra if isinstance(extra, dict) else {}

    cid = str(extra.get("course_id") or "").strip()
    if cid and cid in {str(x).strip() for x in course_ids}:
        return True

    owned = course_key(str(extra.get("course") or ""))
    if owned and owned == needle:
        return True
    if owned and owned in {course_key(str(n)) for n in other_names if str(n).strip()}:
        # 名册里已经有人跟这条归属**全等**——简称、别名、子串都不许再来抢。
        # 这一格就是真库上那 2 条作业被「电工电子学」认领走的现场（`16a06f71` 的参照，
        # 本刀把它挪进共用函数里，并排在子串之前——顺序就是判据）。
        return False

    title = course_key(event.title)
    if title == needle:
        return True
    if owned and needle in owned:
        # 兜底：没人全等认领这条归属时，简称仍然够得到全称（C5 那档口径，
        # 现有两条测试钉着：名册项目「线性」对站方课名「线性代数」）。
        # 它**不再是课程串台的路径**——上面那一格先把有人认领的挡掉了。
        return True

    return bool(title) and title == course_key(course_alias(needle))


def _next_ddl(conn: Any, entry_name: str, window_from: datetime,
              *, other_names: Iterable[str] = (), course_ids: Iterable[str] = ()) -> Optional[datetime]:
    """该项目的下一个 DDL 事件，确定性启发式。

    匹配不到 = 无 DDL（排序垫底，按名称稳定）。
    """
    ddl_limit = window_from + timedelta(days=DDL_LOOKAHEAD_DAYS)
    moments = [
        _as_dt(event.start_at)
        for event in _ddl_candidates(conn, window_from, ddl_limit)
        if _matches_project(event, entry_name, other_names=other_names, course_ids=course_ids)
    ]
    return min(moments) if moments else None


NO_PLANNER_NOTE = (
    "（没有排程入口：这一版一个项目块/作业块都没排，面上那些就是当天已有的日程与作息。"
    "不是排满了，也不是排砸了——要让 L1 排，就得把 planner 接上。）"
)


def _l1_blocks(
    conn: Any,
    day: str,
    events: list[Event],
    window_from: datetime,
    window_to: datetime,
    *,
    planner: Optional[Callable[[Any, str], tuple[list[dict], str]]] = None,
) -> tuple[list[dict], list, list, str]:
    """项目块 / 作业块交给 L1（裁定 149）。返回 ``(blocks, unscheduled, deferred, note)``。

    `planner(conn, day) -> (blocks, note)` 是**显式**注入点：生产由秘书那一发
    :func:`plobi.agenda.llm_planning.blocks_from_l1`（材料口 + 提示 + 边界校验 + 退回重排）；
    不传就是没排，**绝不默认去打一发模型**（9d7e4e58 那颗雷：默认回退到真外部调用，
    一跑就花钱、同一条测试还跟着路由通不通漂）。测试要块就自己给一个假的。

    **这里没有第二条路**：那套把剩余空窗按日预算分给每个项目 / 每条作业的均分分配函数，
    连同它只服务自己的私有 helper 与"没人接线时兜一版均分"那条兜底，已随裁定 149／163③
    整体删除。为什么不留兜底：静默换回一版"看起来排满了"的均分，就是把「没法执行」修成
    「更难看的没法执行」，而用户明说过「一定要 LLM 参与。不要代码自动生成」「不要用脚本排程」。
    排不出来 ⇒ 一个块不写，并在面上照实说没排（见 :data:`NO_PLANNER_NOTE`）。

    第 2、3 位（``unscheduled`` / ``deferred``）**恒为空**：那是均分自己算出来的"欠了几小时 /
    排不下的作业"，换成 L1 之后只有 planner 本人知道自己放弃了什么，而它的返回形状就是
    ``(blocks, note)`` 两位（与 :func:`plobi.agenda.llm_planning.blocks_from_l1` 一致）。
    留这两位是为了 ``_plan_summary`` 那两句不必改口径；不许拿它们造第二套分配数学。
    """
    if planner is None:
        # 没接线就照实没排。这一格会拼进 daily_plans.summary，面上看得见"这不是 L1 排的"。
        return [], [], [], NO_PLANNER_NOTE
    try:
        blocks, note = planner(conn, day)
    except Exception as exc:  # 通道炸了也不写假块
        return [], [], [], f"L1 那一轮没排成（{type(exc).__name__}: {str(exc)[:120]}），今天没有项目块"
    return list(blocks), [], [], note


def _pending_summary(event_count: int, conflict_count: int) -> str:
    if conflict_count:
        return f"明天有{event_count}项日程，其中{conflict_count}处时间重叠。"
    return f"明天有{event_count}项日程，时间无重叠。"


def _plan_summary(
    event_count: int,
    pair_count: int,
    blocks: Optional[list[dict]] = None,
    unscheduled: Optional[list[dict]] = None,
    deferred_ddl: Optional[list[dict]] = None,
) -> str:
    """One paragraph for L1 / DingTalk (L1 cost discipline unchanged)."""
    blocks = blocks or []
    unscheduled = unscheduled or []
    deferred_ddl = deferred_ddl or []
    # 是"到上限了"还是"没空窗了"：前者是政策，后者是当天太满，两句话不一样。
    ddl_minutes_used = sum(
        (
            datetime.fromisoformat(item["end_at"]) - datetime.fromisoformat(item["start_at"])
        ).total_seconds()
        / 60.0
        for item in blocks
        if (item.get("evidence") or {}).get("kind") == DDL_BLOCK_EVIDENCE_KIND
    )
    ddl_cap_hit = deferred_ddl and ddl_minutes_used >= DDL_DAILY_BUDGET_MINUTES - MIN_BLOCK_MINUTES
    # 两句要各说各的数：把作业块报成"项目推进块"是谎，反过来也一样。
    project_block_count = sum(
        1 for item in blocks if (item.get("evidence") or {}).get("kind") == PROJECT_BLOCK_EVIDENCE_KIND
    )
    ddl_block_count = sum(
        1 for item in blocks if (item.get("evidence") or {}).get("kind") == DDL_BLOCK_EVIDENCE_KIND
    )
    block_phrase = "、".join(
        part
        for part in (
            f"{project_block_count}项项目推进块" if project_block_count else "",
            f"{ddl_block_count}项作业块" if ddl_block_count else "",
        )
        if part
    )
    if not event_count:
        text = f"明天没有日程，排了{block_phrase}。" if block_phrase else EMPTY_PLAN_SUMMARY
    else:
        text = _pending_summary(event_count, pair_count)
        if block_phrase:
            text += f"另排{block_phrase}。"
    if deferred_ddl:
        # 排不下的要说出来并给原因，不许悄悄少排——「上限 4 小时」这条判据就靠这句可见。
        capped = ddl_cap_hit
        text += (
            f"另有{len(deferred_ddl)}条作业今天没排上"
            + ("（已到当日 4 小时作业上限）" if capped else "（没有装得下的空窗了）")
            + "。"
        )
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
    planner: Optional[Callable[[Any, str], tuple[list[dict], str]]] = None,
) -> DailyPlan:
    """Snapshot tomorrow (or ``for_date``) from events.

    Event items first, then the L1-authored blocks. ``event_count == 0`` with
    no blocks → ``status=empty``. A ``confirmed`` row is returned unchanged.
    """
    day = _as_for_date(for_date)
    window_from, window_to = _day_window(day)
    conn = store.connect(db_path)
    try:
        existing = store.get_daily_plan(conn, day)
        if existing is not None and existing.status == "confirmed":
            return existing

        # WP-PLAN-COMPLETION-ACK：人承认做完的那条**不再被排**（现核铁证是同一条
        # `ddl:evt_ac46920dc0c1` 连排五天 10-12…10-16）。只在这一侧关掉已完成的；
        # 读侧（日 surface / 看板）照旧看得见历史，默认值没动。
        # 排程造的那一行（每格计划行一条）不进这份占用表——`list_events` 默认就把它挡了
        # （见那里的 ``include_plan_artifacts``）。
        events = store.list_events(conn, start_from=window_from, start_to=window_to,
                                   include_completed=False)
        # 裁定 149：项目块 / 作业块**由 L1 排**，代码不再均分。
        # 排不出来就是没有块——不悄悄换回一版均分（那正是这条要退役的形状）。
        blocks, unscheduled, deferred_ddl, plan_note = _l1_blocks(
            conn, day, events, window_from, window_to, planner=planner
        )

        if not events and not blocks:
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
        conflict_count = pair_count
        seq = store.next_confirm_seq(conn)
        seq_at = store.now_iso()
        summary = _plan_summary(len(events), pair_count, blocks, unscheduled, deferred_ddl)
        # 裁定 149④：L1 那句「为什么这么排」要上得了面（接裁定 145 的留痕）；
        # 没排成时同一格照实说没排成与原因——**不拿一句空话冒充排过了**。
        if plan_note:
            summary = f"{summary.rstrip('。')}。{plan_note}"
        evidence: Optional[dict] = None
        if blocks or unscheduled:
            ddl_items = [
                item for item in blocks if (item.get("evidence") or {}).get("kind") == DDL_BLOCK_EVIDENCE_KIND
            ]
            evidence = {
                "kind": "evening_plan",
                "planner_note": plan_note,
                "block_count": len(blocks),
                # 治理线的判据要读的就是这两位：作业块的**时长合计**与项目块的数量，
                # 分开记，别让人从 summary 的中文里再解析一遍。
                "ddl_block_count": len(ddl_items),
                "ddl_minutes": round(
                    sum(
                        (
                            datetime.fromisoformat(item["end_at"])
                            - datetime.fromisoformat(item["start_at"])
                        ).total_seconds()
                        / 60.0
                        for item in ddl_items
                    ),
                    1,
                ),
                "project_block_count": len(blocks) - len(ddl_items),
            }
            if deferred_ddl:
                evidence["ddl_deferred"] = len(deferred_ddl)
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


def avoid_windows_for_day(conn: Any, day: str) -> list[dict]:
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
    """白天看板用的「今天」快照：events（含 location/overlaps）+ 可选 plan_items + conflicts。

    与晚间 20:00 的 generate_evening_plan 不同：**不**写库、不要求已有计划。
    ``avoid_windows`` 带当天 confirmed avoid 窗（缺省 ``[]``）。
    """
    plan, items = plan_items_for_day(conn, day)
    overlaps, conflicts = compute_event_overlaps(events)
    return {
        "date": day,
        "plan_items": [_plan_item_dict(item) for item in items],
        "overlaps": overlaps,
        "conflicts": conflicts,
        "plan_status": plan.status if plan is not None else None,
        "avoid_windows": avoid_windows_for_day(conn, day),
    }


def _plan_item_dict(item: PlanItem) -> dict:
    return {
        # WP-PLAN-COMPLETION-ACK 判据③：界面上那颗「做完了」要打在**事件**上（完成态的真源在
        # `events.completed_at`，不在会被每晚重排抹掉的 `plan_items`），所以这一行必须把
        # `event_id` 带出去——否则界面只能拿到一句标题，无处可认。
        "event_id": item.event_id,
        "id": item.id,
        "title": item.title,
        "start_at": item.start_at,
        "end_at": item.end_at,
        "kind": item.kind,
        "evidence": item.evidence,
    }
