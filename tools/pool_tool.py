"""信息池的读手（WP-POOL-READ-ANY）：两枚原子，**每个角色都拿得到**。

- `pool_read`：按**筛选条件**捞池里的条目，返回结构化条目**含正文**。
- `pool_mark`：给某条记一个「我动过了」的记号。**池不是队列**——标完那条还在
  （`specs/source-pool.md` §3.5），所以这里没有"取走并删除"这种形状。

参数全是筛选条件，没有 `action`／`intent`／`mode`／`purpose` 这类选分支的东西
（裁定 185：业务流程由模型用原子工具组合，不编码成参数）。读完之后拿这些信息做什么，
是模型的判断，不归这枚工具管。

工具本体不认识任何下游目的地，也不认识"哪些角色该看到它"：注册即全员可用
（配给方式见 `toolsets.py` 的默认束那一格，与 `memory` 同一 rung）。
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, Optional

from tools.registry import registry

_POOL_READ_SCHEMA: Dict[str, Any] = {
    "name": "pool_read",
    "description": (
        "读信息池：采集器已经收进来的结构化条目，按筛选条件捞，返回含正文的条目列表。\n\n"
        "所有参数都是**筛选条件**（可组合，全部可省）：`source` 哪个信源、`kind` 条目类型、"
        "`visibility` 那一面的可见性（私聊／群聊这类）、`conversation` 哪一扇窗口、"
        "`since` 起点时间（ISO，如 `2026-10-19` 或带时分秒）、`contains` 标题或正文里含这段文字、"
        "`acted` 是否已标记过、`limit` 最多几条（默认 50，上限 500）。\n"
        "一个参数都不给 = 最近的一批。返回按时间倒序，每条带 `body` 正文，"
        "并带 `visibility` 与 `conversation`（空串 = 采它那一轮还没记这一格，不是「不属于任何窗口」）。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "source": {
                "type": "string",
                "description": "只看这个信源的条目，如 wechat / dingtalk / calendar。",
            },
            "kind": {
                "type": "string",
                "description": "条目类型，如 message / event / ddl / note。",
            },
            "visibility": {
                "type": "string",
                "description": "按可见性筛，如 private（私聊）/ group（群聊）。精确匹配；信源没记这一格的条目筛不到。",
            },
            "conversation": {
                "type": "string",
                "description": "哪一扇窗口（群名／会话 id，信源给什么存什么）。精确匹配；要先从读回来的条目里拿那个值。",
            },
            "since": {
                "type": "string",
                "description": "只要这个时间之后的条目（ISO 日期或日期时间）。",
            },
            "contains": {
                "type": "string",
                "description": "标题或正文里包含这段文字的条目。",
            },
            "acted": {
                "type": "boolean",
                "description": "true 只要标记过的，false 只要没标记过的；省略 = 都要。",
            },
            "limit": {
                "type": "integer",
                "description": "最多返回几条，默认 50，上限 500。",
            },
        },
    },
}

_POOL_MARK_SCHEMA: Dict[str, Any] = {
    "name": "pool_mark",
    "description": (
        "给池里某一条记一个「我动过了」的记号：`source` + `external_id` 定位那一条"
        "（就是 `pool_read` 返回里的那两个字段）。\n"
        "**这只是记号**——那条还在池里，别人照样读得到，也不是队列出队。"
        "`acted` 省略为 true；传 false 撤掉记号。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "那一条来自哪个信源。"},
            "external_id": {"type": "string", "description": "那条在信源里的稳定 id。"},
            "acted": {"type": "boolean", "description": "true 记上（默认），false 撤掉。"},
        },
        "required": ["source", "external_id"],
    },
}


def _pool_module():
    """惰性拿池的公开面：那一层不可用时要说得出为什么，不是抛个 ImportError 就当没这事。"""
    from plobi.pool import PoolError, mark_acted, query, stats

    return PoolError, mark_acted, query, stats


def pool_read(
    source: Optional[str] = None,
    kind: Optional[str] = None,
    visibility: Optional[str] = None,
    conversation: Optional[str] = None,
    since: Optional[str] = None,
    contains: Optional[str] = None,
    acted: Optional[bool] = None,
    limit: int = 50,
    **_kw: Any,
) -> str:
    """按筛选条件读池，返回含正文的结构化条目。"""
    try:
        _PoolError, _mark, query, stats = _pool_module()
    except Exception as exc:  # noqa: BLE001 - 池这一层没装好也要出声
        return json.dumps({"ok": False, "error": f"信息池不可用：{exc}"}, ensure_ascii=False)

    filters = {
        k: v
        for k, v in (
            ("source", source), ("kind", kind), ("visibility", visibility),
            ("conversation", conversation), ("since", since),
            ("contains", contains), ("acted", acted),
        )
        if v not in (None, "")
    }
    try:
        items = query(**filters, limit=limit)
    except Exception as exc:  # noqa: BLE001 - 读不到就说读不到，别回一个空列表装成"池里没有"
        return json.dumps(
            {"ok": False, "error": f"读信息池失败：{exc}", "filters": filters},
            ensure_ascii=False,
        )
    try:
        _PoolError, _mark, _query, stats = _pool_module()
        counts = stats()
    except Exception:  # noqa: BLE001 - 总数只是背景信息，读不到不影响这一批条目
        counts = {}
    return json.dumps(
        {
            "ok": True,
            "count": len(items),
            "filters": filters,
            "items": items,
            "pool": counts,
        },
        ensure_ascii=False,
    )


def pool_mark(
    source: Optional[str] = None,
    external_id: Optional[str] = None,
    acted: bool = True,
    **_kw: Any,
) -> str:
    """给一条记上／撤掉「我动过了」的记号；那条仍在池里。"""
    if not (source or "").strip() or not (external_id or "").strip():
        return json.dumps(
            {"ok": False, "error": "pool_mark 需要 source 与 external_id（两者都来自 pool_read 的返回）"},
            ensure_ascii=False,
        )
    try:
        _PoolError, mark_acted, _query, stats = _pool_module()
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "error": f"信息池不可用：{exc}"}, ensure_ascii=False)

    try:
        matched = mark_acted(source.strip(), external_id.strip(), acted=bool(acted))
    except Exception as exc:  # noqa: BLE001
        return json.dumps(
            {"ok": False, "error": f"标记失败：{exc}", "source": source, "external_id": external_id},
            ensure_ascii=False,
        )
    if not matched:
        return json.dumps(
            {
                "ok": False,
                "error": (
                    f"池里没有这一条（source={source!r}, external_id={external_id!r} 对不上）"
                    "——先调 pool_read 拿到那两条的真实取值"
                ),
                "source": source,
                "external_id": external_id,
            },
            ensure_ascii=False,
        )
    return json.dumps(
        {
            "ok": True,
            "source": source,
            "external_id": external_id,
            "acted": bool(acted),
            "note": "只是记号，那条还在池里",
            "pool": stats(),
        },
        ensure_ascii=False,
    )


def _handler(fn) -> Callable:
    return lambda args, **kw: fn(**args)


registry.register(
    name="pool_read",
    toolset="pool",
    schema=_POOL_READ_SCHEMA,
    handler=_handler(pool_read),
    description="按筛选条件读信息池（含正文）",
    emoji="🗂",
)

registry.register(
    name="pool_mark",
    toolset="pool",
    schema=_POOL_MARK_SCHEMA,
    handler=_handler(pool_mark),
    description="给池里某一条记一个「我动过了」的记号",
    emoji="🗂",
)
