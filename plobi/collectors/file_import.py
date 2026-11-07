"""用户选一个文件，进信息池（`WP-SOURCE-FIRST-RUN-IMPORT`，用户 11-05 截图那四条里的第三条）。

这不是信源：没有采集频率、没有开关、不进每一轮。它是一次**用户主动的导入**，所以落点与身份
都和采集那族不一样——`source="import"`，`external_id` 由「文件名 + 序号 + 内容摘要」哈希出来，
同一个文件再导一次是同一条（幂等），不会把池子灌成两份。

第一版收五种（纸 §2 写了为什么）：`ics/ical`（日历导出，一事件一条）、`csv`（表格导出，一行一条）、
`md/txt`（笔记，一文件一条）、`opml`（大纲导出，一顶层节点一条）。全是纯文本、零新依赖。
不收 xlsx／pdf／json：前者要新依赖（另存 csv 就能进）、pdf 的解析是另一条腿、json 的我们自己的
导出格式还不存在。**不支持的扩展名拒收并说清支持哪几种**，不静默吞。
"""

from __future__ import annotations

import csv
import hashlib
import io
import xml.etree.ElementTree as ElementTree
from datetime import datetime
from pathlib import Path
from typing import Any

from plobi.pool import PoolItem
from plobi.pool import land as pool_land

#: 第一版支持的扩展名（小写）。按钮的提示与拒收那句话都从这一份取，不另抄一份名单。
SUPPORTED_SUFFIXES = (".ics", ".ical", ".csv", ".md", ".txt", ".opml")

#: 单个文件的上限：导入是用户主动的一次动作，不该把几百 MB 的东西读进内存再逐行落库。
MAX_IMPORT_BYTES = 20 * 1024 * 1024

SOURCE_ID = "import"

_CSV_TITLE_KEYS = ("title", "标题", "name", "名称")
_CSV_BODY_KEYS = ("body", "正文", "content", "内容", "text", "描述")
_CSV_WHEN_KEYS = ("when", "时间", "date", "日期", "start", "开始")
_CSV_KIND_KEYS = ("kind", "类型", "type")


class UnsupportedFormat(ValueError):
    """扩展名不在第一版名单里。message 就是给用户看的那句（支持哪几种写在里面）。"""


class ImportTooLarge(ValueError):
    """超过单文件上限。说清上限是多少，不静默截断。"""


def _key(filename: str, index: int, title: str, body: str) -> str:
    digest = hashlib.sha256(f"{filename}\x00{index}\x00{title}\x00{body}".encode("utf-8")).hexdigest()
    return digest[:16]


def _now() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _item(filename: str, index: int, *, title: str, body: str = "", kind: str = "note",
          occurred_at: str = "", context: dict[str, Any] | None = None) -> PoolItem:
    return PoolItem(
        source=SOURCE_ID,
        external_id=_key(filename, index, title, body),
        kind=kind,
        title=title,
        body=body,
        occurred_at=occurred_at or _now(),
        context={"imported_from": filename, **(context or {})},
    )


def _from_ics(path: Path) -> list[PoolItem]:
    # 日历解析不另写一份：timetable 那族已经有一份实测过的（含缺 DTSTART / 重复 UID 的统计）。
    from plobi.collectors.timetable.parser import parse_ics_with_stats

    occurrences, _stats = parse_ics_with_stats(path)
    items: list[PoolItem] = []
    for index, occ in enumerate(occurrences):
        items.append(_item(
            path.name, index,
            title=str(getattr(occ, "title", "") or ""),
            body=str((getattr(occ, "evidence", {}) or {}).get("description", "") or ""),
            kind="event",
            occurred_at=str(getattr(occ, "start_at", "") or ""),
            context={"end_at": str(getattr(occ, "end_at", "") or ""),
                     "ics_uid": str(getattr(occ, "external_id", "") or "")},
        ))
    return items


