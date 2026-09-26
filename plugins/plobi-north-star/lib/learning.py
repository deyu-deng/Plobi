"""Passive learning — draft Skill.md candidates for human confirm."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional


@dataclass
class Observation:
    id: str
    title: str
    steps: List[str]
    count: int = 1
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class SkillDraft:
    id: str
    name: str
    description: str
    body: str
    status: str = "pending_human"  # pending_human | approved | rejected
    observation_id: str = ""


class PassiveLearner:
    """Accumulate repeated ops; emit Skill drafts that require human approval."""

    def __init__(self, path: Optional[Path] = None, threshold: int = 3):
        if path is None:
            try:
                from hermes_constants import get_hermes_home

                root = get_hermes_home() / "plobi"
            except Exception:
                root = Path.home() / ".hermes" / "plobi"
            path = root / "learning.json"
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.threshold = threshold
        if not self.path.exists():
            self._write({"observations": [], "drafts": []})

    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"observations": [], "drafts": []}

    def _write(self, data: dict) -> None:
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def observe(self, title: str, steps: List[str]) -> dict:
        data = self._read()
        obs_list = data.get("observations", [])
        key = title.strip().lower()
        found = None
        for raw in obs_list:
            if raw.get("title", "").strip().lower() == key:
                raw["count"] = int(raw.get("count", 1)) + 1
                raw["steps"] = steps or raw.get("steps") or []
                raw["updated_at"] = datetime.now(timezone.utc).isoformat()
                found = raw
                break
        if found is None:
            found = asdict(
                Observation(
                    id=f"obs_{uuid.uuid4().hex[:10]}",
                    title=title.strip(),
                    steps=list(steps),
                )
            )
            obs_list.append(found)
        data["observations"] = obs_list

        draft = None
        if int(found["count"]) >= self.threshold:
            draft = self._ensure_draft(data, found)
        self._write(data)
        return {"observation": found, "draft": draft}

    def _ensure_draft(self, data: dict, obs: dict) -> dict:
        for d in data.get("drafts", []):
            if d.get("observation_id") == obs["id"] and d.get("status") == "pending_human":
                return d
        name = "plobi-" + "".join(ch if ch.isalnum() else "-" for ch in obs["title"].lower())[:40].strip("-")
        steps_md = "\n".join(f"{i+1}. {s}" for i, s in enumerate(obs.get("steps") or []))
        body = f"# {obs['title']}\n\n## Steps\n\n{steps_md}\n"
        draft = asdict(
            SkillDraft(
                id=f"skd_{uuid.uuid4().hex[:10]}",
                name=name or "plobi-learned-skill",
                description=f"Auto-drafted from repeated ops: {obs['title']}",
                body=body,
                observation_id=obs["id"],
            )
        )
        data.setdefault("drafts", []).append(draft)
        return draft

    def list_drafts(self, status: str = "pending_human") -> List[dict]:
        return [d for d in self._read().get("drafts", []) if d.get("status") == status]

    def resolve_draft(self, draft_id: str, *, approve: bool) -> Optional[dict]:
        data = self._read()
        for d in data.get("drafts", []):
            if d.get("id") == draft_id:
                d["status"] = "approved" if approve else "rejected"
                self._write(data)
                return d
        return None
