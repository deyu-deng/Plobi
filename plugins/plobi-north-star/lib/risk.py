"""Risk levels for North Star task autonomy.

六级，与 ``Docs/specs/risk-and-gates.md`` §RiskLevel 那张表逐字对齐。裁定 160 拆掉的是
历史上那一整格 ``L1_local_mutate``：它把「归档日志」和「删文件 / 改开机项 / 装卸软件 /
改 ``.env`` / ``git push``」放在同一个自动档里。现在分两格——``L1a_organize`` 可自动过夜，
``L1b_destructive_local`` 挂起等人批。
"""

from __future__ import annotations

from enum import Enum


class RiskLevel(str, Enum):
    L0_OBSERVE = "L0_observe"
    L1A_ORGANIZE = "L1a_organize"
    L1B_DESTRUCTIVE_LOCAL = "L1b_destructive_local"
    L2_EXTERNAL_MSG = "L2_external_msg"
    L3_MONEY_OR_IRREVERSIBLE = "L3_money_or_irreversible"
    L4_SELF_MODIFY = "L4_self_modify"

    @classmethod
    def parse(cls, value: str | None) -> "RiskLevel":
        if value is None:
            return UNDECLARED
        raw = str(value).strip()
        if not raw:
            return UNDECLARED
        for member in cls:
            if raw == member.value or raw.upper() == member.name.upper():
                return member
        lowered = raw.lower()
        if lowered == LEGACY_L1_VALUE.lower():
            return cls.L1A_ORGANIZE
        short = _SHORT_FORMS.get(lowered)
        if short is not None:
            return short
        raise ValueError(f"Unknown risk level: {value!r}")


#: 历史值。裁定 160 拍定它映射到 ``L1a``（「历史上默认那格就是它」），存量任务不许解析失败。
#: 只管**已经写成这个字符串的存量**——新任务没填等级是另一回事，见 ``UNDECLARED``。
LEGACY_L1_VALUE = "L1_local_mutate"

#: 空值 / 缺字段 / 裸 ``L1`` 一律算「没说是哪一级」，落到重的那格：契约的分界是轻重，
#: 判不出轻重就当重的，比「看着像 L1a 就放行」安全——本刀要收的就是这一半。
UNDECLARED = RiskLevel.L1B_DESTRUCTIVE_LOCAL

_SHORT_FORMS = {
    "l0": RiskLevel.L0_OBSERVE,
    "l1": UNDECLARED,
    "l1a": RiskLevel.L1A_ORGANIZE,
    "l1b": RiskLevel.L1B_DESTRUCTIVE_LOCAL,
    "l2": RiskLevel.L2_EXTERNAL_MSG,
    "l3": RiskLevel.L3_MONEY_OR_IRREVERSIBLE,
    "l4": RiskLevel.L4_SELF_MODIFY,
}


def can_run_at_night(risk: RiskLevel, *, allow_l1a_night: bool = True) -> bool:
    """夜间能否不经人批直接跑。

    旧参数叫 ``allow_l1``，那时「L1」是一整格——一开就把删文件、改 ``.env``、``git push``
    一起放过夜。拆两级之后这个开关**只管 ``L1a``**：``L1b`` 及更高没有任何参数能让它夜跑，
    所以还照旧写 ``allow_l1=...`` 的调用会 ``TypeError``，而不是悄悄沿用旧语义。
    """
    if risk == RiskLevel.L0_OBSERVE:
        return True
    if risk == RiskLevel.L1A_ORGANIZE:
        return allow_l1a_night
    return False


def requires_human(risk: RiskLevel) -> bool:
    """契约那张表里「人审 = 是」的几格；``L1b`` 是本刀新添的一格。"""
    return risk in {
        RiskLevel.L1B_DESTRUCTIVE_LOCAL,
        RiskLevel.L2_EXTERNAL_MSG,
        RiskLevel.L3_MONEY_OR_IRREVERSIBLE,
        RiskLevel.L4_SELF_MODIFY,
    }
