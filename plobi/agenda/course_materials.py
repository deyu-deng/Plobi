"""一门课自己目录里的教材清单（`WP-COURSE-DATA-MOUTH` 第二手的数据那半）。

判据要求的是「在那门课的会话里问到题，答案里出现题号 + 书里的位置」，而**不许再去 `agenda.db` 捐标题**。
这一档只回答前一半里更基础的一件事：**这门课的资料到底在不在它自己 `project_path` 指的地方、
在的话是哪几份、不在或读不到就直说**。它不猜、不把"没绑目录"的门课当成 butler、
也不把"云占位读不到"说成"这门课没有资料"（裁定 150② 同一条形状：不许拿"还没接上"解释看不见）。

归属那颗课名一律走真源 `planning.course_key` / `course_alias`——同一门课有两种拼法
（`大学物理（甲）Ⅱ` vs `...II`）与学期前缀（`2026-2027秋冬-电工电子学`），在这里再各写一套
就是第二真源（`WP-COURSE-MATCH-ONE-SOURCE` 那一刀刚为这个付过账）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .planning import course_alias, course_key

# 三种可观察状态：读得到 / 云端占位（列得出、内容读不出）/ 目录不存在或没权限。
READABLE = "local"
PLACEHOLDER = "cloud-placeholder"
UNREADABLE = "unreadable"

_PROBE_BYTES = 32
_MAX_FILES = 40


@dataclass(frozen=True)
class MaterialFile:
    name: str
    size: int
    state: str
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "size": self.size, "state": self.state, "detail": self.detail}


def _probe(path: Path) -> MaterialFile:
    """只探不读：读前 32 字节够判断"这份是不是真在本地盘上"，不整本进内存。"""
    try:
        size = path.stat().st_size
    except OSError as exc:
        return MaterialFile(path.name, 0, UNREADABLE, f"问不到大小：{exc}")

    try:
        with open(path, "rb") as handle:
            handle.read(_PROBE_BYTES)
    except OSError as exc:
        # Windows 云占位（重解析点 + RECALL_ON_DATA_ACCESS）在提供方没运行时是这个形状：
        # 目录能列、大小有名、内容一读就 Errno 22。说成"读不到"，不说成"没有"。
        return MaterialFile(path.name, size, PLACEHOLDER, f"读不出内容：{exc}")

    return MaterialFile(path.name, size, READABLE)


def _resolve_entry(registry: Any, course: str):
    """按真源那颗课名找名册条目：全等 → 别名全等，**不做子串**（子串就是串台）。"""
    needle = course_key(course)
    alias = course_key(course_alias(course))
    entries = list(registry.live_entries())

    for entry in entries:
        if course_key(entry.display_name or entry.name) == needle:
            return entry
    for entry in entries:
        if course_key(entry.display_name or entry.name) == alias:
            return entry
    return None


def materials_for_course(course: str, *, registry: Any = None,
                         home: Optional[Path] = None) -> dict[str, Any]:
    """一门课的教材清单。`registry` / `home` 只为测试可注入，生产走名册真源。"""
    name = (course or "").strip()
    if not name:
        return {"ok": False, "course": "", "files": [],
                "detail": "没给课程名：这一问没有对象，不去猜是哪一门"}

    if registry is None:
        from plobi.agents.registry import load_registry

        try:
            registry = load_registry()
        except Exception as exc:  # noqa: BLE001 - 名册读不到就是说读不到，不编一份空清单
            return {"ok": False, "course": name, "files": [],
                    "detail": f"读不动名册：{exc}"}

    entry = _resolve_entry(registry, name)
    if entry is None:
        return {"ok": False, "course": name, "files": [],
                "detail": f"名册里没有这一门「{name}」：这是查过的结果，不是它没有资料"}

    paths = [str(p) for p in (getattr(entry, "project_paths", ()) or ())] or \
            ([str(entry.project_path)] if getattr(entry, "project_path", "") else [])
    if not paths:
        # 裁定 153② 那句：没绑定路径就是没绑定，给人话，不许静默当成家务分身去别处找。
        return {"ok": False, "course": entry.name, "files": [],
                "detail": "这一门没写 `project_path`：资料不在本机任何地方，我不去猜目录，"
                          "也不把它当成没有绑定属性的家务分身——要它讲题，先把文件夹绑上"}

    found: list[MaterialFile] = []
    notes: list[str] = []
    for raw in paths:
        root = Path(raw).expanduser()
        if home is not None:
            root = home / root.name
        if not root.is_dir():
            notes.append(f"{raw}：这个目录现在不在")
            continue
        children = sorted((p for p in root.rglob("*") if p.is_file()),
                          key=lambda p: p.name)[:_MAX_FILES]
        if not children:
            notes.append(f"{raw}：目录在，里面一个文件都没有")
            continue
        found.extend(_probe(p) for p in children)

    states = {f.state for f in found}
    if not found:
        return {"ok": False, "course": entry.name, "files": [],
                "detail": "；".join(notes) or "一处都没读到文件"}

    if states == {READABLE}:
        how = "都在本地盘上，读得到"
    elif READABLE in states:
        how = "有一部分读得到，其余读不到（逐份带 state）"
    elif states == {PLACEHOLDER}:
        how = ("全都在云端占位（网盘没同步/提供方没运行）：**目录列得出、文件名是真名，内容一份都读不出** ⇒ "
               "要讲到题，先把这份拉到本地")
    else:
        how = "一份都读不出（云端占位或权限），逐份带原因"

    return {"ok": True, "course": entry.name, "root": paths[0],
            "files": [f.as_dict() for f in found],
            "counts": {state: sum(1 for f in found if f.state == state)
                       for state in (READABLE, PLACEHOLDER, UNREADABLE)},
            "note": "；".join(notes), "detail": how}
