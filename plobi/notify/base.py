"""Notification transport contract.

Kept deliberately tiny so the channel can change without touching callers:
DingTalk is the MVP stand-in for a desktop-native and, later, a Plobi mobile
client (docs/adr/0002-notification-channel-dingtalk-robot.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class SendOutcome:
    ok: bool
    detail: str = ""


class Notifier(Protocol):
    @property
    def configured(self) -> bool:
        ...

    def send(self, text: str) -> SendOutcome:
        ...


class NullNotifier:
    """Drops messages. Default until a channel is configured."""

    @property
    def configured(self) -> bool:
        return False

    def send(self, text: str) -> SendOutcome:
        return SendOutcome(ok=False, detail="no notifier configured")


class RecordingNotifier:
    """Collects messages in memory — for tests and dry runs."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    @property
    def configured(self) -> bool:
        return True

    def send(self, text: str) -> SendOutcome:
        self.sent.append(text)
        return SendOutcome(ok=True)
