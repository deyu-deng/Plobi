"""裁定 149：明天那份计划**由 L1 排**，代码退到「给材料 + 校边界 + 退回重排」。

这条模块存在的原因是把责任归属写死，而不是再调一版阈值：`planning.py` 那套把剩余时间
均分给每个项目的分配函数（现读形状 = 11 个项目各 42 分 51 秒、14:10→22:44，summary 自己
列「未能安排：Resonote（缺 0.7 小时）…」）排出来的东西用户不认——用户 22:1x 的原话是
「**一定要 LLM 参与。不要代码自动生成**」「**反正就是一天排满就行了**」。

三条纪律写在这里，不留在注释里：

1. **代码不许替 L1 改数据**。校验只报问题，不修数据：不裁时间、不挪条目、不删通勤。
   不通过就带着问题清单**退回重排**（:func:`plan_with_llm` 的重问回路），
   重排到上限仍不过 ⇒ 抛 :class:`PlanRejected` 让调用方当场点名，**绝不静默落一版"差不多"的**。
2. **边界只有四条**（:func:`validate_proposal`）：同一时段不许双订 · 同一件事不许复述当天已有的那条 ·
   占满相邻两项之间唯一间隔的通勤条目不许存在 · 不许排在人**批准过**的避开时段（`avoid_windows`）里。
   第四条是**关系判据**（通勤条目 ≥ 它两侧可用的空档 ⇒ 违规），不是"通勤超过 N 分钟"那种数字。
   第五条是退役均分之后补的：那条偏好原来由均分自己躲开，代码不再排块之后**只剩闸能执行它**——
   光写进材料里让模型"自己注意"，等于用户那句「那段时间别排事」没人负责。
3. **排完必须留一句"为什么这么排"**（裁定 145 的留痕要求）。缺这句的提案直接算不合格——
   它和"派了工要写派给谁"是同一条纪律，不是文案要求。

完成态（`WP-PLAN-COMPLETION-ACK` / `R-099`）的真源已经是 `events.completed_at`，
计划层（`daily_plans` / `plan_items`）10-22 整层退役。本模块①②③ 那几步**不写库**：
它只负责把材料给出去、把边界守住、把不合格的退回去。第⑤ 步
:func:`plan_and_store_day`：**过闸才写 events**，没过闸整笔不写——写库这件事仍然
绑在同一道 :func:`validate_proposal` 上，不是另起一台没有闸的写入器。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterable, Optional, Sequence

logger = logging.getLogger("plobi.agenda.llm_planning")

from . import planning, store

# 与 planning 共用同一套 evidence 口径（那边是写入侧的常量，这里只引用不另立）
PROJECT_BLOCK_EVIDENCE_KIND = planning.PROJECT_BLOCK_EVIDENCE_KIND

# 睡眠锚点的识别词（作息种子是用户自己写的模板，不是我们编的分类）。判据用词表而不是
# 模板 id：模板 id 会变，"睡觉要睡"不会。

# 通勤类条目的识别：标题里带这些词。同一条作业换教室不算通勤，那是课表本身。
_COMMUTE_WORDS = ("通勤", "走路", "步行", "赶车", "坐车", "地铁", "公交", "骑车")

MAX_REPLANS = 2

#: 作业窗口（天）。与那段退役中的均分用的 `planning.DDL_LOOKAHEAD_DAYS` 同一条口径，
#: 不另立一个数（裁定 94：同一事实一份真源）。
DDL_LOOKAHEAD_DAYS = 14

#: 排程那一发的读超时（秒）。见 :func:`default_llm_call` 里那条现核读数。
PLANNING_TIMEOUT_SECONDS = 300.0


class PlanRejected(RuntimeError):
    """重排到上限仍然不合格。带着最后一轮的问题清单抛出，调用方照原话点名。"""

    def __init__(self, violations: Sequence[str], attempts: int) -> None:
        super().__init__("；".join(violations) or "提案不合格")
        self.violations = list(violations)
        self.attempts = attempts


@dataclass(frozen=True)
class Proposal:
    """L1 交回来的那一版计划。`items` 是排好的条目，`reason` 是「为什么这么排」。"""

    items: tuple[dict[str, Any], ...]
    reason: str
    attempts: int = 1
    violations_seen: tuple[str, ...] = field(default_factory=tuple)


# --------------------------------------------------------------------------- #
# ① 材料口：L1 排之前必须读到的那几样，一次给齐
# --------------------------------------------------------------------------- #

def plan_materials(conn: Any, day: str) -> dict[str, Any]:
    """把裁定 149① 列的那五样材料收成一个可 JSON 化的包。

    只读，不写。缺哪样就照实标缺（`missing`），不许拿空数组冒充「明天没事」。
    """
    # include_completed=False：人承认做完的那条**不进材料**（R-099 / Code `3f5d0208` 的读侧）。
    # 均分那段是在生成侧关掉的；换成 L1 之后，关掉的地方就是这里——否则同一条作业还会被排第二次
    # （现核铁证就是 `ddl:evt_ac46920dc0c1` 连排五天）。
    # 秘书自己排出来的块**不进材料**：它们是排程的结果，不是素材。若不挡，
    # 第二天晚上会把昨晚的「写作业 · X」当成一条新作业再排一遍（标题还会越套越长）。
    events = [
        event for event in (store.list_events(
            conn, start_from=f"{day}T00:00:00", start_to=f"{day}T23:59:59",
            include_completed=False) or [])
        if not store.is_planner_event(event)
    ]
    yesterday_day = _shift_day(day, -1)
    yesterday_events = store.list_events(
        conn, start_from=f"{yesterday_day}T00:00:00",
        start_to=f"{yesterday_day}T23:59:59") or []
    # 完成态的真源是 events.completed_at（R-099）；昨天的账只看这一格。
    yesterday = {
        "date": yesterday_day,
        "events": [_event_row(event) for event in yesterday_events],
        "completed": [
            event.title for event in yesterday_events
            if getattr(event, "completed_at", None)
        ],
    }
    # 判据「作业信源接进来」：作业**不是当天的日程**，它是"某天之前要交"。只看当天那一格，
    # 现核 2026-10-17 就是 `ddl 0 条` ⇒ L1 永远看不见有作业要写，排出来的"满"是假的满。
    todays = {str(getattr(e, "id", "")) for e in events}
    upcoming = [_event_row(e) for e in (store.list_events(
        conn, start_from=f"{day}T00:00:00",
        start_to=f"{_shift_day(day, DDL_LOOKAHEAD_DAYS)}T23:59:59",
        include_completed=False) or [])
        if str(getattr(e, "kind", "")) in ("ddl", "task")
        and str(getattr(e, "id", "")) not in todays
        and not store.is_planner_event(e)]

    missing: list[str] = []

    # 裁定 148 那一维「额度」：读得出的只有发数与 token，钱读不出来就照实标估（R-106②）。
    budget = quota_budget()
    if not budget.get("available"):
        missing.append("额度这一维（账本读不出来，不是「没花」）")

    return {
        "for_date": day,
        "classes": _by_kind(events, "class"),
        "deadlines": _by_kind(events, "ddl", "task"),
        "upcoming_deadlines": upcoming,
        "other_events": _others(events),
        # 当天**已经会落成条目**的那些行（课／作业／别的），边界六与落库前摘复述用的都是这一格。
        # 之前只有 classes／deadlines／other_events 三个分桶，闸拿不到"这一面已有的全部条目"，
        # 于是「把课又排一遍」那种提案没人拦（2026-10-20 图上那三张空壳）。
        "today_items": [_event_row(e) for e in events],
        # 用户**批准过**的「避开时段」（checkin 卡的 avoid_windows）。退役前是均分自己躲开它们；
        # 换成秘书排之后，这一格必须跟着进材料，否则"我说过那段时间别排事"这句话就没人执行了。
        # 未批准的窗不进来（`list_active_avoid_windows` 只读 confirmed 那张卡）。
        "avoid_windows": planning.avoid_windows_for_day(conn, day),
        "project_pace": [
            {"name": entry.name, "weekly_hours": hours}
            for entry, hours in planning.paced_projects()
        ],
        "quota_budget": budget,
        "yesterday": yesterday,
        "missing": missing,
    }


#: 裁定 148「每一类要有预算」里**额度**那一维取多少行账本回来算。
QUOTA_LEDGER_ROWS = 200


def quota_budget(rows: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
    """额度这一维的材料——**真数只有发数与 token，钱这一格这台读不出来就明写读不出来**。

    为什么不是零：`plobi/quota/usage_ledger.py` 的账本里 `priced_rows` 现在是 0
    （gateway 不解析 usage 的钱，文件处理器默认也没挂），报个 0 就是"这发没花钱"的假账
    （R-073 同一条纪律）。所以 `money` 留 `None`、`basis` 标 `estimated`，
    让 L1 按"今天还能打几发"权衡，而不是按一个不存在的余额。

    读账本失败不许把排程搞崩，但也不许冒充"额度宽松"：异常收进 `available=False` 的 note。
    """
    try:
        from plobi.quota import usage_ledger

        if rows is None:
            rows = usage_ledger.read_recent(QUOTA_LEDGER_ROWS)
        summary = usage_ledger.summarize(rows)
    except Exception as exc:  # 账本没落盘 / 目录不在 = 这一维没材料，不是零
        return {
            "available": False,
            "basis": "unknown",
            "money": None,
            "note": f"额度账本读不出来（{type(exc).__name__}: {str(exc)[:100]}）——"
                    "这一维今天没有材料，不是「没花」",
        }

    priced = int(summary.get("priced_rows") or 0)
    return {
        "available": True,
        "basis": "estimated" if not priced else "partially_priced",
        "window_rows": summary.get("rows", 0),
        "per_source": summary.get("per_source") or {},
        "money": summary.get("cost"),
        "money_status": summary.get("cost_status"),
        "note": (
            "这一维只有发数与 token 是真数；钱读不出来（账本里 priced="
            f"{priced}/{summary.get('rows', 0)}），所以只是**估**——"
            "按「今天还能打几发」权衡，不许把没数的东西当余额报给用户。"
        ),
    }


def build_prompt(materials: dict[str, Any], *, extra: Optional[Sequence[str]] = None) -> str:
    """给 L1 的那段口令。`extra` 是上一轮的问题清单——退回重排时把违规原样带回去。"""
    import json

    lines = [
        "你来排明天（" + str(materials.get("for_date")) + "）的一天。规则四条，一条都不能破：",
        "1) **一天排满**：不留大块空白。",
        "2) **同一时段不许双订**：两件事不许叠在一起。"
        "材料里 `classes` / `deadlines` / `other_events` 那些条**本来就已经在当天日程里了**——"
        "你要排的是它们之外的空白，把它们原样再写一条不算「保留」，那种提案会被当场打回"
        "（现核 2026-10-20：三门课各被复述成一块，面上就是一个时段三张卡、两张是空壳）。",
        "3) **相邻两项之间只够一次步行通勤的那种间隔，不许再写一条通勤占掉它**"
        "——那种空档本来就是留给走路的，写成条目等于把它吃掉。",
        "4) 排完必须交一句**为什么这么排**（`reason`）。没有这句的提案算不合格。",
        "",
        "**三条口径**（裁定 148 里缺的那两类 + 人已批准的避开时段）：",
        "- `avoid_windows` 是人**批准过**的「那段时间别排事」。撞上它的提案会被当场打回，"
        "别指望写一句理由糊过去；材料里没有这一格就是没有（不存在的窗不许你替用户记着）。",
        "- 吃饭／睡觉这类生活块如果人让你安排，就用普通条目排进去；材料里没有的生活类别"
        "（运动／休息／杂务…）**不许替用户编**，他想要会在对话里自己说。",
        "- `quota_budget` 那一格是**估的**：这台只有发数与 token 是真数，钱读不出来时 `money` 就是 `null`——"
        "`null` 是「读不出来」，不是「没花」。按「今天还能打几发」权衡，别把它当余额报给用户。",
        "",
        "**只交一个 JSON 对象，别的字都别加**（不要前言、不要解释、不要 markdown 围栏之外的话）：",
        '{"items": [{"title": "…", "start_at": "YYYY-MM-DDTHH:MM:SS", '
        '"end_at": "YYYY-MM-DDTHH:MM:SS"}], "reason": "为什么这么排"}',
        "键名就叫 `items` / `start_at` / `end_at` / `reason`；时间必须是完整 ISO（带日期）。",
        "材料如下（缺的照实缺，别编）：",
        json.dumps(materials, ensure_ascii=False, default=str),
    ]
    if extra:
        lines.append("上一版被打回，问题是：")
        lines.extend("- " + str(v) for v in extra)
        lines.append("请改掉这些问题重新排一版，不要只改措辞。")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# ② 边界校验：只报问题，不动数据
# --------------------------------------------------------------------------- #

def validate_proposal(
    items: Iterable[dict[str, Any]],
    *,
    avoid: Sequence[dict[str, Any]] = (),
    scheduled: Sequence[dict[str, Any]] = (),
) -> list[str]:
    """返回违规清单（空 = 合格）。**这个函数一个字都不改 `items`。**"""
    rows = [dict(item) for item in items]
    spans = [(row, _span_of(row)) for row in rows]
    problems: list[str] = []

    for row, span in spans:
        if span is None:
            problems.append(f"「{_title(row)}」缺起止时间")
    timed = [(row, span) for row, span in spans if span is not None]

    # 边界一：不许双订
    for i in range(len(timed)):
        for j in range(i + 1, len(timed)):
            (row_a, (s_a, e_a)), (row_b, (s_b, e_b)) = timed[i], timed[j]
            if _overlaps(s_a, e_a, s_b, e_b):
                problems.append(f"「{_title(row_a)}」与「{_title(row_b)}」同一时段双订")

    # 边界二：不许把当天**已经有**的那一件事再排一遍（同时段、标题归一后全等）。
    # 边界二只看提案内部的两两，看不见课表当天已经落的那条 `class` 行——图上「一个时段三张卡、
    # 两张是空壳」就是从这里漏出去的（现核 2026-10-20：三节课各被复述成一条 `task`）。
    # 这一条点名给模型看，是让它**别再交**这一发；落库前 :func:`_block_rows_from_proposal`
    # 还会再摘一次，那是数据那一面的兜底，两者用的是同一个判据（:func:`restates`）。
    for row, _span in timed:
        for existing in scheduled:
            if restates(row, existing):
                problems.append(
                    f"「{_title(row)}」把当天已有的一条又排了一遍"
                    f"（{_span[0].strftime('%H:%M')}–{_span[1].strftime('%H:%M')} 同段同题，那条已经在日程里了）"
                )

    # 边界三：人批准过的「避开时段」。均分那套自己躲开它；换成秘书排之后，
    # 这条只能落在闸上——只写进材料里让模型"自己注意"，等于这句话没人执行。
    for window in avoid:
        w_span = _span_of(window)
        if w_span is None:
            continue
        for row, (start, end) in timed:
            if not _overlaps(start, end, *w_span):
                continue
            problems.append(
                f"「{_title(row)}」排在你说过要避开的时段"
                f"（{w_span[0].strftime('%H:%M')}–{w_span[1].strftime('%H:%M')}）里"
            )

    # 边界四：占满相邻两项之间唯一间隔的通勤条目（关系判据，不写分钟数）
    ordered = sorted(timed, key=lambda pair: pair[1][0])
    for index, (row, (start, end)) in enumerate(ordered):
        if not _is_commute(row):
            continue
        prev_end = ordered[index - 1][1][1] if index > 0 else None
        next_start = ordered[index + 1][1][0] if index + 1 < len(ordered) else None
        if prev_end is None or next_start is None:
            continue
        gap = (next_start - prev_end).total_seconds()
        if (end - start).total_seconds() >= gap:
            problems.append(
                f"「{_title(row)}」把「{_title(ordered[index - 1][0])}」与"
                f"「{_title(ordered[index + 1][0])}」之间那段空档占满了——"
                "那段本来就是留给步行通勤的，不许再写成一条"
            )

    return problems


# --------------------------------------------------------------------------- #
# ③ 重排回路：不合格就退回重问，到上限就点名
# --------------------------------------------------------------------------- #

def plan_with_llm(
    day: str,
    llm_call: Callable[[str], Any],
    *,
    conn: Any = None,
    max_replans: int = MAX_REPLANS,
) -> Proposal:
    """让 L1 排一版：给材料 → 收提案 → 校边界 → 不合格带问题清单重问。

    `llm_call(prompt)` 返回 dict（`items` + `reason`）或 JSON 字符串。**不写库**；
    写库归 :func:`plan_and_store_day`（过同一道闸后直接写 events）。
    """
    owns_conn = conn is None
    conn = conn or store.connect(None)
    try:
        materials = plan_materials(conn, day)
        prompt = build_prompt(materials)
        seen: list[str] = []
        for attempt in range(1, max_replans + 2):
            parsed = _parse_proposal(llm_call(prompt), day)
            proposal = parsed["items"]
            problems = validate_proposal(proposal,
                                 avoid=materials.get("avoid_windows") or [],
                                 scheduled=materials.get("today_items") or [])
            if not parsed["reason"]:
                # 裁定 149④：不写「为什么这么排」就是没留痕，与裁定 145 同一条纪律。
                problems = problems + ["没交「为什么这么排」（reason）——裁定 149④ 的留痕"]
            if not problems:
                return Proposal(
                    items=tuple(proposal), reason=parsed["reason"],
                    attempts=attempt, violations_seen=tuple(seen),
                )
            seen.extend(problems)
            prompt = build_prompt(materials, extra=problems)
        raise PlanRejected(list(problems), max_replans + 1)
    finally:
        if owns_conn:
            conn.close()


# --------------------------------------------------------------------------- #
# 小工具（时间解析只在这一处）
# --------------------------------------------------------------------------- #

def _extract_json(text: str) -> Any:
    """从"一句人话 + ```json 围栏"那种回包里把 JSON 对象抠出来。

    现核真回包（2026-10-16 那一发）就是这个形状：先一句「好的，按规则排完…」再一段围栏，
    直接 `json.loads(整串)` 必然 `Expecting value: line 1 column 1`。
    取第一个 `{` 到与之配对的最后一个 `}`，剥掉围栏标记。
    """
    body = text
    if "```" in body:
        parts = body.split("```")
        for part in parts[1:]:
            candidate = part[4:] if part.lstrip().lower().startswith("json") else part
            if "{" in candidate:
                body = candidate
                break
    start = body.find("{")
    end = body.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("回包里找不到 JSON 对象")
    import json

    return json.loads(body[start:end + 1])


def _normalize_items(rows: Any, day: str) -> list[dict[str, Any]]:
    """把模型爱用的别名形状收成契约形状（`title` / `start_at` / `end_at`）。

    现核那一发交的是 `schedule` + `time` + `end`（裸 `HH:MM`）。**这不是迁就**：
    键名同义是模型行为的常态，而"排得很好却被扔掉"是我们这边的损失；
    真源与边界仍然按契约校验，认不出来的字段一律不当成读数。
    """
    out: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        title = item.get("title") or item.get("name") or item.get("activity")
        start = item.get("start_at") or item.get("start") or item.get("time") or item.get("from")
        end = item.get("end_at") or item.get("end") or item.get("to")
        if not title or not start:
            continue
        item["title"] = str(title)
        item["start_at"] = _expand(start, day)
        item["end_at"] = _expand(end, day) if end else item["start_at"]
        out.append(item)
    return out


def _expand(value: Any, day: str) -> str:
    """裸 `HH:MM:SS` / `HH:MM` 补成当天完整 ISO；已经是 ISO 的原样走。"""
    text = str(value).strip()
    if len(text) <= 8 and ":" in text:
        return f"{day}T{text if len(text) == 8 else text + ':00'}"
    return text


def _material_index(materials: dict[str, Any]) -> list[tuple[str, str]]:
    """(名字, 证据串) 表：课名 / 作业标题 / 项目名，每条都指得回材料里那一行。"""
    out: list[tuple[str, str]] = []
    for key, label in (("classes", "class"), ("deadlines", "ddl"), ("upcoming_deadlines", "ddl")):
        for row in materials.get(key) or []:
            title = str(row.get("title") or "").strip()
            if title:
                out.append((title, f"{label}:{row.get('id')}"))
    for row in materials.get("project_pace") or []:
        name = str(row.get("name") or "").strip()
        if name:
            out.append((name, f"project:{name}"))
    return out


def _match_source(title: str, sources: list[tuple[str, str]]) -> str:
    """块标题 ↔ 材料那一条。**先全等，有全等就不使用前缀**（裁定 153 那条串课教训的口径）。"""
    text = (title or "").strip()
    exact = [ev for name, ev in sources if name == text]
    if exact:
        return exact[0]
    hits = [ev for name, ev in sources if name and name in text]
    return hits[0] if len(hits) == 1 else ""


def _parse_proposal(raw: Any, day: str = "") -> dict[str, Any]:
    """把 L1 交回来的东西归成 ``{"items": [...], "reason": "..."}``。

    字符串（模型常这么回）先 JSON 解析；解析不动就抛出去让调用方点名，
    不猜一版空计划当答案。
    """
    if isinstance(raw, str):
        raw = _extract_json(raw)
    if isinstance(raw, dict):
        items = raw.get("items") or raw.get("plan") or raw.get("schedule") or []
        reason = str(raw.get("reason") or raw.get("why") or raw.get("note") or "").strip()
    else:
        items, reason = list(raw or []), ""
    day = day or str(items[0].get("start_at", ""))[:10] if items else ""
    return {"items": _normalize_items(items, day), "reason": reason}


def _span_of(row: dict[str, Any]) -> Optional[tuple[datetime, datetime]]:
    start, end = _dt(row.get("start_at")), _dt(row.get("end_at"))
    if start is None or end is None or end <= start:
        return None
    return start, end


def _dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start < b_end and b_start < a_end


def _title(row: dict[str, Any]) -> str:
    return str(row.get("title") or row.get("id") or "（无名条目）")


def _title_key(text: Any) -> str:
    """标题归一：去掉所有空白再比大小写。判据是**归一后全等**，不是相似——差一个字算两件事。"""
    return "".join(str(text or "").split()).casefold()


def restates(row: dict[str, Any], other: dict[str, Any]) -> bool:
    """这两行是不是**同一件事被排了两遍**：起止时段全等，且标题归一后全等。

    图上那三张并排的卡就是这么来的——秘书交回 `08:00–09:35 理论力学（甲）` 这种块，
    而课本身已经从天课表落了一条同题同段的 `class` 行（现核 2026-10-20：
    `evt_eccec2fa3243` class 与 `evt_2d1d0af1c4d6` task，工程训练、电工电子学实验同样一对）。
    只认全等：比那条窄或宽一段都不算复述（那是真占用了别人的时间，交给边界二去拦）。
    没有结束时间的那条（作业／截止类）比不出时段，**不猜**。
    """
    a, b = _span_of(row), _span_of(other)
    if a is None or b is None:
        return False
    return a == b and _title_key(_title(row)) == _title_key(_title(other))


def duplicate_slot_titles(rows: Sequence[dict[str, Any]]) -> list[tuple[str, str]]:
    """一面计划里所有「同时段同题」的行对——**判据就是它的数量，0 才算修好**。

    行形状按提案那一族读：`title` + `start_at` + `end_at`。
    """
    pairs: list[tuple[str, str]] = []
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            if restates(rows[i], rows[j]):
                pairs.append((_title(rows[i]), _title(rows[j])))
    return pairs


def _is_commute(row: dict[str, Any]) -> bool:
    title = _title(row)
    return any(word in title for word in _COMMUTE_WORDS)


def _shift_day(day: str, delta: int) -> str:
    from datetime import timedelta

    return (datetime.fromisoformat(day).date() + timedelta(days=delta)).isoformat()


def _by_kind(events: Sequence[Any], *kinds: str) -> list[dict[str, Any]]:
    return [_event_row(event) for event in events if str(getattr(event, "kind", "")) in kinds]


def _others(events: Sequence[Any]) -> list[dict[str, Any]]:
    return [_event_row(event) for event in events
            if str(getattr(event, "kind", "")) not in ("class", "ddl", "task")]


def _event_row(event: Any) -> dict[str, Any]:
    return {
        "id": event.id, "title": event.title, "kind": event.kind,
        "start_at": str(event.start_at), "end_at": str(event.end_at),
        "evidence": dict(event.evidence or {}),
    }


# --------------------------------------------------------------------------- #
# ④ 接进生成路径：项目块 / 作业块由 L1 排，代码不再均分（裁定 149）
# --------------------------------------------------------------------------- #

def default_llm_call(route: Any = None) -> Callable[[str], Any]:
    """生产用的那一发调用：走额度感知那条便宜路（`plobi.quota.route`）。

    为什么用它而不是新造一个客户端：那条腿已经有账（`usage_ledger` 记打没打 / 哪个源 /
    几次 / 多少钱），新造一条就又多一本账（裁定 94）。返回的字符串按 JSON 解析；
    解析不出来就抛，让上层点名——不猜一版空计划当答案。
    """
    from plobi.quota.route import QuotaAwareCompleter

    # 现核（10-16 11:0x）：这份材料 + 这条口令在 workbuddy/hy3 上跑完 **113.6 秒**、回 1902 字，
    # 而 `route.py` 给 aigw 那条的默认超时是 **120 秒**——排程这一发是贴着天花板跑的，
    # 稍微慢一点就被判成"这条腿死了"（今天那三发就是这么挂的）。所以这一发单独给预算，
    # 不去动那个全局默认值（那是所有 L2 便宜路共用的，放宽它等于放宽所有事）。
    completer = QuotaAwareCompleter(timeout=PLANNING_TIMEOUT_SECONDS)

    def call(prompt: str) -> str:
        text = completer(prompt, route)
        if not text:
            raise RuntimeError("排程那一发没拿到回答（额度路空返回）")
        # 原样把字符串交出去：抠 JSON 这件事只在 `_extract_json` 一处做（裁定 94）。
        return str(text)

    return call


#: 裁定 161（10-16）：**没有任何可排材料时不许打模型**——花钱问模型「今天没什么可排」既贵又
#: 会凭空排出块来（实测那条空计划就是这么变成待确认的）。空这一格照实回
#: `status=nothing_to_schedule`，由调用方对人说「明天没有任何可排材料」。
NOTHING_TO_SCHEDULE_NOTE = "明天没有任何可排材料（课、作业、待办、项目节奏全空），没有问模型"

_MATERIAL_KEYS = (
    "classes", "deadlines", "upcoming_deadlines", "other_events", "project_pace",
)


def nothing_to_schedule(materials: dict[str, Any]) -> bool:
    """材料包是不是真的一点都没有。缺哪样算哪样——不许拿 `missing` 冒充「有材料」。"""
    return not any(materials.get(key) for key in _MATERIAL_KEYS)


def _block_rows_from_proposal(
    items: Iterable[dict[str, Any]],
    reason: str,
    sources: list[tuple[str, str]],
    scheduled: Sequence[dict[str, Any]] = (),
) -> list[dict[str, Any]]:
    """把提案条目收成排程块的形状（**evidence 的口径只在这一处定义**）。

    晚间计划口（:func:`blocks_from_l1`）与秘书嘴上那一发（:func:`plan_and_store_day`）共用这一个
    构造口：`planner` 是「谁排的」、`source` 指回材料里那一条（哪门课的哪份作业 / 哪个项目）。
    两处各写一份，面上「是谁排的」就会有两套说法（裁定 94）。
    对不上材料也留一句「对不上」，不许空着——空着那一格看起来像排过了，实际没人知道为什么。

    `scheduled` 是当天已经会落成条目的那些行（课表里的课、当天的作业／截止）。
    2026-10-20 图上「一个时段三张卡、两张是空壳」就是这一格空着——模型把三节课各复述成一块 `task`。
    摘的条件是 :func:`restates`（时段全等＋标题归一后全等），**并且出声**（`logger.warning`）：
    静默摘行会把「模型交错了」伪装成「今天就这么排」。
    """
    blocks: list[dict[str, Any]] = []
    dropped: list[str] = []
    for item in items:
        span = _span_of(item)
        if span is None:
            continue
        if any(restates(item, existing) for existing in scheduled):
            dropped.append(_title(item))
            continue
        source = _match_source(_title(item), sources) or "l1:unnamed（材料里对不上任何一条）"
        # 面上与治理线把「作业块」和「项目块」分开计数读的就是这一格：来源是作业（`ddl:`）的那块
        # 必须算作业块。一律盖成 project_block 会让 `ddl_block_count` 永远是 0——排得再对也是假读数。
        kind = (planning.DDL_BLOCK_EVIDENCE_KIND if source.startswith("ddl:")
                else PROJECT_BLOCK_EVIDENCE_KIND)
        blocks.append({
            "title": _title(item),
            "start_at": span[0].isoformat(),
            "end_at": span[1].isoformat(),
            "kind": "task",
            "conflict_with": [],
            "evidence": {"kind": kind, "planner": "l1",
                         "reason": reason,
                         "source": source},
        })
    if dropped:
        logger.warning(
            "plobi planning: 摘掉 %d 条复述当天已有日程的块（同时段同题）：%s",
            len(dropped), "、".join(dropped),
        )
    return blocks


def blocks_from_l1(
    conn: Any,
    day: str,
    *,
    llm_call: Optional[Callable[[str], Any]] = None,
    max_replans: int = MAX_REPLANS,
) -> tuple[list[dict[str, Any]], str]:
    """让 L1 排项目块 / 作业块，返回 ``(blocks, note)``。

    `note` 是给面上那句人话用的：排成就留 L1 写的「为什么这么排」，没排成就照实说没排成
    与原因。**这里没有任何兜底分配**——均分那段（`_project_block_items`）按裁定 149 退役，
    L1 排不出来就是没有块，不悄悄换回一版均分。
    """
    call = llm_call or default_llm_call()
    try:
        materials = plan_materials(conn, day)
        if nothing_to_schedule(materials):
            return [], NOTHING_TO_SCHEDULE_NOTE
        proposal = plan_with_llm(day, call, conn=conn, max_replans=max_replans)
    except PlanRejected as rejected:
        return [], "L1 那一轮没排成（" + "；".join(rejected.violations[:3]) + "），今天没有项目块"
    except Exception as exc:  # 通道 / 解析 / 网络：照实说，不降级成均分
        return [], f"L1 那一轮没排成（{type(exc).__name__}: {str(exc)[:120]}），今天没有项目块"

    blocks = _block_rows_from_proposal(proposal.items, proposal.reason,
                                       _material_index(materials),
                                       scheduled=materials.get("today_items") or [])
    if not blocks:
        return [], "L1 交回来的提案里没有任何能落的时间块"
    return blocks, proposal.reason


# --------------------------------------------------------------------------- #
# ⑤ 秘书的嘴落库：排一次 → 过同一道闸 → 才写 **events**（裁定 163；10-22 计划层退役）
# --------------------------------------------------------------------------- #

#: 条目级 evidence 上「谁排的」那一格（与 :func:`_block_rows_from_proposal` 一致）。
MOUTH_PLANNER = "l1"
MOUTH_PLAN_SOURCE = "l1:secretary_mouth"


def _refusal(reason: str, violations: Sequence[str] = (), **extra: Any) -> dict[str, Any]:
    """一句人话 + `written=0`。

    返回形状与 :func:`plobi.agenda.plan_edits.move_plan_item` 的失败分支**同族**（R-105）：
    `{ok: False, reason, violations}`。重试就是再叫一次 :func:`plan_and_store_day`
    （:func:`plobi.agenda.store.upsert_planner_events` 按（日, kind, 标题）覆盖同一行），
    撤销就是走既有的 `move_block` / 确认流，这里只保证**没过闸时一个字节都不写**。
    """
    payload: dict[str, Any] = {"ok": False, "reason": reason, "written": 0,
                               "violations": list(violations)}
    payload.update(extra)
    return payload


def _mouth_summary(
    day: str,
    event_count: int,
    conflict_count: int,
    blocks: Sequence[dict[str, Any]],
    reason: str,
) -> str:
    """面上那一段：谁排的、排了几块、为什么这么排（裁定 145／149④ 的留痕）。"""
    head = (
        f"明天有{event_count}项日程，其中{conflict_count}处时间重叠。"
        if conflict_count else f"明天有{event_count}项日程，时间无重叠。"
    ) if event_count else f"{day} 没有日程。"
    return f"{head}秘书自己排了{len(blocks)}块。{reason}".rstrip("。") + f"。（{MOUTH_PLAN_SOURCE}）"


def plan_and_store_day(
    day: str,
    *,
    conn: Any = None,
    llm_call: Optional[Callable[[str], Any]] = None,
    proposal_fn: Optional[Callable[[Any, str], tuple[Iterable[dict[str, Any]], str]]] = None,
    max_replans: int = MAX_REPLANS,
) -> dict[str, Any]:
    """秘书自己把 `day` 排一次，**过了 :func:`validate_proposal` 那道同一的闸才写库**。

    裁定 163（用户 10-16 22:5x）：「不要用脚本排程。让日程秘书自己排一次明天的计划。
    要落到真实的 entry 里面。」所以这条路的四步是写死的顺序：

    1. **材料**：:func:`plan_materials` 那五样真材料（一课都不许多报）。没材料就**连模型都不打**
       （裁定 161）。
    2. **提案**：默认走 :func:`plan_with_llm`（同一发模型、同一条退回重排回路）；
       `proposal_fn(conn, day) -> (items, reason)` 是**测试的注入点**，给假提案用——
       生产不传，测试绝不用真模型。
    3. **闸**：装配出来的块再过一次 `validate_proposal`（双订 / 复述 / 避开时段 / 通勤空档），
       **不过就整笔不写**，返回点名拒绝 + 一句人话。绝不降级回均分（裁定 149／161③）。
    4. **写**：排出来的块**直接落成 events**（用户 10-22「计划整层退役，夜间直接写 events」），
       每块 `evidence` 带 `planner`（谁排的）与 `source`（指回材料哪一条）。
       不再写 `daily_plans` / `plan_items`。
    """
    owns_conn = conn is None
    conn = conn or store.connect(None)
    try:
        materials = plan_materials(conn, day)

        if nothing_to_schedule(materials):
            return _refusal(NOTHING_TO_SCHEDULE_NOTE, for_date=day,
                            status="nothing_to_schedule", written=0)

        # ② 出提案（测试注入假提案；生产只走 L1 那一发）
        if proposal_fn is not None:
            raw_items, reason = proposal_fn(conn, day)
            items = list(raw_items or [])
        else:
            try:
                proposed = plan_with_llm(day, llm_call or default_llm_call(),
                                         conn=conn, max_replans=max_replans)
            except PlanRejected as rejected:
                return _refusal(
                    "L1 那一轮没排成（" + "；".join(rejected.violations[:3])
                    + f"）。{day} 那份计划一个字没写，也不换回均分。",
                    rejected.violations, for_date=day, status="rejected",
                    attempts=rejected.attempts)
            except Exception as exc:  # 通道 / 解析 / 网络：照实说，不编一版
                return _refusal(
                    f"L1 那一轮没排成（{type(exc).__name__}: {str(exc)[:120]}）。"
                    f"{day} 那份计划一个字没写。",
                    for_date=day, status="channel_error")
            items, reason = list(proposed.items), proposed.reason

        reason = (reason or "").strip()
        if not reason:
            return _refusal(
                f"没交「为什么这么排」（reason）——裁定 149④ 的留痕。{day} 那份计划一个字没写。",
                ["没交 reason"], for_date=day, status="no_reason")

        blocks = _block_rows_from_proposal(items, reason, _material_index(materials),
                                       scheduled=materials.get("today_items") or [])
        if not blocks:
            return _refusal(
                f"这一版没有任何能落的时间块。{day} 那份计划一个字没写。",
                for_date=day, status="no_blocks")

        # ③ 过闸：唯一一处写入前置。拆掉这一步，下面那个「同时段双订」的提案就会直接落库。
        problems = validate_proposal(blocks,
                             avoid=materials.get("avoid_windows") or [],
                             scheduled=materials.get("today_items") or [])
        if problems:
            return _refusal(
                "这一版没过边界闸：" + "；".join(problems[:3]) + "。"
                f"{day} 那份计划一个字没写，也不换回均分（裁定 149／161③）。",
                problems, for_date=day, status="rejected")

        # ④ 写：排出来的块直接落成 events（计划层已整层退役，不再写 daily_plans/plan_items）
        written = store.upsert_planner_events(
            conn,
            day,
            blocks,
            planner=MOUTH_PLANNER,
            reason=reason,
            written_by=MOUTH_PLAN_SOURCE,
        )
        events = store.list_events(
            conn, start_from=f"{day}T00:00:00", start_to=f"{day}T23:59:59",
            include_completed=False) or []
        _, conflict_rows = planning.compute_event_overlaps(events)
        event_count = sum(1 for event in events if not store.is_planner_event(event))
        summary = _mouth_summary(day, event_count, len(conflict_rows), blocks, reason)
        return {
            "ok": True,
            "status": "planned",
            "for_date": day,
            "summary": summary,
            "reason": reason,
            "block_count": len(blocks),
            "written": len(written),
            "event_ids": [event.id for event in written],
            "events": [
                {
                    "id": event.id,
                    "title": event.title,
                    "start_at": event.start_at,
                    "end_at": event.end_at,
                    "kind": event.kind,
                    "source": event.source,
                }
                for event in written
            ],
            "conflict_count": len(conflict_rows),
            "blocks": [dict(block["evidence"]) for block in blocks],
        }
    finally:
        if owns_conn:
            conn.close()
