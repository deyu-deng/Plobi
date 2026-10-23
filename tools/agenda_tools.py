"""日程的两枚原子（`WP-AGENDA-ATOMS`）：`agenda_read` 读这一面，`agenda_write` 写这一面。

形状就是裁 185 要的那种：**参数全是筛选条件与记录自己的字段，没有 `intent`／`action` 这种选分支的东西**。
「排一下明天」不是一种工具，是模型用原子组合出来的一段活：`pool_read` 读材料 → `agenda_read` 看已有的一面
→ 想 → `agenda_write` 写。旧那张嘴把这段流程编码成 `intent=plan_day` 再带 `action=create|delete|move_block…`，
每个枚举都是一处「模型必须猜对才走对」的地方——10-17 那颗 P0（说一句「排一下明天」反而把排好的 8 块抹掉）
就是 `intent=plan_day` 不带 `action` 时走了另一条 DELETE 分支。

注册即全员可用（`toolsets.py` 默认束那一格，与 `pool_read`／`pool_mark` 同一 rung、同一理由）：
读得懂、也改得动自己家的日程是每个角色都需要的手，按角色过滤就是再造一个特权入口。

写入**落真 `events` 行**（裁 175）：这一枚产出的每条都有自己的 `evt_…` id，不是 `l1:block-N` 那种自造身份——
否则面上的勾点了没反应，手机同步看到的与库里查到的也不是同一件事。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from tools.registry import registry

#: 读回来的条目数上限。理由与池那一枚相同：不是隐私，是 prompt——一次回几万条正文会把上下文撑爆。
DEFAULT_LIMIT = 100
MAX_LIMIT = 500

#: 能改的那几格（与 `AgendaService.update_manual` 的白名单同一份，不另开一条写入口）。
EDITABLE_FIELDS = ("title", "start_at", "end_at", "kind", "location", "notes")
#: 状态那一格只认库里已有的两种值；给别的照实点名退回，不猜模型想要什么。
ACCEPTED_STATUS = ("confirmed", "cancelled")

_AGENDA_READ_SCHEMA: Dict[str, Any] = {
    "name": "agenda_read",
    "description": (
        "读日程这一面：按时间范围捞库里的事件行，返回**带 id 的结构化条目**。\n\n"
        "所有参数都是**筛选条件**（全部可省）：`start`／`end` 时间范围（`YYYY-MM-DD` 或带时分秒）、"
        "`source` 哪条信源（manual／timetable／zju…）、`kind` 类型（class／task／ddl／rest…）、"
        "`contains` 标题或备注里含这段文字、`include_cancelled` 要不要连已取消的一起给、`limit` 最多几条。\n"
        "一个参数都不给 = 今天到明天那一批。除条目外还带回 `pending`（等人认下的那一队）。\n"
        "这一枚**只读**，什么都不会改。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "start": {"type": "string", "description": "范围起点（含）。日期或日期时间。省略=今天。"},
            "end": {"type": "string", "description": "范围终点（含当天）。省略=明天。"},
            "source": {"type": "string", "description": "只看这一条信源落进来的那些行。"},
            "kind": {"type": "string", "description": "只看这一类：class／task／ddl／rest／meeting…"},
            "contains": {"type": "string", "description": "标题或备注里包含这段文字的行。"},
            "include_cancelled": {
                "type": "boolean",
                "description": "true 连已取消的一起读；省略（false）只要活着的。",
            },
            "limit": {"type": "integer", "description": f"最多返回几条，默认 {DEFAULT_LIMIT}，上限 {MAX_LIMIT}。"},
        },
    },
}

_AGENDA_WRITE_SCHEMA: Dict[str, Any] = {
    "name": "agenda_write",
    "description": (
        "写日程这一面：`items` 是一批记录，**一条一件原子事**，逐条落库、逐条回报。\n\n"
        "每条记录的字段：`event_id`（给了就是改那一行，不给就是**新建一行**）、`title`、`start_at`、`end_at`、"
        "`kind`、`location`、`notes`；`status` 只能是 `confirmed`（把等人认下的那条认下）或 `cancelled`"
        "（不要那条了：待确认的回滚、已经落定的删掉）；`completed` 是**人承认这条做完了**那一刻，传 false 撤销。\n"
        "时间轴重叠**不拒写**——重叠是事实，由面上标红，不是把写挡住的理由（人令不拒）。\n"
        "没有 `action`／`intent`／`mode`：要改哪条就带它的 `event_id`，先 `agenda_read` 拿到真实 id。\n"
        "新建的都落成**真实 events 行**（各带自己的 `evt_…` id），面上的勾、手机同步、事后对账认的就是这一行。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "description": "要写的那几条，每条一个对象。",
                "items": {
                    "type": "object",
                    "properties": {
                        "event_id": {"type": "string", "description": "改哪一行；省略 = 新建一行。"},
                        "title": {"type": "string", "description": "这条叫什么。新建时必给。"},
                        "start_at": {"type": "string", "description": "开始时间（新建时必给）。"},
                        "end_at": {"type": "string", "description": "结束时间；省略 = 没有结束（截止类就这样）。"},
                        "kind": {"type": "string", "description": "class／task／ddl／rest／meeting…默认 task。"},
                        "location": {"type": "string", "description": "地点。"},
                        "notes": {"type": "string", "description": "备注。"},
                        "status": {
                            "type": "string",
                            "enum": list(ACCEPTED_STATUS),
                            "description": "confirmed = 把待确认那条认下；cancelled = 不要这条了。",
                        },
                        "completed": {"type": "boolean", "description": "人承认这条做完了；false 撤销承认。"},
                    },
                },
            },
        },
        "required": ["items"],
    },
}


def _service():
    from plobi.agenda.service import get_service

    return get_service()


def _as_time(value: Any) -> Optional[str]:
    """把模型给的时间核成可解析的 ISO 串；认不出来返回 None（由调用方点名退回，不猜）。"""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return text


def _brief(event: Any) -> Dict[str, Any]:
    """一条事件 → 给模型看的那份形状。`prev_value`（回滚用的旧值）不投出去。"""
    data = event.to_dict() if hasattr(event, "to_dict") else dict(vars(event))
    data.pop("prev_value", None)
    evidence = data.get("evidence")
    if isinstance(evidence, str):
        try:
            data["evidence"] = json.loads(evidence)
        except ValueError:
            pass
    return data


def _bound(value: Optional[str], *, end_of_day: bool = False) -> Optional[str]:
    """给的是**光一天**（`2026-10-21`）就铺成那一整天。

    库里比的是 `start_at >= from AND start_at <= to`（`store.list_events:623`），
    所以直接把 `2026-10-21` 当终点用会被解析成那天 `00:00:00` ⇒ **当天 00:01 之后的全查不到**，
    而模型说「读 10-21 那天」要的正是那一整天。起点同理铺到 `00:00:00`。
    """
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) == 10:  # 光一天
        return f"{text}T23:59:59" if end_of_day else f"{text}T00:00:00"
    return text


def agenda_read(
    start: Optional[str] = None,
    end: Optional[str] = None,
    source: Optional[str] = None,
    kind: Optional[str] = None,
    contains: Optional[str] = None,
    include_cancelled: bool = False,
    limit: int = DEFAULT_LIMIT,
    **_kw: Any,
) -> str:
    """按时间范围读日程这一面（只读）。"""
    for label, raw in (("start", start), ("end", end)):
        if raw and _as_time(raw) is None:
            return json.dumps(
                {"ok": False, "error": f"`{label}` 这个时间读不出来：{raw!r}——要 `YYYY-MM-DD` 或带时分秒。"},
                ensure_ascii=False,
            )

    start_text = _bound(start)
    end_text = _bound(end, end_of_day=True)
    capped = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))

    try:
        service = _service()
        events = service.list_agenda(start_text, end_text, include_cancelled=bool(include_cancelled))
    except Exception as exc:  # noqa: BLE001 - 读不到要说得出为什么，不许回空列表冒充「日程是空的」
        return json.dumps(
            {"ok": False,
             "error": f"读日程失败：{type(exc).__name__}: {exc}",
             "range": {"start": start_text, "end": end_text}},
            ensure_ascii=False,
        )

    needle = str(contains or "").strip().lower()
    items: List[Dict[str, Any]] = []
    for event in events:
        if source and str(event.source) != str(source).strip():
            continue
        if kind and str(event.kind) != str(kind).strip():
            continue
        if needle and needle not in f"{event.title or ''} {event.notes or ''} {event.location or ''}".lower():
            continue
        items.append(_brief(event))

    try:
        pending = [_brief(event) for event in service.list_pending()]
    except Exception:  # noqa: BLE001 - 那一队只是背景信息，读不到不影响这一批条目
        pending = []

    return json.dumps(
        {
            "ok": True,
            "count": len(items),
            "returned": items[:capped],
            "truncated": len(items) > capped,
            "range": {"start": start_text, "end": end_text},
            "pending": pending,
        },
        ensure_ascii=False,
    )


def agenda_write(items: Optional[Any] = None, **_kw: Any) -> str:
    """逐条写日程这一面：没给 `event_id` 就新建一行，给了就改那一行。"""
    if isinstance(items, dict):
        rows: List[Any] = [items]
    elif isinstance(items, (list, tuple)):
        rows = list(items)
    else:
        return json.dumps({"ok": False, "error": "agenda_write 需要 items（一个对象或对象数组）"},
                          ensure_ascii=False)
    if not rows:
        return json.dumps({"ok": False, "error": "items 是空的——这一次什么都别写。"}, ensure_ascii=False)
    if len(rows) > MAX_LIMIT:
        return json.dumps(
            {"ok": False, "error": f"items 给了 {len(rows)} 条，超过 {MAX_LIMIT} 条上限——分批写。"},
            ensure_ascii=False,
        )

    try:
        service = _service()
    except Exception as exc:  # noqa: BLE001 - 日程层没起来也要出声，不是抛个 ImportError 当没事
        return json.dumps({"ok": False, "error": f"日程库不可用：{type(exc).__name__}: {exc}"},
                          ensure_ascii=False)

    applied: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    for index, item in enumerate(rows):
        try:
            applied.append(_write_one(service, item))
        except Exception as exc:  # noqa: BLE001 - 一条坏了不吞掉整批：逐条点名
            failed.append({"index": index, "item": item if isinstance(item, dict) else str(item)[:200],
                           "error": f"{type(exc).__name__}: {exc}"})

    return json.dumps(
        {
            "ok": not failed,
            "written": len(applied),
            "failed": len(failed),
            "items": applied,
            "errors": failed,
            "note": "逐条落库：第 N 条坏了不影响前 N-1 条，重试只针对 errors 里点名的那几条。",
        },
        ensure_ascii=False,
    )


def _write_one(service: Any, item: Any) -> Dict[str, Any]:
    """一条记录的写法：改字段 → 认下／取消 → 完成态。每一步都走 `AgendaService`，不自己拼 SQL。"""
    from plobi.agenda.service import EventNotFound

    if not isinstance(item, dict):
        raise TypeError(f"items 里有一条不是对象：{type(item).__name__}")

    event_id = str(item.get("event_id") or "").strip() or None
    status = str(item.get("status") or "").strip().lower() or None
    if status and status not in ACCEPTED_STATUS:
        raise ValueError(f"`status` 只认 {list(ACCEPTED_STATUS)}，给的是 {status!r}")
    completed = item.get("completed")

    if event_id is None:
        title = str(item.get("title") or "").strip()
        if not title:
            raise ValueError("新建一条日程得有 `title`（没写 `event_id` 就是要新建）")
        start_at = _as_time(item.get("start_at"))
        if start_at is None:
            raise ValueError(f"新建一条日程得有读得出来的 `start_at`，给的是 {item.get('start_at')!r}")
        event = service.create_manual(
            title=title,
            start_at=start_at,
            end_at=_as_time(item.get("end_at")),
            kind=str(item.get("kind") or "").strip() or "task",
            location=item.get("location"),
            notes=item.get("notes"),
        )
        return {"action": "created", **_brief(event)}

    patch: Dict[str, Any] = {}
    for field in EDITABLE_FIELDS:
        if item.get(field) is None:
            continue
        value = item[field]
        if field in {"start_at", "end_at"}:
            value = _as_time(value)
            if value is None:
                raise ValueError(f"`{field}` 这个时间读不出来：{item[field]!r}")
        patch[field] = value

    try:
        current = service.get(event_id)
    except EventNotFound:
        raise EventNotFound(f"库里没有 `event_id={event_id}` 这一行——先 agenda_read 拿真实 id") from None

    changed: List[str] = []
    if patch:
        service.update_manual(event_id, **patch)
        changed += sorted(patch)

    if status == "cancelled":
        # 待确认那一条走 `dismiss`（它自己分得清「回滚上一值」与「这条本来就是新提议」）；
        # 已经落定的那条按旧 `action=delete` 的同一出口删掉。
        if str(getattr(current, "status", "")) == "pending":
            survived = service.dismiss(event_id)
            return {"action": "dismissed", "event_id": event_id,
                    "still_there": survived is not None, "changed": changed}
        service.delete(event_id)
        return {"action": "deleted", "event_id": event_id, "changed": changed}

    if status == "confirmed":
        service.confirm(event_id)
        changed.append("status")

    if completed is not None:
        service.set_completed(event_id, completed=bool(completed))
        changed.append("completed")

    try:
        fresh = service.get(event_id)
    except EventNotFound:  # 写完了却读不回来：只回 id，不假装什么都没发生
        return {"action": "updated" if changed else "no-op", "event_id": event_id, "changed": changed}
    return {"action": "updated" if changed else "no-op", "changed": changed, **_brief(fresh)}


def _handler(fn) -> Callable:
    return lambda args, **kw: fn(**args)


registry.register(
    name="agenda_read",
    toolset="agenda",
    schema=_AGENDA_READ_SCHEMA,
    handler=_handler(agenda_read),
    description="按时间范围读日程这一面（只读）",
    emoji="🗓",
)

registry.register(
    name="agenda_write",
    toolset="agenda",
    schema=_AGENDA_WRITE_SCHEMA,
    handler=_handler(agenda_write),
    description="逐条写日程这一面（新建／改名改时／取消／承认完成）",
    emoji="🗓",
)
