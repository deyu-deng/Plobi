"""Compute routing: HID primary, aigw bypass."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class RouteDecision:
    surface: str
    path: str  # hid | aigw | local | blocked
    reason: str
    fallback: Optional[str] = None


class ComputeRouter:
    """Map intent/surface → execution path."""

    RULES = {
        "marvis": RouteDecision(
            surface="marvis",
            path="hid",
            reason="Marvis is closed-source GUI; HID is the only path",
            fallback=None,
        ),
        "antigravity": RouteDecision(
            surface="antigravity",
            path="aigw",
            reason="Protocol-adapted via aigw (OpenDesign-style); not HID primary",
            fallback="hid",
        ),
        "cursor": RouteDecision(
            surface="cursor",
            path="hid",
            reason="Exploratory HID; aigw reverse path incomplete",
            fallback="aigw",
        ),
        "browser": RouteDecision(
            surface="browser",
            path="hid",
            reason="Browser free tiers via GUI automation",
            fallback=None,
        ),
        "workbuddy": RouteDecision(
            surface="workbuddy",
            path="aigw",
            reason="Desktop quota via aigw when available",
            fallback="hid",
        ),
        "master": RouteDecision(
            surface="master",
            path="local",
            reason="Master uses paid API — never free-tier GUI",
            fallback=None,
        ),
    }

    def route(self, surface: str, *, prefer: Optional[str] = None) -> RouteDecision:
        key = (surface or "").strip().lower()
        if prefer in {"hid", "aigw", "local"}:
            base = self.RULES.get(key) or RouteDecision(key or "unknown", prefer, "explicit prefer")
            return RouteDecision(
                surface=base.surface,
                path=prefer,
                reason=f"explicit prefer={prefer}; default would be {base.path}",
                fallback=base.fallback,
            )
        if key in self.RULES:
            return self.RULES[key]
        return RouteDecision(
            surface=key or "unknown",
            path="hid",
            reason="unknown surface — default HID worker",
            fallback="aigw",
        )

    def table(self) -> dict:
        return {
            k: {"path": v.path, "reason": v.reason, "fallback": v.fallback}
            for k, v in self.RULES.items()
        }
