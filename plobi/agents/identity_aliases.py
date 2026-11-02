"""身份别名表：改过名以后，**旧写法照样找得到人**。

为什么要有这一张表（2026-11-02 现读）：名册那一行的 ``name`` 就是身份（裁定 192），
可它同时被十几处当钥匙用——总账的 ``sessions.agent_id``、身份文件、它那一份排程、
记忆那份目录、看板上的 assignee、笔记的 ``belongs:``、外部会话那份索引。改名时这些
都能一起搬，但**搬不掉的还有别人手里那句话**：用户/模型记着的旧名字、写在旧笔记里
的旧写法、``@session:<旧名>/<id>`` 这种历史链接。没有别名表，那些地方就变成
「名册里没有叫「X」的 Agent」——而这句话是**假**的，那个人只是换了名字。

边界（重要）：这张表**只服务于解析**，不参与任何存在性判据。判据照旧只问名册那一行
（裁定 192：读路径问名册行，不问家目录）。所以这里没有"别名也算一个 Agent"这种口子。

落点：共享层 ``<共享根>/plobi/identity-aliases.json``，形状 ``{旧写法: 现名}``。
链式（A→B→C）在读侧展开，写侧顺手压平，最长跟 8 跳，防自环。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_MAX_HOPS = 8


def alias_path() -> Path:
    """别名表那份文件（共享层，与名册同一把根）。"""
    from plobi_constants import get_shared_plobi_root

    return get_shared_plobi_root() / "plobi" / "identity-aliases.json"


def _read() -> dict[str, str]:
    path = alias_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("identity aliases unreadable (%s): %s", path, exc)
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        str(old).strip(): str(new).strip()
        for old, new in raw.items()
        if str(old).strip() and str(new).strip()
    }


def _write(table: dict[str, str]) -> None:
    path = alias_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(table, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        os.replace(tmp, path)
    except OSError as exc:
        logger.warning("identity aliases not written (%s): %s", path, exc)


def resolve_identity_alias(name: str) -> str:
    """旧写法 → 现名。不是别名就原样回（空进空出）。

    大小写不敏感：调用方手里常常是**归一过的**写法（``normalize_profile_name``
    会小写），而表里记的是名册那一行的原样名字。
    """
    raw = (name or "").strip()
    if not raw:
        return ""
    table = _read()
    if not table:
        return raw
    folded = {old.casefold(): new for old, new in table.items()}
    current = raw
    seen = {current}
    for _ in range(_MAX_HOPS):
        nxt = table.get(current) or folded.get(current.casefold())
        if not nxt or nxt in seen:
            break
        current = nxt
        seen.add(current)
    return current


def record_alias(old: str, new: str) -> None:
    """记一条「旧写法 → 现名」。同一个旧写法再改一次名 ⇒ 覆盖（表只答"现在叫什么"）。"""
    old_key = (old or "").strip()
    new_key = (new or "").strip()
    if not old_key or not new_key or old_key == new_key:
        return
    table = _read()
    # 压平：旧写法本身若是别名的话，链子直接指到新名，别留 A→B→C。
    table[old_key] = new_key
    for alias, target in list(table.items()):
        if target == old_key:
            table[alias] = new_key
    _write(table)


def aliases_of(current: str) -> list[str]:
    """现名 → 它历史上用过的写法（给"改名之后还能回看"那一类用）。"""
    target = (current or "").strip()
    if not target:
        return []
    return sorted(old for old, new in _read().items() if new == target)


def drop_aliases_for(current: str) -> list[str]:
    """清掉指向某个现名的别名（删行时用，免得别名指向一个不存在的人）。"""
    target = (current or "").strip()
    table = _read()
    removed = sorted(old for old, new in table.items() if new == target)
    if removed:
        for old in removed:
            table.pop(old, None)
        _write(table)
    return removed
