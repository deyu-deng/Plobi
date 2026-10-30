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

def resolve_agent_for_path(path: str) -> str:
    """按会话所在目录认 Agent：名录行的 ``project_path`` / ``project_paths`` 最长前缀命中。

    为什么靠目录：桌面"进某个 Agent"那一下已经把它自己的工作目录种进会话（`$currentCwd`），
    而 profile 家名那一跳随 profile 退休断了 ⇒ 用目录当身份线索不需要任何前端改动，
    课程那些行本来就有 ``project_path=D:/Cloud/Courses/<学期-课名>``。
    认不出（没有一行的目录是这个路径的前缀）回空串，调用方按 L1 处理——**不猜**。
    """
    target = str(path or "").strip()
    if not target:
        return ""
    norm = lambda value: str(value or "").replace("\\", "/").rstrip("/").lower()
    needle = norm(target)
    if not needle:
        return ""
    best_name, best_len = "", -1
    try:
        from plobi.agents.registry import load_registry

        rows = load_registry().entries()
    except Exception:  # noqa: BLE001 - 名册读不到就当没绑定
        return ""
    for entry in rows:
        candidates = [getattr(entry, "project_path", "") or ""]
        candidates += list(getattr(entry, "project_paths", ()) or ())
        for raw in candidates:
            folder = norm(raw)
            if not folder:
                continue
            if needle == folder or needle.startswith(folder + "/"):
                if len(folder) > best_len:
                    best_name, best_len = str(getattr(entry, "name", "") or ""), len(folder)
    return best_name
