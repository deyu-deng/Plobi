"""Device registry + exclusive screen lock for HID workers."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class DeviceInfo:
    device_id: str
    kind: str = "pico2w"  # pico2w | mock
    host: str = "local"
    transport: str = "usb_hid"  # usb_hid | wifi
    endpoint: str = ""  # serial path or host:port
    label: str = ""
    online: bool = True
    meta: Dict = field(default_factory=dict)


class ScreenLock:
    """One foreground GUI session per host (single-machine constraint)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._holder: Optional[str] = None
        self._since: float = 0.0

    def acquire(self, worker_id: str, timeout: float = 30.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if self._holder is None or self._holder == worker_id:
                    self._holder = worker_id
                    self._since = time.time()
                    return True
            time.sleep(0.05)
        return False

    def release(self, worker_id: str) -> None:
        with self._lock:
            if self._holder == worker_id:
                self._holder = None
                self._since = 0.0

    def status(self) -> dict:
        with self._lock:
            return {"holder": self._holder, "since": self._since}


class DeviceRegistry:
    def __init__(self, path: Optional[Path] = None):
        if path is None:
            try:
                from hermes_constants import get_hermes_home

                root = get_hermes_home() / "plobi"
            except Exception:
                root = Path.home() / ".hermes" / "plobi"
            path = root / "hid_devices.json"
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.screen = ScreenLock()
        if not self.path.exists():
            # Default H3 fleet: one local Pico 2W (+ mock for CI)
            self._write(
                {
                    "devices": [
                        asdict(
                            DeviceInfo(
                                device_id="pico-local-1",
                                kind="pico2w",
                                host="local",
                                transport="usb_hid",
                                label="Pico 2W primary",
                            )
                        ),
                        asdict(
                            DeviceInfo(
                                device_id="mock-1",
                                kind="mock",
                                host="local",
                                transport="usb_hid",
                                label="Mock HID (tests / dry-run)",
                            )
                        ),
                    ]
                }
            )

    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"devices": []}

    def _write(self, data: dict) -> None:
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def list_devices(self) -> List[DeviceInfo]:
        return [DeviceInfo(**d) for d in self._read().get("devices", [])]

    def get(self, device_id: str) -> Optional[DeviceInfo]:
        for d in self.list_devices():
            if d.device_id == device_id:
                return d
        return None

    def upsert(self, device: DeviceInfo) -> DeviceInfo:
        data = self._read()
        devices = data.get("devices", [])
        for i, raw in enumerate(devices):
            if raw.get("device_id") == device.device_id:
                devices[i] = asdict(device)
                data["devices"] = devices
                self._write(data)
                return device
        devices.append(asdict(device))
        data["devices"] = devices
        self._write(data)
        return device

    def pick_available(self, prefer_kind: str = "pico2w") -> Optional[DeviceInfo]:
        devices = self.list_devices()
        online = [d for d in devices if d.online]
        for d in online:
            if d.kind == prefer_kind:
                return d
        return online[0] if online else None
