"""B5 管家包：早报 / 消息待办 digest / 额度预警 的数据收集与消息模板。

三个 cron job 共享这里的 ``build_*`` 收集与 ``format_*`` 模板。全部纯函数、
零 LLM，因此 cron 任务是 ``no_agent``（零 token 成本）——L1 秘书只转发、不进
生成循环（B5 禁区）。早报不含完整工具日志（R3 口径）：只带改动率结论、待批项
条数与额度健康，不逐条贴工具调用记录。

WP-DT-DIGEST（裁定 28.1）在此基础上加了**一整天清单**：早报正文 = 当日清单 +
统计段，20:00 计划后追加次日清单，开机晚了由看门狗补发一次（``morning_body``
是两边共用的唯一组装函数）。清单是为了**手抄进手机日历**——每行一条、钟点在前、
有起止就写起止，**绝不编造结束时间**，也不把没批的东西当成已安排。

「L2 便宜模型生成」由 :mod:`plobi.butler.polish` 提供可选改写（走 B3 额度池
便宜源，便宜优先 + aigw 兜底），默认关闭；模板是永远在线的确定基线。
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

from plobi.agenda import store
from plobi.agenda.dispatch import format_pending
from plobi.agenda.service import AgendaService, get_service
from plobi.agenda.stats import change_report
from plobi.agenda.store import DailyPlan, PlanItem
from plobi.quota.pool import QuotaPool, get_quota_pool

_PLAN_STATUS_LABEL = {
    "empty": "空",
    "pending": "待批",
    "confirmed": "已批",
    "dismissed": "已忽略",
}

# ── 清单（WP-DT-DIGEST）词表与常量 ───────────────────────────────────────────

# events.kind 是冻结词表（meeting/ddl/class/task）；没列到的种类不写标签。
_EVENT_KIND_LABELS = {
    "meeting": "会议",
    "task": "任务",
    "ddl": "截止",
    "class": "课",
}
# 计划项按其类型标：作息锚点 / 项目推进块（evidence.kind，见 agenda/planning.py）。
_PLAN_ITEM_LABELS = {
    "routine": "作息",
    "project_block": "项目块",
}
_WEEKDAYS = "一二三四五六日"
# "08:00–09:40" 正好 11 列；单点时间左对齐补空格，正文起始列两版一致。
_TIME_COLUMN = 11

# 早报标记文件：07:30 的 cron 与看门狗的开机补发共用一个（谁先发谁写）。
DIGEST_STATE_FILENAME = "digest_state.json"


def _as_day(value: Any) -> str:
    """date / datetime / "YYYY-MM-DD..." → "YYYY-MM-DD"。"""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value).strip()[:10]


def _clock(value: Any) -> str:
    """ISO 时间戳 → HH:MM。空值返回空串（调用方据此判断「未写结束」）。"""
    text = str(value or "")
    return text[11:16] if len(text) >= 16 else text


# ── data collection ─────────────────────────────────────────────────────────


def _jsonable(value: Any) -> Any:
    """Coerce domain objects into JSON-dumpable shapes for cron output.

    Why here: 07:30 ``plobi-morning-report`` used to crash on ``Event`` /
    ``DailyPlan`` / ``PlanItem`` rows with ``TypeError: Object of type Event
    is not JSON serializable``. The fix is the contract: cron payloads must
    be plain ``dict`` / ``str`` / ``int`` / ``float`` / ``bool`` / ``None`` /
    ``list`` / ``tuple``. We never fall back to ``default=str`` to "leak" an
    ``Event(id=...)`` representation — that would make the manifest a lie.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            return _jsonable(to_dict())
        except TypeError:
            pass
    raise TypeError(
        f"cannot JSON-serialize {type(value).__name__} "
        f"(use to_dict or stay on primitives)"
    )


