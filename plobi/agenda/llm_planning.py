"""裁定 149：明天那份计划**由 L1 排**，代码退到「给材料 + 校边界 + 退回重排」。

这条模块存在的原因是把责任归属写死，而不是再调一版阈值：`planning.py` 那套把剩余时间
均分给每个项目的分配函数（现读形状 = 11 个项目各 42 分 51 秒、14:10→22:44，summary 自己
列「未能安排：Resonote（缺 0.7 小时）…」）排出来的东西用户不认——用户 22:1x 的原话是
「**一定要 LLM 参与。不要代码自动生成**」「**反正就是一天排满就行了**」。

三条纪律写在这里，不留在注释里：

1. **代码不许替 L1 改数据**。校验只报问题，不修数据：不裁时间、不挪条目、不删通勤。
   不通过就带着问题清单**退回重排**（:func:`plan_with_llm` 的重问回路），
   重排到上限仍不过 ⇒ 抛 :class:`PlanRejected` 让调用方当场点名，**绝不静默落一版"差不多"的**。
2. **边界只有四条**（:func:`validate_proposal`）：睡眠与三餐锚点不许被挤掉 · 同一时段不许双订 ·
   总量不许超过当天清醒窗口 · 占满相邻两项之间唯一间隔的通勤条目不许存在。
   第四条是**关系判据**（通勤条目 ≥ 它两侧可用的空档 ⇒ 违规），不是"通勤超过 N 分钟"那种数字。
3. **排完必须留一句"为什么这么排"**（裁定 145 的留痕要求）。缺这句的提案直接算不合格——
   它和"派了工要写派给谁"是同一条纪律，不是文案要求。

前置（`WP-PLAN-COMPLETION-ACK` / `R-099`：`plan_items` 没有状态列 ⇒ 同一条作业连排五天）
由产品线在落。本模块因此**不写库**：它只负责把材料给出去、把边界守住、把不合格的退回去。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterable, Optional, Sequence

from . import planning, store

# 与 planning 共用同一套 evidence 口径（那边是写入侧的常量，这里只引用不另立）
PROJECT_BLOCK_EVIDENCE_KIND = planning.PROJECT_BLOCK_EVIDENCE_KIND

# 睡眠锚点的识别词（作息种子是用户自己写的模板，不是我们编的分类）。判据用词表而不是
# 模板 id：模板 id 会变，"睡觉要睡"不会。
_SLEEP_WORDS = ("觉", "睡")

# 通勤类条目的识别：标题里带这些词。同一条作业换教室不算通勤，那是课表本身。
_COMMUTE_WORDS = ("通勤", "走路", "步行", "赶车", "坐车", "地铁", "公交", "骑车")

MAX_REPLANS = 2

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
    events = store.list_events(conn, start_from=f"{day}T00:00:00", start_to=f"{day}T23:59:59") or []
    kept, _yielded, moved = planning.place_anchors_for_day(conn, day, list(events))
    anchors = planning.kept_anchors_as_dicts(kept)
    yesterday = planning.l1_plan_view(_shift_day(day, -1), [], db_path=None)

    missing: list[str] = []
    # 完成态（R-099）落地之前，「昨天做完了哪些」这台读不出来——照实说，不拿 event 数量冒充。
    if not _has_completion_state():
        missing.append("昨天的完成情况（plan_items 还没有状态列，R-099 在落）")

    return {
        "for_date": day,
        "waking_window": _waking_window(conn, day),
        "classes": _by_kind(events, "class"),
        "deadlines": _by_kind(events, "ddl", "task"),
        "other_events": _others(events),
        "routine_anchors": anchors,
        "anchors_moved": moved,
        "project_pace": [
            {"name": entry.name, "weekly_hours": hours}
            for entry, hours in planning.paced_projects()
        ],
        "yesterday": yesterday,
        "missing": missing,
    }


def build_prompt(materials: dict[str, Any], *, extra: Optional[Sequence[str]] = None) -> str:
    """给 L1 的那段口令。`extra` 是上一轮的问题清单——退回重排时把违规原样带回去。"""
    import json

    lines = [
        "你来排明天（" + str(materials.get("for_date")) + "）的一天。规则四条，一条都不能破：",
        "1) **一天排满**：清醒窗口内不留大块空白；但**睡眠与三餐锚点不许被挤掉**，"
        "它们占的时段就是你的边界。",
        "2) **同一时段不许双订**：两件事不许叠在一起。",
        "3) **相邻两项之间只够一次步行通勤的那种间隔，不许再写一条通勤占掉它**"
        "——那种空档本来就是留给走路的，写成条目等于把它吃掉。",
        "4) 排完必须交一句**为什么这么排**（`reason`）。没有这句的提案算不合格。",
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
    anchors: Sequence[dict[str, Any]],
    waking: tuple[datetime, datetime],
) -> list[str]:
    """返回违规清单（空 = 合格）。**这个函数一个字都不改 `items`。**"""
    rows = [dict(item) for item in items]
    spans = [(row, _span_of(row)) for row in rows]
    problems: list[str] = []

    for row, span in spans:
        if span is None:
            problems.append(f"「{_title(row)}」缺起止时间")
    timed = [(row, span) for row, span in spans if span is not None]

    # 边界一：生活锚点不许被挤掉
    for anchor in anchors:
        a_span = _span_of(anchor)
        if a_span is None:
            continue
        for row, (start, end) in timed:
            if _is_anchor_row(row, anchor) or not _overlaps(start, end, *a_span):
                continue
            problems.append(
                f"「{_title(row)}」挤掉了作息锚点「{_title(anchor)}」"
                f"（{a_span[0].strftime('%H:%M')}–{a_span[1].strftime('%H:%M')}）"
            )

    # 边界二：不许双订
    for i in range(len(timed)):
        for j in range(i + 1, len(timed)):
            (row_a, (s_a, e_a)), (row_b, (s_b, e_b)) = timed[i], timed[j]
            if _overlaps(s_a, e_a, s_b, e_b):
                problems.append(f"「{_title(row_a)}」与「{_title(row_b)}」同一时段双订")

    # 边界三：总量不许超过当天清醒窗口
    for row, (start, end) in timed:
        if start < waking[0] or end > waking[1]:
            problems.append(
                f"「{_title(row)}」排在清醒窗口之外"
                f"（{start.strftime('%H:%M')}–{end.strftime('%H:%M')}）"
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

    `llm_call(prompt)` 返回 dict（`items` + `reason`）或 JSON 字符串。**不写库**：
    完成态那一半（R-099）落地之前，写库这条路归 `planning.generate_evening_plan`。
    """
    owns_conn = conn is None
    conn = conn or store.connect(None)
    try:
        materials = plan_materials(conn, day)
        anchors = materials["routine_anchors"]
        waking = tuple(_dt(value) for value in materials["waking_window"])
        prompt = build_prompt(materials)
        seen: list[str] = []
        for attempt in range(1, max_replans + 2):
            parsed = _parse_proposal(llm_call(prompt), day)
            proposal = parsed["items"]
            problems = validate_proposal(proposal, anchors=anchors, waking=waking)
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


