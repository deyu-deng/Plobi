"""Browser free-tier surfaces — exploratory HID adapter."""

from __future__ import annotations

from typing import Any, Dict, List

from .base import SurfaceAdapter


class BrowserAdapter(SurfaceAdapter):
    name = "browser"

    def plan(self, prompt: str, actions: List[Dict[str, Any]] | None = None) -> dict:
        if actions:
            return {"surface": self.name, "actions": actions, "source": "explicit"}
        url = "https://chatgpt.com/"
        return {
            "surface": self.name,
            "source": "default_script",
            "actions": [
                {"type": "hotkey", "payload": {"keys": ["ctrl", "l"]}},
                {"type": "type_text", "payload": {"text": url}},
                {"type": "key", "payload": {"key": "enter"}},
                {"type": "wait", "payload": {"ms": 1500}},
                {"type": "type_text", "payload": {"text": prompt}},
                {"type": "key", "payload": {"key": "enter"}},
            ],
            "notes": ["Exploratory stub — target site must be configured per deployment."],
        }