def _csv_row_to_item(filename: str, index: int, row: dict[str, str]) -> PoolItem:
    lowered = {str(k or "").strip().lower(): str(v or "").strip() for k, v in row.items()}

    def pick(keys: tuple[str, ...]) -> str:
        for key in keys:
            if lowered.get(key):
                return lowered[key]
        return ""

    title = pick(_CSV_TITLE_KEYS)
    body = pick(_CSV_BODY_KEYS)
    if not title and not body:
        # 表头一个都不认：整行拼成正文，标题取第一格——总比丢了一行强。
        values = [v for v in row.values() if str(v or "").strip()]
        title = str(values[0]) if values else f"row {index + 1}"
        body = " / ".join(str(v) for v in values[1:])
    return _item(filename, index, title=title, body=body,
                 kind=pick(_CSV_KIND_KEYS) or "note", occurred_at=pick(_CSV_WHEN_KEYS))


def _from_csv(raw: bytes, filename: str) -> list[PoolItem]:
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    fields = [str(name or "").strip().lower() for name in (reader.fieldnames or [])]
    known = set(_CSV_TITLE_KEYS + _CSV_BODY_KEYS + _CSV_WHEN_KEYS + _CSV_KIND_KEYS)
    if any(field in known for field in fields):
        items = [_csv_row_to_item(filename, index, row) for index, row in enumerate(reader)]
        if items:
            return items
    # 表头一个都不认（或根本没有表头）：**每一行都是数据**，按位置读。
    # 从前这里只在 DictReader 一条不剩时才退回去，于是「甲,乙 / 丙,丁」这种无表头文件
    # 会把第一行当表头吃掉——丢一行比认不出表头更糟。
    rows = list(csv.reader(io.StringIO(text)))
    return [_csv_row_to_item(filename, index, {str(i): cell for i, cell in enumerate(row)})
            for index, row in enumerate(rows) if any(str(c or "").strip() for c in row)]


def _from_opml(raw: bytes, filename: str) -> list[PoolItem]:
    root = ElementTree.fromstring(raw.decode("utf-8", errors="replace"))
    body = root.find("body")
    if body is None:
        return []

    def flatten(node: ElementTree.Element, depth: int) -> list[str]:
        text = str(node.get("text") or node.get("title") or "").strip()
        lines = [f"{'  ' * depth}- {text}"] if text else []
        for child in node.findall("outline"):
            lines.extend(flatten(child, depth + 1))
        return lines

    items: list[PoolItem] = []
    for index, node in enumerate(body.findall("outline")):
        title = str(node.get("text") or node.get("title") or "").strip() or f"outline {index + 1}"
        items.append(_item(filename, index, title=title, body="\n".join(flatten(node, 0))))
    return items


def parse_import(path: str | Path) -> list[PoolItem]:
    """把一个文件解析成池子里的行。**只解析不落库**——预览与导入共用这一只手。"""
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise UnsupportedFormat(
            f"{file_path.name}: 不支持 {suffix or '（无扩展名）'}；第一版支持 "
            + "、".join(SUPPORTED_SUFFIXES))
    raw = file_path.read_bytes()
    if len(raw) > MAX_IMPORT_BYTES:
        raise ImportTooLarge(f"{file_path.name}: {len(raw)} 字节，超过单文件上限 "
                             f"{MAX_IMPORT_BYTES // (1024 * 1024)} MB")

    if suffix in (".ics", ".ical"):
        return _from_ics(file_path)
    if suffix == ".csv":
        return _from_csv(raw, file_path.name)
    if suffix == ".opml":
        return _from_opml(raw, file_path.name)
    text = raw.decode("utf-8-sig", errors="replace")
    return [_item(file_path.name, 0, title=file_path.stem, body=text)]


def import_file(path: str | Path) -> dict[str, Any]:
    """解析并落池。返回 `{file, format, parsed, created, updated, unchanged}`。"""
    file_path = Path(path)
    items = parse_import(file_path)
    result = pool_land(items)
    return {"file": file_path.name, "format": file_path.suffix.lower(),
            "parsed": len(items), **result}
