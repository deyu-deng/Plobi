from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List


class SurfaceAdapter(ABC):
    name: str = "base"

    @abstractmethod
    def plan(self, prompt: str, actions: List[Dict[str, Any]] | None = None) -> dict:
        ...

    def summarize(self, prompt: str, run: dict) -> str:
        ok = run.get("ok")
        count = run.get("count", 0)
        mode = "unknown"
        results = run.get("results") or []
        if results:
            mode = results[0].get("mode", mode)
        return f"[{self.name}] ok={ok} actions={count} mode={mode} prompt={prompt[:160]}"
