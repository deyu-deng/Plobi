"""「哪种安装方式能被就地应用更新」的唯一判据。

git 自更新链路在 2026-09-06/07 两次摧毁工作树的事故后冻结：`plobi update` 只打印停用说明
并返回 0。仪表盘与桌面的更新入口必须引用这里的同一个答案，而不是各写一份
`install_method in (...)` —— 那种抄本正是「按钮返回成功、什么都没发生」的成因（R-046）。
"""

from typing import Dict, Any

SELF_UPDATE_FROZEN_MSG = (
    "Plobi 本地开发版：git 自更新已禁用（历史事故防护）。代码同步由负责 Agent 手动进行。"
)

# 只有自管虚拟环境的那几条路能就地升级；git 检出被冻结，其余要用户自己在带外做。
_APPLIABLE_METHODS = ("pip",)


def local_self_update_applies(install_method: str) -> bool:
    """True 仅当 `plobi update` 真能把更新落到这台机器上。"""
    return install_method in _APPLIABLE_METHODS


def frozen_refusal(install_method: str, update_command: str) -> Dict[str, Any]:
    """仪表盘 `POST /api/plobi/update` 在按不动时该返回的形状。"""
    return {
        "ok": False,
        "pid": None,
        "name": "plobi-update",
        "error": "self_update_frozen",
        "message": SELF_UPDATE_FROZEN_MSG,
        "update_command": update_command,
    }
