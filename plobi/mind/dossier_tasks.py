"""档案里的每日任务清单（``tasks.json``）——人和 Agent 共用一处真源。

清单的真源是**档案目录下的 ``tasks.json``**，与 ``plan.md``／``progress.md`` 同目录：
人在屏上勾选 ⇒ 本模块改写这个文件；Agent 用 ``surface_write`` 原子工具改**同一个**文件。
两边共用一处真源，所以这一层只认那一个文件，别处不留副本。

三件事钉住：

* **坏文件不许糊住读手**：文件不在 ⇒「没有清单」；文件在但 JSON 坏了 ⇒「读不到」。
  这两件事必须分开——空表会被读成「一个都没有」，而那是**假的**（这一层最贵的一笔）。
* **只改那一条**：写回时原样保留文件里我们不认识的键（顶上的、那天上的、那条上的），
  不把别人的字段洗掉。
* **落盘要原子**：临时文件 ＋ ``os.replace``，写到一半崩了不会留下半份清单。

档案在哪儿由 :mod:`plobi.mind.agent_dossier` 那一套回答（名册 → ``mind_subtree`` →
:func:`plobi.mind.paths.real_target`），这里**不另立**一套「档案在哪」的逻辑。
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .agent_dossier import _find, _home_for, load_entries

logger = logging.getLogger(__name__)

TASKS_FILENAME = "tasks.json"
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# 谁标记的。Agent 那边的值由它自己写文件时落，这里只管人勾的。
DONE_BY_USER = "user"
DONE_BY_AGENT = "agent"


class TasksError(RuntimeError):
    """清单这一层的失败。"""


class AgentNotRegistered(TasksError):
    """名册里没有这一行。"""


class DossierRootUnavailable(TasksError):
    """这一行绑了档案目录，但人读面／脑的根读不到。"""


class TasksFileMissing(TasksError):
    """档案目录下没有 ``tasks.json``。"""


class TasksFileBroken(TasksError):
    """``tasks.json`` 在，但读不出／解不开。"""


class TaskNotFound(TasksError):
    """文件在，但那天没有那一条。"""


@dataclass
class TaskItem:
    """清单上一条。"""

    id: str
    title: str
    done: bool
    doneBy: str
    updatedAt: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "done": self.done,
            "doneBy": self.doneBy,
            "updatedAt": self.updatedAt,
        }


@dataclass
class TaskDay:
    """一天。"""

    date: str
    items: list[TaskItem]

    def to_dict(self) -> dict[str, Any]:
        return {"date": self.date, "items": [item.to_dict() for item in self.items]}


@dataclass
class TaskFile:
    """读回来的整份清单。``error`` 有值 = 读不到（不是「一个都没有」）。"""

    exists: bool
    error: str | None
    days: list[TaskDay]

    def to_dict(self) -> dict[str, Any]:
        return {
            "exists": self.exists,
            "error": self.error,
            "days": [day.to_dict() for day in self.days],
        }


def tasks_path(home: Path) -> Path:
    """档案目录下的清单文件。"""
    return home / TASKS_FILENAME


def find_home(name: str, roster_path: Path | None = None) -> Path | None:
    """名册那一行落到的档案目录。

    * 名册里没这一行 ⇒ :class:`AgentNotRegistered`（该 404 就 404，不瞎猜一个）。
    * 这一行没绑 ``mind_subtree`` ⇒ ``None``：它压根没有清单可谈。
    * 绑了但根读不到 ⇒ :class:`DossierRootUnavailable`——**不是**「没有清单」，
      空表会被读成「一个都没有」，那是假的。
    """
    _, entries = load_entries(roster_path)
    entry = _find(entries, name)
    if entry is None:
        raise AgentNotRegistered(f"agent {name!r} is not registered")
    if not (getattr(entry, "mind_subtree", "") or "").strip():
        return None
    home = _home_for(entry)
    if home is None:
        raise DossierRootUnavailable(f"the dossier root of {name!r} is not available")
    return home


def read_tasks(path: Path) -> TaskFile:
    """读一份清单。坏了不抛异常糊住读手——返回「读不到」这一格。"""
    if not path.is_file():
        return TaskFile(exists=False, error=None, days=[])
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("dossier tasks: %s is unreadable: %s", path, exc)
        return TaskFile(exists=True, error=f"{path.name} is not valid JSON: {exc}", days=[])
    if not isinstance(raw, dict):
        return TaskFile(exists=True, error=f"{path.name} must be a JSON object", days=[])
    return TaskFile(exists=True, error=None, days=_parse_days(raw.get("days")))


def toggle_task(path: Path, date: str, item_id: str, done: bool, *, actor: str = DONE_BY_USER) -> TaskFile:
    """把某一条改成目标状态（不是翻转）；只动那一条，别的键原样留着。

    ``doneBy`` 记的是**最后一次动它的人**：Agent 那边自己写文件时落 ``agent``，
    人勾的落 ``user``。``updatedAt`` 每次都写，前端才知道这一条是什么时候变的。
    """
    if not path.is_file():
        raise TasksFileMissing(f"{path.name} does not exist")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TasksFileBroken(f"{path.name} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise TasksFileBroken(f"{path.name} must be a JSON object")

    target = _find_item(raw, date, item_id)
    if target is None:
        raise TaskNotFound(f"no task {item_id!r} on {date}")

    target["done"] = bool(done)
    target["doneBy"] = actor
    target["updatedAt"] = datetime.now().isoformat(timespec="seconds")
    _write_atomic(path, raw)
    return read_tasks(path)


def _parse_days(raw_days: Any) -> list[TaskDay]:
    """把 ``days`` 收成干净的一份。缺字段的**跳过那一条**，不炸整份。"""
    if not isinstance(raw_days, list):
        return []
    days: list[TaskDay] = []
    for entry in raw_days:
        if not isinstance(entry, dict):
            continue
        date = str(entry.get("date") or "").strip()
        if not DATE_PATTERN.match(date):
            continue
        raw_items = entry.get("items")
        if not isinstance(raw_items, list):
            continue
        items = [item for item in (_parse_item(row) for row in raw_items) if item is not None]
        days.append(TaskDay(date=date, items=items))
    return days


def _parse_item(raw: Any) -> TaskItem | None:
    """一条任务。``id`` 与 ``title`` 缺一即没法用（跳过）；其余的补默认。"""
    if not isinstance(raw, dict):
        return None
    item_id = str(raw.get("id") or "").strip()
    title = raw.get("title")
    if not item_id or not isinstance(title, str):
        return None
    return TaskItem(
        id=item_id,
        title=title,
        done=_as_bool(raw.get("done")),
        doneBy=str(raw.get("doneBy") or ""),
        updatedAt=str(raw.get("updatedAt") or ""),
    )


def _as_bool(value: Any) -> bool:
    """``done`` 归一成 bool——文件里写了 ``"true"`` 也算完成。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "done"}
    if isinstance(value, (int, float)):
        return bool(value)
    return False


def _find_item(raw: dict[str, Any], date: str, item_id: str) -> dict[str, Any] | None:
    """在原文件结构里找到那一条（返回的是**原字典**，改它就不动别的键）。"""
    raw_days = raw.get("days")
    if not isinstance(raw_days, list):
        return None
    for day in raw_days:
        if not isinstance(day, dict) or str(day.get("date") or "").strip() != date:
            continue
        raw_items = day.get("items")
        if not isinstance(raw_items, list):
            continue
        for item in raw_items:
            if isinstance(item, dict) and str(item.get("id") or "").strip() == item_id:
                return item
    return None


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    """临时文件 ＋ ``os.replace``：读者要么看到旧的，要么看到新的，看不到半份。"""
    body = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    handle, tmp = tempfile.mkstemp(prefix=".tasks-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(body)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
