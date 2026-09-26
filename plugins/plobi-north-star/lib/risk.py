"""Risk levels for North Star task autonomy."""

from __future__ import annotations

from enum import Enum


class RiskLevel(str, Enum):
    L0_OBSERVE = "L0_observe"
    L1_LOCAL_MUTATE = "L1_local_mutate"
    L2_EXTERNAL_MSG = "L2_external_msg"
    L3_MONEY_OR_IRREVERSIBLE = "L3_money_or_irreversible"
    L4_SELF_MODIFY = "L4_self_modify"

    @classmethod
    def parse(cls, value: str | None) -> "RiskLevel":
        if value is None:
            return cls.L1_LOCAL_MUTATE
        raw = str(value).strip()
        for member in cls:
            if raw == member.value or raw == member.name or raw.lower() == member.value.lower():
                return member
        # Allow short forms: L0, L1, ...
        short = raw.upper()
        mapping = {
            "L0": cls.L0_OBSERVE,
            "L1": cls.L1_LOCAL_MUTATE,
            "L2": cls.L2_EXTERNAL_MSG,
            "L3": cls.L3_MONEY_OR_IRREVERSIBLE,
            "L4": cls.L4_SELF_MODIFY,
        }
        if short in mapping:
            return mapping[short]
        raise ValueError(f"Unknown risk level: {value!r}")


def can_run_at_night(risk: RiskLevel, *, allow_l1: bool = True) -> bool:
    """Return True if night autonomy may execute this risk without human approval."""
    if risk == RiskLevel.L0_OBSERVE:
        return True
    if risk == RiskLevel.L1_LOCAL_MUTATE:
        return allow_l1
    return False


def requires_human(risk: RiskLevel) -> bool:
    return risk in {
        RiskLevel.L2_EXTERNAL_MSG,
        RiskLevel.L3_MONEY_OR_IRREVERSIBLE,
        RiskLevel.L4_SELF_MODIFY,
    }
