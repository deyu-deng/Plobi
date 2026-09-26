"""Outbound notification channels.

Public: :class:`Notifier` (protocol), :func:`get_notifier` (configured default).
"""

from __future__ import annotations

import threading
from typing import Optional

from .base import Notifier, NullNotifier, RecordingNotifier, SendOutcome
from .dingtalk import DingTalkNotifier

__all__ = [
    "DingTalkNotifier",
    "Notifier",
    "NullNotifier",
    "RecordingNotifier",
    "SendOutcome",
    "get_notifier",
    "set_notifier",
]

_DEFAULT: Optional[Notifier] = None
_DEFAULT_LOCK = threading.Lock()


def get_notifier() -> Notifier:
    """Configured channel, or a no-op when nothing is set up yet."""
    global _DEFAULT
    if _DEFAULT is None:
        with _DEFAULT_LOCK:
            if _DEFAULT is None:
                dingtalk = DingTalkNotifier()
                _DEFAULT = dingtalk if dingtalk.configured else NullNotifier()
    return _DEFAULT


def set_notifier(notifier: Optional[Notifier]) -> None:
    """Injection seam for tests and for reconfiguration at runtime."""
    global _DEFAULT
    _DEFAULT = notifier