def build_morning(
    service: Optional[AgendaService] = None,
    pool: Optional[QuotaPool] = None,
    *,
    days: int = 7,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """收集早报三件套：改动率 + 待批项 + 额度健康。

    改动率沿用 A5 口径（连续 ``days`` 天统计，见 :func:`change_report`）；
    待批项 = ``status == pending`` 的日程改动（含消息来源与手动草稿）；
    额度 = 三源最新探针状态（默认探针无网络，诚实标注 degraded/unavailable）。

    返回**纯 JSON 可序列化**的 dict：``pending`` / ``plan`` 都是经过
    :func:`_jsonable` 的形状——07:30 cron 的 ``json.dumps`` 不再炸。
    """
    service = service or get_service()
    pool = pool or get_quota_pool()

    when = now or datetime.now()
    change = change_report(service, days=days, now=now)
    pending = [_jsonable(event) for event in service.list_pending()]
    quota = [s.as_dict() for s in pool.probe_all()]
    # Last night's 20:00 run writes for_date = that next day = this morning's date.
    plan = _jsonable(service.get_plan(when.date().isoformat()))

    return {
        "date": when.date().isoformat(),
        "change": change,
        "pending": pending,
        "quota": quota,
        "plan": plan,
    }


def build_todo_digest(service: Optional[AgendaService] = None) -> dict[str, Any]:
    """收集消息待办：仅消息来源（source != manual）的待确认改动。

    返回纯 dict/list（``items`` 是 ``Event.to_dict()`` 后的形状），与
    :func:`build_morning` 共用 :func:`_jsonable` 契约。
    """
    service = service or get_service()
    items = [e for e in service.list_pending() if e.source != "manual"]
    return {"count": len(items), "items": [_jsonable(item) for item in items]}


def build_quota_alert(pool: Optional[QuotaPool] = None) -> dict[str, Any]:
    """收集额度预警：健康度在阈值以下（degraded/unavailable）的源。"""
    pool = pool or get_quota_pool()
    pool.probe_all()
    alerts = [s.as_dict() for s in pool.alert_statuses()]
    return {"alerts": alerts}


# ── one-day digest (WP-DT-DIGEST) ────────────────────────────────────────────


def _planned_items_for_digest(service: AgendaService, day: str) -> tuple[Optional[DailyPlan], list[PlanItem]]:
    """该日的计划表头 + **可写进清单**的计划项。

    只有 ``confirmed`` 计划的 items 才进清单；``evidence.kind == "event"`` 的
    引用项一律跳过——events 段已经负责它们（同一个 ``event_id`` 只出现一次，
    以事件为准），而没被 events 段收录只说明那条事件还是 ``pending``／已取消，
    绝不能当成已安排推给手机。``pending`` 计划因此只贡献一个「计划待批」提示。
    """
    conn = store.connect(getattr(service, "db_path", None))
    try:
        plan = store.get_daily_plan(conn, day)
        if plan is None or plan.status != "confirmed":
            return plan, []
        items = [
            item
            for item in store.list_plan_items(conn, plan.id)
            if str((item.evidence or {}).get("kind") or "") != "event"
        ]
        return plan, items
    finally:
        conn.close()


def build_day_digest(
    service: Optional[AgendaService] = None,
    *,
    for_date: Any,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """一整天的清单数据：已确认事件 + 已批计划项 + 待确认条数。

    纯函数、零 LLM、零网络（清单绝不进模型：钟点就是钟点）。``for_date`` 是
    要抄进日历的那一天；``now`` 只用来决定标题写「今天」还是「明天」。
    """
    service = service or get_service()
    when = now or datetime.now()
    if when.tzinfo is not None:
        when = when.replace(tzinfo=None)
    day = _as_day(for_date)
    target = date.fromisoformat(day)

    events = [
        event
        for event in service.list_agenda(
            datetime.combine(target, datetime.min.time()),
            datetime.combine(target, datetime.max.time()).replace(microsecond=0),
        )
        if event.status == "confirmed"
    ]
    events.sort(key=lambda event: (event.start_at, event.id))

    plan, plan_items = _planned_items_for_digest(service, day)
    pending_count = sum(
        1 for event in service.list_pending() if str(event.start_at)[:10] == day
    )

    relative = ""
    if day == when.date().isoformat():
        relative = "今天"
    elif day == (when.date() + timedelta(days=1)).isoformat():
        relative = "明天"
    stamp = f"{target.strftime('%m-%d')}（周{_WEEKDAYS[target.weekday()]}）"

    return {
        "for_date": day,
        "title": f"{relative} {stamp}".strip(),
        "relative": relative,
        "plan_status": plan.status if plan is not None else None,
        "plan_pending": bool(plan is not None and plan.status == "pending"),
        "events": events,
        "plan_items": plan_items,
        "pending_count": pending_count,
    }


# ── digest state: one marker, shared by the 07:30 cron and the catch-up ──────


def digest_state_path() -> Path:
    """``PLOBI_HOME/plobi/digest_state.json``（与看门狗同一套 home 解析）。"""
    try:
        from plobi_constants import get_plobi_home

        root = get_plobi_home() / "plobi"
    except Exception:
        root = Path.home() / ".plobi" / "plobi"
    return root / DIGEST_STATE_FILENAME


def load_digest_state(path: Path | str | None = None) -> dict[str, Any]:
    target = Path(path) if path else digest_state_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def save_digest_state(state: dict[str, Any], path: Path | str | None = None) -> Path:
    target = Path(path) if path else digest_state_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, ensure_ascii=False, indent=2)
    # Atomic write: temp file + rename, so a crash mid-write can never leave a
    # half-written marker that would look like "already sent".
    tmp = target.with_suffix(".tmp")
    try:
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(target)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return target


def morning_sent_for(path: Path | str | None = None) -> Optional[str]:
    """`morning_sent_for` 标记（当天已发的日期），没有则 ``None``。"""
    value = load_digest_state(path).get("morning_sent_for")
    return str(value) if value else None


def mark_morning_sent(for_date: Any, path: Path | str | None = None) -> Path:
    """记录「这一天的早报已经发出去了」——只有发送成功才调用。"""
    state = load_digest_state(path)
    state["morning_sent_for"] = _as_day(for_date)
    return save_digest_state(state, path)


def _morning_plan_label(plan: DailyPlan | dict[str, Any] | None) -> str:
    """empty / pending / confirmed / dismissed / 无 — last night's next-day plan."""
    if plan is None:
        return "无"
    status = plan.status if isinstance(plan, DailyPlan) else plan.get("status")
    return _PLAN_STATUS_LABEL.get(str(status or ""), "无")


# ── message templates ────────────────────────────────────────────────────────


def format_morning(data: dict[str, Any]) -> str:
    """早报正文。R3 口径：结论 + 计数 + 健康，不贴工具日志。"""
    change = data["change"]
    pending = data["pending"]
    quota = data["quota"]

    lines = [f"[Plobi] 早报 {data['date']}"]

    if change["total"] == 0:
        lines.append("改动率: 近 %d 天暂无落定动作" % change["days"])
    else:
        verdict = "达标" if change["meets_target"] else "超标"
        lines.append(
            "改动率: %.0f%%（%s，目标 ≤ %.0f%%）"
            % (change["rate"] * 100, verdict, change["target"] * 100)
        )

    lines.append("待批项: %d 条" % len(pending))
    lines.append("昨夜计划: %s" % _morning_plan_label(data.get("plan")))

    if not quota:
        lines.append("额度: 无探针数据")
    else:
        summary = " · ".join("%s=%s" % (s["name"], s["health"]) for s in quota)
        lines.append("额度: %s" % summary)

    return "\n".join(lines)


def format_day_digest(data: dict[str, Any]) -> str:
    """一整天清单正文：每行一条、钟点在前，照着抄进手机日历就行。

    有起止写 ``08:00–09:40``；**没有 ``end_at`` 就只写开始并标「未写结束」**，
    不编 1 小时、不编 9:00。空日照样发，写明当天没有已确认的安排。
    """
    lines = ["[Plobi] %s" % str(data.get("title") or data.get("for_date") or "")]
    if data.get("plan_pending"):
        lines[0] += "（计划待批，以下为已确认事件）"

    rows = _digest_rows(data)
    if rows:
        lines.extend(_digest_line(row) for row in rows)
    else:
        relative = str(data.get("relative") or "")
        lines.append("（%s没有已确认的安排）" % (relative or "当天"))

    lines.append("—")
    lines.append("待确认 %d 条（桌面看板处理）" % int(data.get("pending_count") or 0))
    return "\n".join(lines)


def _digest_rows(data: dict[str, Any]) -> list[tuple[str, Optional[str], str, str]]:
    """事件 + 计划项合成一份按 ``start_at`` 排序的 ``(start, end, title, label)``。"""
    rows: list[tuple[str, Optional[str], str, str]] = []
    for event in data.get("events") or []:
        rows.append(
            (
                event.start_at,
                event.end_at,
                event.title,
                _EVENT_KIND_LABELS.get(event.kind, ""),
            )
        )
    for item in data.get("plan_items") or []:
        evidence = item.evidence if isinstance(item.evidence, dict) else {}
        rows.append(
            (
                item.start_at,
                item.end_at,
                item.title,
                _PLAN_ITEM_LABELS.get(str(evidence.get("kind") or ""), ""),
            )
        )
    rows.sort(key=lambda row: (row[0] or "", row[2]))
    return rows


def _digest_line(row: tuple[str, Optional[str], str, str]) -> str:
    start_at, end_at, title, label = row
    start = _clock(start_at)
    end = _clock(end_at) if end_at else ""
    span = "%s–%s" % (start, end) if end else start
    parts = [str(title)]
    if label:
        parts.append(label)
    if not end:
        parts.append("未写结束")
    return "%-*s  %s" % (_TIME_COLUMN, span, " · ".join(parts))


def morning_body(
    now: Optional[datetime] = None,
    *,
    report: Optional[dict[str, Any]] = None,
    service: Optional[AgendaService] = None,
    pool: Optional[QuotaPool] = None,
    transform_stats: Optional[Callable[[str], Optional[str]]] = None,
) -> str:
    """早报正文 = 当天清单 + 空行 + 统计段（清单在前）。

    07:30 的 cron 与看门狗的开机补发**共用这一个组装函数**（不复制模板）。
    ``transform_stats`` 是统计段的可选改写钩子（``PLOBI_BUTLER_POLISH``）：
    它只拿得到统计段，永远碰不到清单里的钟点。
    """
    when = now or datetime.now()
    data = report if report is not None else build_morning(service, pool, now=when)
    stats = format_morning(data)
    if transform_stats is not None:
        rewritten = transform_stats(stats)
        if rewritten:
            stats = rewritten
    digest = format_day_digest(build_day_digest(service, for_date=when.date(), now=when))
    return "%s\n\n%s" % (digest, stats)


def format_todo_digest(data: dict[str, Any], *, service: Optional[AgendaService] = None) -> str:
    """消息待办 digest：逐条列出待确认的消息改动（复用 §7 卡片格式）。

    ``data["items"]`` 现在是 ``Event.to_dict()`` 后的 JSON 安全 dict（与
    :func:`build_todo_digest` 共用 :func:`_jsonable` 契约）。``format_pending``
    仍要求 ``Event`` dataclass（§7 UI 契约冻结），所以按 id 走服务回拿 Event
    —— 一次 in-process 查表，无额外 N+1。
    """
    if data["count"] == 0:
        return "[Plobi] 消息待办：暂无待确认的消息改动。"

    lines = ["[Plobi] 消息待办（%d 条待你决定）" % data["count"]]
    if service is None:
        service = get_service()
    for item in data["items"]:
        event_id = item.get("id") if isinstance(item, dict) else None
        event = service.get(event_id) if event_id else None
        if event is None:
            # 字典里 title/start_at/source 都在，兜底用 §7 格式直接拼。
            title = item.get("title", "")
            start_at = item.get("start_at", "")
            lines.append(
                f"【待确认】{title}\n时间: {start_at}\n回复: 确认 / 忽略"
            )
            continue
        lines.append(format_pending(event))
    return "\n\n".join(lines)


def format_quota_alert(data: dict[str, Any]) -> str:
    """额度预警正文。无异常源时返回空串（调用方不推送）。"""
    if not data["alerts"]:
        return ""

    lines = ["[Plobi] 额度预警：以下额度源状态异常"]
    for s in data["alerts"]:
        detail = (" — %s" % s["detail"]) if s.get("detail") else ""
        lines.append("- %s（%s）: %s%s" % (s["name"], s["kind"], s["health"], detail))
    return "\n".join(lines)
