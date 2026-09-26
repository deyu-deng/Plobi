"""Pico 2W HID bridge.

Real firmware talks USB HID (and optionally WiFi command channel).
This module provides:
- a stable host API
- mock transport for CI / dry-run
- optional serial JSON line protocol when PLOBI_PICO_SERIAL is set
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class HidAction:
    type: str  # key | type_text | move | click | hotkey | wait
    payload: Dict[str, Any]


class PicoBridge:
    def __init__(self, device_id: str, *, kind: str = "pico2w", endpoint: str = ""):
        self.device_id = device_id
        self.kind = kind
        self.endpoint = endpoint or os.environ.get("PLOBI_PICO_SERIAL", "")
        self._history: List[HidAction] = []

    def ping(self) -> dict:
        if self.kind == "mock" or os.environ.get("PLOBI_HID_MOCK", "").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }:
            return {"ok": True, "device_id": self.device_id, "mode": "mock"}
        if self.endpoint:
            try:
                return self._serial_rpc({"op": "ping"})
            except Exception as exc:  # pragma: no cover - hardware dependent
                return {"ok": False, "error": str(exc), "device_id": self.device_id}
        # No serial configured: report ready-but-dry (host API wired)
        return {
            "ok": True,
            "device_id": self.device_id,
            "mode": "dry_run",
            "message": "Set PLOBI_PICO_SERIAL or PLOBI_HID_MOCK=1",
        }

    def run_actions(self, actions: List[Dict[str, Any]]) -> dict:
        parsed = [HidAction(type=a.get("type", "wait"), payload=a.get("payload") or a) for a in actions]
        results = []
        for action in parsed:
            self._history.append(action)
            results.append(self._exec(action))
        return {
            "ok": all(r.get("ok", False) for r in results),
            "device_id": self.device_id,
            "results": results,
            "count": len(results),
        }

    def _exec(self, action: HidAction) -> dict:
        if self.kind == "mock" or os.environ.get("PLOBI_HID_MOCK", "").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }:
            return {"ok": True, "mode": "mock", "action": action.type, "payload": action.payload}
        if self.endpoint:
            try:
                return self._serial_rpc({"op": "action", "type": action.type, "payload": action.payload})
            except Exception as exc:  # pragma: no cover
                return {"ok": False, "error": str(exc), "action": action.type}
        return {
            "ok": True,
            "mode": "dry_run",
            "action": action.type,
            "payload": action.payload,
        }

    def _serial_rpc(self, message: dict) -> dict:
        """Newline-delimited JSON over serial. Requires pyserial if endpoint set."""
        try:
            import serial  # type: ignore
        except ImportError as exc:
            raise RuntimeError("pyserial required for PLOBI_PICO_SERIAL") from exc
        baud = int(os.environ.get("PLOBI_PICO_BAUD", "115200"))
        with serial.Serial(self.endpoint, baudrate=baud, timeout=2.0) as ser:
            ser.write((json.dumps(message) + "\n").encode("utf-8"))
            line = ser.readline().decode("utf-8", errors="replace").strip()
            if not line:
                return {"ok": False, "error": "empty serial response"}
            return json.loads(line)
