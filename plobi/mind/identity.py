"""身份那一格：两种写法（profile 家名 / 名册 name）→ 名册 name（不可变身份）。

为什么单独一格而不是塞进 registry：`plobi/agents/registry.py` 由别线在派时动不了，
而"家名/名册名 → name"这条解析会话、记忆目录、档案入口三处都要用——放这里，
`tools/memory_tool.resolve_agent_slug` 与 `gateway/platforms/api_server._handle_create_session`
都调它，只此一处，不各写一遍。

rules：
- 认得出就返回名册 `name`；
- 名册里本来就叫这个名字，原样返回；
- **认不出回空串**（不许猜——猜错等于把 A 的记忆写到 B 名下）；
- 名册读不到也回空串，调用方按"没绑定"处理。
"""
from __future__ import annotations


def resolve_agent_name(scope: str) -> str:
    text = str(scope or "").strip()
    if not text:
        return ""
    try:
        from plobi.agents.registry import entry_for_profile, load_registry

        entry = entry_for_profile(text)
        if entry is not None and getattr(entry, "name", ""):
            return str(entry.name)
        for candidate in load_registry().entries():
            if str(getattr(candidate, "name", "")) == text:
                return text
    except Exception:  # noqa: BLE001 - 名册读不到就当认不出
        return ""
    return ""
