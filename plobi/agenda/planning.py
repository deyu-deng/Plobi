"""Agenda read helpers: overlaps, avoid windows, and the day surface.

计划层已于 10-22 整层退役（用户拍板）：夜间排程直接写 ``events``。这个模块现在
只剩白天看板要用的读侧计算，不再生成或读 ``daily_plans`` / ``plan_items``。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Iterable, Optional

from . import store
from .store import Event

# C5 project pace blocks: deterministic rules only, zero LLM.
PROJECT_BLOCK_EVIDENCE_KIND = "project_block"
# 无主 DDL（没挂上任何 pace 项目的截止事件）自己拿一块——用户那句
# 「未提交作业要排进写作业时间」要的就是这块。它不扩 events 词表（kind 仍是 task），
# 只在 evidence.kind 上标出来，与 project_block 同一手法。
DDL_BLOCK_EVIDENCE_KIND = "ddl_block"
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


# --- WP-DAY-SURFACE: 重叠判定 -------------------------------------------------


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
    """白天看板用的「今天」快照：events（含 location/overlaps）+ conflicts。

    **不**写库。``avoid_windows`` 带当天 confirmed avoid 窗（缺省 ``[]``）。
    """
    overlaps, conflicts = compute_event_overlaps(events)
    return {
        "date": day,
        "overlaps": overlaps,
        "conflicts": conflicts,
        "avoid_windows": avoid_windows_for_day(conn, day),
    }
