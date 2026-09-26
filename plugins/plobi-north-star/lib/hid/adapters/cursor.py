"""Cursor IDE surface — exploratory HID adapter."""

from __future__ import annotations

from typing import Any, Dict, List

from .base import SurfaceAdapter


class CursorAdapter(SurfaceAdapter):
    name = "cursor"

    def plan(self, prompt: str, actions: List[Dict[str, Any]] | None = None) -> dict:
        if actions:
            return {"surface": self.name, "actions": actions, "source": "explicit"}
        return {
            "surface": self.name,
            "source": "default_script",
            "actions": [
                {"type": "hotkey", "payload": {"keys": ["ctrl", "i"]}},
                {"type": "wait", "payload": {"ms": 500}},
                {"type": "type_text", "payload": {"text": prompt}},
                {"type": "key", "payload": {"key": "enter"}},
            ],
            "notes": ["Exploratory — validate Composer/Chat focus per Cursor version."],
        }
