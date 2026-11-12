"""Stage gates for large creative / delivery pipelines."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, List, Optional


class Stage(str, Enum):
    INTAKE = "intake"
    SKETCH = "sketch"
    ROUGH = "rough"
    REFINE = "refine"
    RENDER = "render"
    DONE = "done"

    @classmethod
    def parse(cls, value: str | None) -> "Stage":
        if value is None:
            return cls.INTAKE
        raw = str(value).strip().lower()
        for member in cls:
            if raw == member.value:
                return member
        raise ValueError(f"Unknown stage: {value!r}")


# Default gates that pause for human review
DEFAULT_GATED: frozenset[Stage] = frozenset(
    {Stage.SKETCH, Stage.ROUGH, Stage.REFINE, Stage.RENDER}
)

DOMAIN_PIPELINES: dict[str, List[Stage]] = {
    "code": [Stage.INTAKE, Stage.SKETCH, Stage.ROUGH, Stage.REFINE, Stage.RENDER, Stage.DONE],
    "docs": [Stage.INTAKE, Stage.SKETCH, Stage.ROUGH, Stage.REFINE, Stage.RENDER, Stage.DONE],
    "modeling": [Stage.INTAKE, Stage.SKETCH, Stage.ROUGH, Stage.REFINE, Stage.RENDER, Stage.DONE],
    "painting": [Stage.INTAKE, Stage.SKETCH, Stage.ROUGH, Stage.REFINE, Stage.RENDER, Stage.DONE],
    "browser": [Stage.INTAKE, Stage.SKETCH, Stage.ROUGH, Stage.RENDER, Stage.DONE],
    "generic": [Stage.INTAKE, Stage.SKETCH, Stage.ROUGH, Stage.REFINE, Stage.RENDER, Stage.DONE],
}


@dataclass
class StageMachine:
    domain: str = "generic"
    stage: Stage = Stage.INTAKE
    gated: frozenset[Stage] = field(default_factory=lambda: DEFAULT_GATED)
    awaiting_human: bool = False

    def pipeline(self) -> List[Stage]:
        return list(DOMAIN_PIPELINES.get(self.domain, DOMAIN_PIPELINES["generic"]))

    def is_gated(self, stage: Optional[Stage] = None) -> bool:
        return (stage or self.stage) in self.gated

    def request_advance(self) -> dict:
        """Try to leave current stage. Gated stages pause for human."""
        if self.stage == Stage.DONE:
            return {"ok": True, "stage": self.stage.value, "awaiting_human": False}
        if self.is_gated() and not self.awaiting_human:
            self.awaiting_human = True
            return {
                "ok": False,
                "paused": True,
                "stage": self.stage.value,
                "awaiting_human": True,
                "message": f"Stage gate: approve '{self.stage.value}' to continue",
            }
        return self._advance()

    def approve(self) -> dict:
        if not self.awaiting_human:
            return {"ok": False, "error": "not awaiting human"}
        self.awaiting_human = False
        return self._advance()

    def reject(self, reason: str = "") -> dict:
        self.awaiting_human = True
        return {
            "ok": True,
            "rejected": True,
            "stage": self.stage.value,
            "reason": reason,
            "awaiting_human": True,
        }

    def _advance(self) -> dict:
        pipe = self.pipeline()
        try:
            idx = pipe.index(self.stage)
        except ValueError:
            self.stage = Stage.DONE
            return {"ok": True, "stage": self.stage.value}
        if idx + 1 >= len(pipe):
            self.stage = Stage.DONE
        else:
            self.stage = pipe[idx + 1]
        self.awaiting_human = False
        return {"ok": True, "stage": self.stage.value, "awaiting_human": False}

    def to_dict(self) -> dict:
        return {
            "domain": self.domain,
            "stage": self.stage.value,
            "awaiting_human": self.awaiting_human,
            "pipeline": [s.value for s in self.pipeline()],
            "gated": [s.value for s in sorted(self.gated, key=lambda s: s.value)],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "StageMachine":
        gated_raw: Iterable[str] = data.get("gated") or [s.value for s in DEFAULT_GATED]
        return cls(
            domain=str(data.get("domain") or "generic"),
            stage=Stage.parse(data.get("stage")),
            gated=frozenset(Stage.parse(s) for s in gated_raw),
            awaiting_human=bool(data.get("awaiting_human")),
        )