def _is_anchor_row(row: dict[str, Any], anchor: dict[str, Any]) -> bool:
    if row.get("template_id") and row.get("template_id") == anchor.get("template_id"):
        return True
    return str(row.get("id") or "") == str(anchor.get("id") or "")


def _is_commute(row: dict[str, Any]) -> bool:
    title = _title(row)
    return any(word in title for word in _COMMUTE_WORDS)


def _waking_window(conn: Any, day: str) -> tuple[str, str]:
    """清醒窗口 = 「前一段睡眠结束」→「当天这段睡眠开始」。

    睡眠锚点跨零点（今天 23:30 → 明早 07:30），所以**只查当天那一条会把窗口算反**
    （起点晚于终点 ⇒ 边界三把每份提案都打回）。这里同时取前一天与当天两天的睡眠段。
    真取数跑出来的第一条错就是这个，测试里手喂窗口把它盖住了。
    """
    spans: list[tuple[datetime, datetime]] = []
    for probe in (_shift_day(day, -1), day):
        try:
            rows = planning.enabled_anchors_for_day(conn, probe)
        except Exception:
            rows = []
        for template, start, end in rows:
            if any(word in str(getattr(template, "title", "")) for word in _SLEEP_WORDS):
                spans.append((start, end))

    waking_start = next((end for _start, end in spans if end.date().isoformat() == day), None)
    waking_end = next((start for start, _end in spans if start.date().isoformat() == day), None)
    if waking_start is not None and waking_end is not None and waking_start < waking_end:
        return waking_start.isoformat(), waking_end.isoformat()

    # 拿不全就退回整天，并在材料里照实说这是兜底——不假装知道作息。
    return f"{day}T00:00:00", f"{day}T23:59:00"


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


def _has_completion_state() -> bool:
    """`plan_items` 有没有状态列（R-099）。现核：没有 ⇒ 材料里照实标缺。"""
    try:
        conn = store.connect(None)
    except Exception:
        return False
    try:
        columns = {row[1] for row in conn.execute("pragma table_info(plan_items)")}
    finally:
        conn.close()
    return bool({"status", "done", "completed_at"} & columns)


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
        proposal = plan_with_llm(day, call, conn=conn, max_replans=max_replans)
    except PlanRejected as rejected:
        return [], "L1 那一轮没排成（" + "；".join(rejected.violations[:3]) + "），今天没有项目块"
    except Exception as exc:  # 通道 / 解析 / 网络：照实说，不降级成均分
        return [], f"L1 那一轮没排成（{type(exc).__name__}: {str(exc)[:120]}），今天没有项目块"

    blocks: list[dict[str, Any]] = []
    for item in proposal.items:
        span = _span_of(item)
        if span is None:
            continue
        blocks.append({
            "event_id": f"l1:block-{len(blocks) + 1}",
            "title": _title(item),
            "start_at": span[0].isoformat(),
            "end_at": span[1].isoformat(),
            "kind": "task",
            "conflict_with": [],
            "evidence": {"kind": PROJECT_BLOCK_EVIDENCE_KIND, "planner": "l1",
                         "reason": proposal.reason},
        })
    if not blocks:
        return [], "L1 交回来的提案里没有任何能落的时间块"
    return blocks, proposal.reason
