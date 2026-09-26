"""Open domain registry — money + output slots, no product-level bans."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class DomainSlot:
    id: str
    kind: str  # output | money | butler
    label: str
    stages: List[str] = field(default_factory=lambda: ["intake", "sketch", "rough", "refine", "render", "done"])
    default_risk: str = "L1_local_mutate"
    enabled: bool = True
    meta: Dict = field(default_factory=dict)


DEFAULT_DOMAINS = [
    DomainSlot("code", "output", "代码"),
    DomainSlot("docs", "output", "文档"),
    DomainSlot("modeling", "output", "建模"),
    DomainSlot("painting", "output", "绘画"),
    DomainSlot("browser", "output", "浏览器自动化", stages=["intake", "sketch", "rough", "render", "done"]),
    DomainSlot("outsourcing", "money", "外包接单", default_risk="L3_money_or_irreversible"),
    DomainSlot("self_media", "money", "自媒体", default_risk="L2_external_msg"),
    DomainSlot("quant", "money", "量化套利", default_risk="L3_money_or_irreversible"),
    DomainSlot("butler_email", "butler", "邮箱", default_risk="L2_external_msg"),
    DomainSlot("butler_calendar", "butler", "日历冲突", default_risk="L1_local_mutate"),
    DomainSlot("butler_parcel", "butler", "快递物流", default_risk="L0_observe"),
    DomainSlot("butler_disk", "butler", "磁盘卫生", default_risk="L1_local_mutate"),
]


class DomainRegistry:
    def __init__(self, path: Optional[Path] = None):
        if path is None:
            try:
                from plobi_constants import get_plobi_home

                root = get_plobi_home() / "plobi"
            except Exception:
                root = Path.home() / ".plobi" / "plobi"
            path = root / "domains.json"
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write({"domains": [asdict(d) for d in DEFAULT_DOMAINS]})

    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"domains": []}

    def _write(self, data: dict) -> None:
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def list_domains(self, kind: Optional[str] = None) -> List[DomainSlot]:
        domains = [DomainSlot(**d) for d in self._read().get("domains", [])]
        if kind:
            domains = [d for d in domains if d.kind == kind]
        return domains

    def register(self, slot: DomainSlot) -> DomainSlot:
        data = self._read()
        domains = data.get("domains", [])
        for i, raw in enumerate(domains):
            if raw.get("id") == slot.id:
                domains[i] = asdict(slot)
                data["domains"] = domains
                self._write(data)
                return slot
        domains.append(asdict(slot))
        data["domains"] = domains
        self._write(data)
        return slot

    def get(self, domain_id: str) -> Optional[DomainSlot]:
        for d in self.list_domains():
            if d.id == domain_id:
                return d
        return None
