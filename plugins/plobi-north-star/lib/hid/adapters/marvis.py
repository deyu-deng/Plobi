"""Marvis GUI surface — P0 free-tier consumer (HID only)."""

from __future__ import annotations

from typing import Any, Dict, List

from .base import SurfaceAdapter


class MarvisAdapter(SurfaceAdapter):
    name = "marvis"

    def plan(self, prompt: str, actions: List[Dict[str, Any]] | None = None) -> dict:
        if actions:
            return {"surface": self.name, "actions": actions, "source": "explicit"}
        # Default script: focus Marvis, paste prompt, submit.
        # Coordinates/hotkeys are placeholders — calibrate per machine via meta.
        return {
            "surface": self.name,
            "source": "default_script",
            "actions": [
                {"type": "hotkey", "payload": {"keys": ["alt", "tab"]}},
                {"type": "wait", "payload": {"ms": 400}},
                {"type": "hotkey", "payload": {"keys": ["ctrl", "l"]}},
                {"type": "wait", "payload": {"ms": 200}},
                {"type": "type_text", "payload": {"text": prompt}},
                {"type": "wait", "payload": {"ms": 100}},
                {"type": "key", "payload": {"key": "enter"}},
            ],
            "notes": [
                "Calibrate focus hotkey for Marvis window on this host.",
                "On quota exhaustion, worker must return summary for Master — do not retry forever.",
            ],
        }
