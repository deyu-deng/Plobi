"""Preview bus — priority: artifact > progress > resource."""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from pathlib import Path
from typing import List, Optional


class PreviewPriority(IntEnum):
    ARTIFACT = 0  # 产出预览
    PROGRESS = 1  # 进度
    RESOURCE = 2  # 资源


@dataclass
class PreviewItem:
    id: str
    title: str
    priority: int
    kind: str = "file"  # file | url | text
    url: str = ""
    path: str = ""
    text: str = ""
    auto_open: bool = True
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    meta: dict = field(default_factory=dict)


class PreviewBus:
    def __init__(self, path: Optional[Path] = None):
        if path is None:
            try:
                from plobi_constants import get_plobi_home

                root = get_plobi_home() / "plobi"
            except Exception:
                root = Path.home() / ".plobi" / "plobi"
            path = root / "preview_bus.json"
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        if not self.path.exists():
            self._write({"items": []})

    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"items": []}

    def _write(self, data: dict) -> None:
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def push(
        self,
        title: str,
        *,
        priority: str | int = "artifact",
        kind: str = "file",
        url: str = "",
        path: str = "",
        text: str = "",
        auto_open: bool = True,
        meta: Optional[dict] = None,
    ) -> PreviewItem:
        pri = self._parse_priority(priority)
        item = PreviewItem(
            id=f"prv_{uuid.uuid4().hex[:10]}",
            title=title,
            priority=int(pri),
            kind=kind,
            url=url,
            path=path,
            text=text,
            auto_open=auto_open,
            meta=meta or {},
        )
        with self._lock:
            data = self._read()
            items = data.get("items", [])
            items.append(asdict(item))
            # Keep last 200
            data["items"] = items[-200:]
            self._write(data)
        return item

    def list_items(self, limit: int = 50) -> List[PreviewItem]:
        with self._lock:
            items = [PreviewItem(**i) for i in self._read().get("items", [])]
        items.sort(key=lambda i: (i.priority, i.created_at))
        return items[:limit]

    def latest_for_auto_open(self) -> Optional[PreviewItem]:
        autos = [i for i in self.list_items(100) if i.auto_open]
        return autos[0] if autos else None

    @staticmethod
    def _parse_priority(priority: str | int) -> PreviewPriority:
        if isinstance(priority, int):
            return PreviewPriority(priority)
        key = str(priority).strip().lower()
        mapping = {
            "artifact": PreviewPriority.ARTIFACT,
            "产出": PreviewPriority.ARTIFACT,
            "产出预览": PreviewPriority.ARTIFACT,
            "progress": PreviewPriority.PROGRESS,
            "进度": PreviewPriority.PROGRESS,
            "resource": PreviewPriority.RESOURCE,
            "资源": PreviewPriority.RESOURCE,
        }
        if key not in mapping:
            raise ValueError(f"Unknown preview priority: {priority!r}")
        return mapping[key]
