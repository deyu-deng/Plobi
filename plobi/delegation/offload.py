"""决策面：把一段活交给本机外部 AI 软件，并把这件事记成查得到的一轮（WP-L2-OUTSOURCE-ENTRY）。

本模块只管**决策与记账**。真子进程通道在额度线那半（`WP-EXTERNAL-AGENT-OFFLOAD`：
能调的函数入口 / 返回形状 / 清单来源），这里**不重复实现一条**——需要通道时以
:class:`OffloadChannel` 这个 Protocol 注入，测试用假通道。

三条形状约定（都是仓里已有判据，不是本模块发明的新规矩）：

* 「能不能用 + 为什么不能」只有一个判据函数：:func:`availability`。清单和工具**必须**都读它
  （同 ``plobi/agents/registry.py`` 的 ``agent_askability`` 那条 R-072 教训：两边各判一次，
  就只能靠试）。
* 平台差异走**声明式能力位**（:attr:`ExternalAgent.capabilities`），不许按名字分支（裁定 98 三）。
* 金额算不出时**报 `None` + `cost_status`，不许报 0**（R-073：0 与"真的没花钱"字节相同）。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Protocol

__all__ = [
    "CAP_Writes",
    "ExternalAgent",
    "OffloadChannel",
    "OffloadResult",
    "RoundLedger",
    "availability",
    "failure_note",
    "selectable",
    "summarize_round",
]

#: 能力位名单。**只有这一个地方**列它；加一个外部 Agent = 实现通道 + 给出这些位的值，
#: 不改本模块的判定（判据：新 Agent 不需要同名分支就能被排进/排掉）。
CAP_PERMISSION_MODE = "permission_mode"      # 支持传"最放开档"（裁定 99 一）
CAP_WRITES = "writes"                        # 能改盘上的东西
CAP_TERMINAL = "terminal"                    # 能跑命令
CAP_USAGE_REPORT = "usage_report"             # 通道会回吐用量（没有 ⇒ 金额必为 None）
CAP_STREAMING = "streaming"                   # 能中途报进度
ALL_CAPABILITIES = (
    CAP_PERMISSION_MODE,
    CAP_WRITES,
    CAP_TERMINAL,
    CAP_USAGE_REPORT,
    CAP_STREAMING,
)

#: 四种失败各给不同的一句话（判据 4）。糊成"外包失败"就是没做到。
FAIL_NOT_LOGGED_IN = "not_logged_in"
FAIL_NO_CHANNEL = "no_channel"
FAIL_TIMEOUT = "timeout"
FAIL_BLOCKED_BY_GATE = "blocked_by_plobi_gate"
FAILURES = (
    FAIL_NOT_LOGGED_IN,
    FAIL_NO_CHANNEL,
    FAIL_TIMEOUT,
    FAIL_BLOCKED_BY_GATE,
)

_FAILURE_NOTES = {
    FAIL_NOT_LOGGED_IN: "它没登录：这一轮不会交给它（登录要本人做一次，不代跑）。",
    FAIL_NO_CHANNEL: "通道还没接上（额度线 WP-EXTERNAL-AGENT-OFFLOAD 未交入口），不是它坏了。",
    FAIL_TIMEOUT: "到时间没回话：这一段没干完，产出按没有处理。",
    FAIL_BLOCKED_BY_GATE: "被 Plobi 自己的闸拦下：外部 Agent 回调危险工具仍需人批，这道闸不因为它就松。",
}


def failure_note(code: str) -> str:
    """失败码 → 那一句人话。未知码不猜成"超时"（宁可说不认识）。"""
    return _FAILURE_NOTES.get(code, f"没记到的失败类型 {code!r}：不去猜它属于哪一种。")


@dataclass(frozen=True)
class ExternalAgent:
    """一个本机外部 AI 软件在**决策面眼里**的样子：只有结构性事实。"""

    name: str
    installed: bool = False
    logged_in: Optional[bool] = None          # None = 探不到，不判成"不能"
    channel_ready: bool = False                # 通道半是否已交
    permission_flag: str = ""                  # 该 Agent 自带的最放开档（裁定 99 一）
    capabilities: dict[str, bool] = field(default_factory=dict)

    def can(self, capability: str) -> bool:
        return bool(self.capabilities.get(capability))


def availability(agent: ExternalAgent) -> tuple[bool, str]:
    """现在能不能把活交给它；不能就给一句话原因。**唯一判据**，清单与工具共用。"""
    if not agent.installed:
        return False, f"本机没有 {agent.name}：不是坏了，是没装。"
    if not agent.channel_ready:
        return False, failure_note(FAIL_NO_CHANNEL)
    if agent.logged_in is False:
        return False, failure_note(FAIL_NOT_LOGGED_IN)
    return True, ""


def selectable(agents: Iterable[ExternalAgent]) -> list[dict[str, Any]]:
    """清单：**每个候选都带 (能不能用 + 为什么)**，绝不把不能的伪装成可点。

    这里不调第二份判据 —— 判据只有 :func:`availability` 一个（对照测试钉住这条）。
    """
    out: list[dict[str, Any]] = []
    for agent in agents:
        usable, why_not = availability(agent)
        out.append(
            {
                "name": agent.name,
                "can_use": usable,
                "why_not": why_not,
                "permission_flag": agent.permission_flag,
                "capabilities": {key: bool(agent.can(key)) for key in ALL_CAPABILITIES},
            }
        )
    return out


@dataclass(frozen=True)
class OffloadResult:
    """通道交回来的那一轮（判据 1 要的那些字段）。金额算不出必须是 None。"""

    agent: str
    cwd: str
    permission_flag: str
    artifact_paths: tuple[str, ...] = ()
    usage: Optional[dict[str, Any]] = None
    cost_status: str = "unknown"
    duration_seconds: float = 0.0
    failure: str = ""
    #: 外部软件自己那一条会话的标识（契约 = ``Docs/tasks/offload-session-handle-task.md`` 第 1 条）。
    #: 空串是一个**读数**（通道没带出来，或厂商改了信封），不是失败，也不许拿 0/占位冒充。
    external_session: str = ""
    #: 这个 id 是从哪个键读到的（``result.session_id`` / ``sessionId`` / ``conversation_id`` / ``""``）。
    #: 厂商换信封时这一格当场说出"我们读了什么"，而不是让前端猜。
    external_session_source: str = ""

    @property
    def ok(self) -> bool:
        return not self.failure


def _sha12(path: str) -> str:
    try:
        digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return "unreadable"
    return digest[:12]


def summarize_round(results: Iterable[OffloadResult]) -> str:
    """把一轮外包读成人话（判据 2：给了谁 / 哪个目录 / 什么权限档 / 花了多少，一处能查）。

    用量缺失写成「未知」而不是 0（R-073）；失败写成那一句**分得开**的原因（判据 4）。
    """
    lines: list[str] = []
    for index, item in enumerate(results, start=1):
        flag = item.permission_flag or "（它没有放开档可传）"
        artifacts = "、".join(f"{p}#{_sha12(p)}" for p in item.artifact_paths) or "没有落盘产出"
        if item.ok:
            cost = _money(item)
            lines.append(
                f"{index}. 交给 {item.agent}｜目录 {item.cwd}｜权限档 {flag}｜"
                f"耗时 {item.duration_seconds:.0f}s｜产出 {artifacts}｜{cost}"
            )
        else:
            lines.append(
                f"{index}. 交给 {item.agent}（没成）｜目录 {item.cwd}｜权限档 {flag}｜"
                f"{failure_note(item.failure)}"
            )
    return "\n".join(lines) if lines else "这一轮没有外包：全程是我自己干的。"


def _money(item: OffloadResult) -> str:
    if not item.usage:
        return f"用量未知（cost_status={item.cost_status}）"
    amount = item.usage.get("cost_usd")
    if amount is None:
        return f"用量未知（cost_status={item.cost_status}）"
    return f"花费 ${amount}"


class OffloadChannel(Protocol):
    """通道半的注入缝。**本模块不实现它**（那会是第二条真通道）。"""

    def run(self, agent: ExternalAgent, brief: str, *, cwd: str) -> OffloadResult:  # pragma: no cover
        ...


def total_usd(rows: Iterable[dict[str, Any]]) -> tuple[Optional[float], str]:
    """一批发包记录加起来的真花费。任何一笔算不出 ⇒ 总额也**不能**装作算得出（R-073）。

    单列成函数是为了"按某个分身筛过的那一批"也能用**同一条**算法：以前只有整本台账一个
    总额，答不出"这一格交出去的活花了多少"，而控制台那一处要答的就是后者。
    空批回 ``(None, "priced")`` —— 没有花费不等于花了 0。
    """
    rows = list(rows)
    total = 0.0
    for row in rows:
        amount = (row.get("usage") or {}).get("cost_usd")
        if amount is None:
            return None, row.get("cost_status") or "unknown"
        total += float(amount)

    return (total if rows else None), "priced"


class RoundLedger:
    """流水：每笔外包一行 JSONL，用于"这一轮花了多少"的读数（裁定 99 二）。

    落点 profile-aware（``get_plobi_home()``，不硬编码家目录）；只追加，不改写历史。
    """

    FILENAME = "offload_rounds.jsonl"

    def __init__(self, root: Path | str | None = None):
        self._root = Path(root) if root else None

    @property
    def path(self) -> Path:
        if self._root is not None:
            return self._root / self.FILENAME
        from plobi_constants import get_plobi_home

        return get_plobi_home() / "plobi" / "delegation" / self.FILENAME

    def append(self, result: OffloadResult, *, brief: str) -> dict[str, Any]:
        row: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "agent": result.agent,
            "cwd": result.cwd,
            "permission_flag": result.permission_flag,
            "brief": brief[:280],
            "artifacts": [
                {"path": p, "sha256_12": _sha12(p)} for p in result.artifact_paths
            ],
            "duration_seconds": result.duration_seconds,
            "usage": result.usage,
            "cost_status": result.cost_status,
            "failure": result.failure,
            # 「该用哪条命令回跳」的输入是 (agent, external_session_source)，两样都在这行里：
            # `agent` 早就有了（契约第 3 条据此拒绝再登一个 kind 字段）。
            "external_session": result.external_session,
            "external_session_source": result.external_session_source,
        }
        target = self.path
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row

    def rows(self) -> list[dict[str, Any]]:
        target = self.path
        if not target.is_file():
            return []
        out: list[dict[str, Any]] = []
        for line in target.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # 半行（掉电写在最后一行）不算一条记录，也不回头改文件
        return out

    def round_total_usd(self) -> tuple[Optional[float], str]:
        """整本台账的时间上加起来（算法只有一处：:func:`total_usd`）。"""
        return total_usd(self.rows())
