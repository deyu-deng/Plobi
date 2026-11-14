"""日记那一族（``diary`` 门类）的**注册表工具**。

从 provider 搬过来（``WP-HUMAN-REGISTRY-TOOLS``，11-14）：**provider 只管脑页**，
人读面（笔记／日记／档案）那 17 把改成注册表工具 ⇒ 门类 note／diary／dossier 在
``toolsets.py`` 里那一层**一个字没动**，Tools 页那一格照样管用。

这一族四把：``diary_read``／``diary_write``（基本两把）＋ ``diary_list``／
``diary_append``（细动作，11-14 补）。

**schema 从 provider 逐字搬过来**——描述文案是给模型看的契约，一个字不动。
**闸也不松**：日记是**用户自己的字** ⇒ 写仍走 ``MindWriter``（信任闸：闸关着就进待批）。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict

from tools.human_common import (
    DIARY_PREFIX,
    FACE_HINTS,
    json_result,
    read_face,
    write_face,
)

logger = logging.getLogger(__name__)

DIARY_READ_TOOL = "diary_read"
DIARY_WRITE_TOOL = "diary_write"
DIARY_LIST_TOOL = "diary_list"
DIARY_APPEND_TOOL = "diary_append"

DIARY_READ_SCHEMA = {
    "name": DIARY_READ_TOOL,
    "description": (
        "Read one diary entry on the human side by its LOGICAL path, "
        "``Diary/...`` — e.g. ``Diary/2026-11-10-Tuesday.md``. The diary is "
        "the user's own writing and nothing is pushed into your context when "
        "they write it, so read it when it actually matters: the evening "
        "check-in, or a question about how their day went. Only paths under "
        "``Diary/`` resolve."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Logical path of the diary entry, e.g. Diary/2026-11-10-Tuesday.md",
            }
        },
        "required": ["path"],
    },
    }

DIARY_WRITE_SCHEMA = {
    "name": DIARY_WRITE_TOOL,
    "description": (
        "Write one diary entry on the human side (``Diary/...``) by its "
        "LOGICAL path. The diary is the user's own writing: ask before you "
        "write here and say what you are about to add — one-line jots at the "
        "end of today's page (`- HH:MM …`) are their habit, not a licence to "
        "rewrite the day. The write is checked by the trust gate; the result "
        "says whether it landed, went to the pending queue (with an id), or "
        "was refused, and why."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Logical path of the diary entry, e.g. Diary/2026-11-10-Tuesday.md",
            },
            "content": {
                "type": "string",
                "description": "The full new contents of that entry (markdown).",
            },
        },
        "required": ["path", "content"],
    },
    }

DIARY_LIST_SCHEMA = {
    "name": DIARY_LIST_TOOL,
    "description": (
        "List the diary entries on the human side — which days exist, how "
        "long each one is and when it was last touched. You get dates and "
        "sizes, never the entries themselves; read one with "
        "``diary_read``."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
    }

DIARY_APPEND_SCHEMA = {
    "name": DIARY_APPEND_TOOL,
    "description": (
        "Add one line to a diary entry — the same quick capture the "
        "screen does: a ``- HH:MM …`` line at the end of that day. The "
        "diary is the user's own writing, so ask before you write here "
        "and say what you are about to add. The write is checked by the "
        "trust gate; the result says whether it landed, went to the "
        "pending queue (with an id), or was refused, and why."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "line": {
                "type": "string",
                "description": "The one line to add (no line breaks).",
            },
            "day": {
                "type": "string",
                "description": (
                    "Which day — ``YYYY-MM-DD``. Leave it out for today."
                ),
            },
        },
        "required": ["line"],
    },
    }


# ── 四把手 ──────────────────────────────────────────────────────────────────


def diary_read(args: Dict[str, Any], **_kw: Any) -> str:
    """按逻辑路径读一天的日记（``Diary/2026-11-10-Tuesday.md``）。"""
    return json_result(read_face(args, (DIARY_PREFIX,), FACE_HINTS[DIARY_PREFIX]))


def diary_write(args: Dict[str, Any], **_kw: Any) -> str:
    """整篇覆写某一天的日记（走信任闸：闸关着就进待批，人点头才落）。"""
    return json_result(write_face(args, (DIARY_PREFIX,), FACE_HINTS[DIARY_PREFIX]))


def diary_list(args: Dict[str, Any], **_kw: Any) -> str:
    """列日记有哪几天（屏上那一栏的同一份真源）。

    原来只能按路径读一篇 ⇒ 读之前得先知道路径；日记的路径是日期，
    Agent 根本猜不出来 ⇒ 这一把把「哪几天有」给它。
    """
    from plobi.human import diary as diary_module
    from plobi.mind.paths import MindUnavailable

    try:
        data = diary_module.list_entries()
    except MindUnavailable as exc:
        return json_result({"success": False, "error": str(exc)})
    except Exception as exc:  # noqa: BLE001 - 列不出来要说清，不许炸这一轮
        logger.warning("[human] diary_list failed: %s", exc)
        return json_result({"success": False, "error": f"列日记失败：{exc}"})
    entries = list(data.get("entries") or [])
    return json_result({
        "success": True,
        "today": data.get("today", ""),
        "entries": entries,
        "totals": {"days": len(entries)},
    })


def diary_append(args: Dict[str, Any], **_kw: Any) -> str:
    """往某天末尾接一行 ``- HH:MM …``（屏上「路过记一句」那一条路）。

    日记是**用户自己的字** ⇒ 这一把仍走 ``diary_write`` 那条路（信任闸：
    闸关着就进待批，人点头才落）。
    """
    from plobi.human import diary as diary_module
    from plobi.mind.paths import MindUnavailable

    payload = args or {}
    line = str(payload.get("line") or "").strip()
    if not line:
        return json_result({"success": False, "error": "line 是必需的：要记的那一句"})
    if "\n" in line or "\r" in line:
        return json_result({"success": False, "error": "一次只记一行"})
    raw_day = str(payload.get("day") or "").strip()
    if not raw_day or raw_day.casefold() == "today":
        raw_day = datetime.now().strftime("%Y-%m-%d")
    try:
        entry = diary_module.read_entry(raw_day)
    except ValueError as exc:
        return json_result({"success": False, "error": str(exc)})
    except MindUnavailable as exc:
        return json_result({"success": False, "error": str(exc)})

    addition = f"- {datetime.now().strftime('%H:%M')} {line}"
    body = entry.get("text") or ""
    content = (
        body.rstrip("\n") + "\n" + addition + "\n"
        if body.strip()
        else addition + "\n"
    )
    logical = f"{DIARY_PREFIX}/{entry.get('filename', '')}"
    result = write_face(
        {"path": logical, "content": content},
        (DIARY_PREFIX,),
        FACE_HINTS[DIARY_PREFIX],
    )
    if isinstance(result, dict) and result.get("success"):
        result["line"] = addition
        result["day"] = entry.get("date", raw_day)
    return json_result(result)


# ── 注册 ────────────────────────────────────────────────────────────────────

from tools.registry import registry

registry.register(
    name=DIARY_READ_TOOL,
    toolset="diary",
    schema=DIARY_READ_SCHEMA,
    handler=diary_read,
    description="Read one diary entry by its logical path.",
    emoji="📔",
)
registry.register(
    name=DIARY_WRITE_TOOL,
    toolset="diary",
    schema=DIARY_WRITE_SCHEMA,
    handler=diary_write,
    description="Overwrite one diary entry (trust gate).",
    emoji="📔",
)
registry.register(
    name=DIARY_LIST_TOOL,
    toolset="diary",
    schema=DIARY_LIST_SCHEMA,
    handler=diary_list,
    description="List which days have a diary entry.",
    emoji="📔",
)
registry.register(
    name=DIARY_APPEND_TOOL,
    toolset="diary",
    schema=DIARY_APPEND_SCHEMA,
    handler=diary_append,
    description="Append one line to a day's diary (trust gate).",
    emoji="📔",
)
