"""引用锚点（``reply_to``）的形状 —— 一条消息可以**回多条**。

裁定 196 ④ 起，``reply_to`` 是「这条 user 消息在回哪一条」的**锚点**：落库在
``messages.reply_to``（一列 TEXT），引文由**桌面端**自己在转录里按 id 解析出来
（``lib/chat-messages.ts``），后端从头到尾只当它是「一串 id」。

**为什么这一份要单独立一个模块**：协议最初只允许**一个**锚点，整条链路都按单值
写死。改成「可多个」时，这条链路上有**四个会静默把 list 弄坏的地方**——都不报错，
引用就没了：

1. RPC 入口（``tui_gateway/server.py``）原来只收 ``str | int`` ⇒ list 变成**空串**，
   再被 ``if reply_to:`` 吃掉 ⇒ 整条引用无声消失；
2. ``agent/turn_context.py`` 原来 ``str(reply_to)`` ⇒ list 变成 ``"['a', 'b']"``
   字面量，**不报错**，落库后指向空气；
3. ``run_agent.resolve_reply_to`` 原来 ``.strip()`` ⇒ 对 list 抛 ``AttributeError``，
   被外层的 ``except Exception`` 吞成 warning ⇒ **静默不落库**；
4. ``plobi_state.append_message`` 原来把值直绑进 TEXT 列 ⇒ 绑 list 报 sqlite 错
   （这个是响亮失败，反而最安全）。

**列里的形状**：多锚点存 **JSON 数组**（``["12","34"]``）；旧的裸 id 行照读
（读侧当作单元素数组）。Agent 侧 ``plugins/plobi-core/channel.py`` 的 ``say``
仍写裸 id ⇒ 读侧兼容，不用动它。

**真源就这一处**：形状的归一与编解码只有下面这三个函数，别处别再自己
``str()`` 一遍。
"""

from __future__ import annotations

import json
from typing import Any, List, Optional, Sequence


def normalize_reply_to(value: Any) -> List[str]:
    """把各种形状的「在回哪几条」归一成**一串 id**（去空、去重、保序）。

    收：``None`` / ``str`` / ``int`` / 一串 ``str|int``。``bool`` 不算 id
    （它是 ``int`` 的子类，``True`` 会变成 ``"True"``）。
    """
    if value is None or isinstance(value, bool):
        return []

    if isinstance(value, (str, int)):
        raw: Sequence[Any] = [value]
    elif isinstance(value, Sequence):
        raw = list(value)
    else:
        return []

    out: List[str] = []

    for item in raw:
        if item is None or isinstance(item, bool):
            continue

        text = str(item).strip()

        if text and text not in out:
            out.append(text)

    return out


def encode_reply_to(value: Any) -> Optional[str]:
    """落库形状：一串 id ⇒ **JSON 数组**；一个裸 id ⇒ 原样（Agent 那条路不变）。"""
    if isinstance(value, str):
        return value.strip() or None

    ids = normalize_reply_to(value)

    if not ids:
        return None

    return json.dumps(ids, ensure_ascii=False)


def decode_reply_to(value: Any) -> List[str]:
    """读库形状：JSON 数组 ⇒ 那几个 id；裸 id ⇒ 单元素数组。"""
    if value is None:
        return []

    if isinstance(value, (list, tuple)):
        return normalize_reply_to(value)

    text = str(value).strip()

    if not text:
        return []

    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError):
            return [text]

        return normalize_reply_to(parsed)

    return [text]
