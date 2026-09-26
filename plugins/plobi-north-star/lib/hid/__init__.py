"""HID worker fleet abstraction (single-machine now, multi-worker ready)."""

from .device import DeviceInfo, DeviceRegistry, ScreenLock
from .worker import HidJob, HidWorkerPool, HidResult

__all__ = [
    "DeviceInfo",
    "DeviceRegistry",
    "HidJob",
    "HidResult",
    "HidWorkerPool",
    "ScreenLock",
]
