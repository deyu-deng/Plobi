"""L1 把一段活交给**本机外部 AI 软件**去干的两条嘴（WP-L2-OUTSOURCE-ENTRY 判据 1/3/4）。

- `external_offload_status`：这台有哪些外部 Agent、每个**能不能用 + 为什么不能**。
- `external_offload_run`：交一段活过去，回来指名道姓报"哪段是它干的、产出在哪、怎么验、花了多少"。

三条纪律写在这儿，免得又长出第二份：
1. **可问性只有一条判据**——`plobi.delegation.offload.availability()`。清单与工具共用它，
   不在这里再判一次（裁定 94 同族；对照测试钉住）。
2. **默认关不在代码里**。`check_fn` 只回答"这台有没有可驱动的命令"这种**当场事实**；
   "这个能力开没开"是配置面 `toolsets` 里那一格（裁定 78 入口不偷默认，
   也避开本线踩过的"恒 False 的 check_fn 把开关关在代码里"那颗雷）。
3. **金额算不出就报 null + 状态**（R-073），不许报 0。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from plobi.delegation.offload import (
    CAP_PERMISSION_MODE,
    CAP_STREAMING,
    CAP_TERMINAL,
    CAP_USAGE_REPORT,
    CAP_WRITES,
    FAIL_BLOCKED_BY_GATE,
    FAIL_NO_CHANNEL,
    FAIL_NOT_LOGGED_IN,
    FAIL_TIMEOUT,
    ExternalAgent,
    OffloadResult as LedgerResult,
    RoundLedger,
    availability,
    failure_note,
    selectable,
    summarize_round,
)
from tools.registry import registry

TOOLSET = "offload"

#: 各外部 Agent 自带的"最放开档"旋钮在 `agent/external_offload.permission_flags` 里；
#: 这里只留**登录态**的本地探针（不跑它、不花钱）。
#: 探针取**保守方向**：指名位置里没有凭据 ⇒ 判"未登录"，不判"探不到所以能用"——
#: 清单把一个点了必失败的东西列成可用，是这条刀明令禁止的形状（任务书硬边界）。
#: 已知缺口：agy 的 OAuth 凭据不在 `~/.gemini/antigravity-cli/` 下（10-13 现核：该目录
#: 只有 log/db/history，没有任何 auth 文件），所以它现在恒判"未登录"。要放开它，
#: 补的是这里的探针路径，不是清单那一条判据。
_CREDENTIAL_HINTS = {
    "codebuddy": ("~/.codebuddy/local_storage",),
    "agy": ("~/.agy/credentials", "~/.config/agy/credentials"),
    # Qoder 的登录态落在 `~/.qoder/.auth`（10-22 实测：`qodercli login` 之前没有它，
    # 登录之后它出现）。只探在不在，**不读内容**——里面是凭据。
    "qoder": ("~/.qoder/.auth",),
}


def _probe_logged_in(agent_name: str) -> Optional[bool]:
    for raw in _CREDENTIAL_HINTS.get(agent_name, ()):
        candidate = Path(os.path.expanduser(raw))
        try:
            if candidate.is_dir():
                return any(candidate.iterdir())
            if candidate.is_file():
                return True
        except OSError:
            continue
    return False


def agent_roster() -> List[ExternalAgent]:
    """本机外部 Agent 的名册——只填**结构性事实**，判断交给 `availability()`。

    名单从通道那张表来（`EXTERNAL_AGENTS`），不在这里再列一遍名字：第三家登记进表
    就该在名册里出现，两处各列一份迟早不一致（裁 94）。
    """
    from agent import external_offload as channel

    roster: List[ExternalAgent] = []
    for name in channel.EXTERNAL_AGENTS:
        binary = channel.resolve_command(name)
        try:
            flags = channel.permission_flags(name)
        except ValueError:
            flags = ()
        roster.append(
            ExternalAgent(
                name=name,
                installed=bool(binary),
                logged_in=_probe_logged_in(name),
                channel_ready=bool(binary),
                permission_flag=" ".join(flags) or (channel.PERMISSION_MODES[0] if flags else ""),
                capabilities={
                    CAP_PERMISSION_MODE: bool(flags),
                    CAP_WRITES: True,
                    CAP_TERMINAL: True,
                    # 只有 CodeBuddy 那一发实测回过 usage；Qoder 的 envelope 里有没有
                    # 这一格**还没实测**，所以按 False 记，不拿"应该有"当"有"（裁 191）。
                    CAP_USAGE_REPORT: name == channel.AGENT_CODEBUDDY,
                    CAP_STREAMING: False,
                },
            )
        )
    return roster


def classify_failure(error: str) -> str:
    """通道那一句英文错 → 判据 4 要求的四个**分得开**的失败码之一。

    认不出来的不硬归成"超时"（返回空串，由调用方把原话带出去）。
    """
    text = (error or "").lower()
    if "did not finish within" in text or "timed out" in text:
        return FAIL_TIMEOUT
    if "not logged" in text or "logged out" in text or "oauth" in text or "credential" in text:
        return FAIL_NOT_LOGGED_IN
    if "no drivable command" in text or "cannot start" in text:
        return FAIL_NO_CHANNEL
    if "refusing to offload" in text or "cwd does not exist" in text or "permission mode" in text:
        return FAIL_BLOCKED_BY_GATE
    return ""


def to_ledger_result(channel_result: Any) -> LedgerResult:
    """通道形状 → 台账形状（两边字段名不同，转换只在这一处）。"""
    artifacts = tuple(
        str(item.get("path") if isinstance(item, dict) else item)
        for item in (getattr(channel_result, "artifacts", ()) or ())
    )
    usage = dict(getattr(channel_result, "usage", {}) or {})
    if usage.get("cost_usd") is None and getattr(channel_result, "cost", None) is not None:
        usage["cost_usd"] = channel_result.cost
    return LedgerResult(
        agent=channel_result.agent,
        cwd=str(channel_result.cwd),
        permission_flag=" ".join(getattr(channel_result, "permission_flags", ()) or ()),
        artifact_paths=artifacts,
        usage=usage or None,
        cost_status=getattr(channel_result, "cost_status", "unknown") or "unknown",
        duration_seconds=float(getattr(channel_result, "duration_s", 0.0) or 0.0),
        failure="" if channel_result.ok else classify_failure(getattr(channel_result, "error", "")),
        # 外部会话标识：通道读出来是什么就是什么，读不到是 ""（空是读数，不是失败）。
        external_session=str(getattr(channel_result, "external_session", "") or ""),
        external_session_source=str(getattr(channel_result, "external_session_source", "") or ""),
        # 回执那一族原样带过去（转换只在这一处，见本函数第一句）。
        job=str(getattr(channel_result, "job", "") or ""),
        reply=str(getattr(channel_result, "reply", "") or ""),
        reply_status=str(getattr(channel_result, "reply_status", "") or ""),
        # 通道从 stdout 解析出来的正文（`agent/external_offload._parse_envelope` 的 `text`）。
        # 以前它在这一跳被丢掉 ⇒ 台账里没有它说过的一句话，界面上那一格无事可显示。
        answer=str(getattr(channel_result, "text", "") or ""),
        reply_to=str(getattr(channel_result, "reply_to", "") or ""),
    )


# --------------------------------------------------------------------------- #
# handlers
# --------------------------------------------------------------------------- #

def _handle_status(_args: Dict[str, Any], **_kw: Any) -> str:
    rows = selectable(agent_roster())
    return json.dumps({"agents": rows, "note": "can_use=false 的那一行点了必失败，别当备选。"},
                      ensure_ascii=False)


def _handle_run(args: Dict[str, Any], **_kw: Any) -> str:
    from agent import external_offload as channel

    task = str(args.get("task") or "").strip()
    if not task:
        return json.dumps({"ok": False, "error": "task 是空的：不知道要交什么过去。"}, ensure_ascii=False)

    agent_name = str(args.get("agent") or channel.AGENT_CODEBUDDY).strip()
    cwd = str(args.get("cwd") or "").strip()
    roster = {a.name: a for a in agent_roster()}
    agent = roster.get(agent_name)
    if agent is None:
        return json.dumps({"ok": False, "error": f"没有这个外部 Agent：{agent_name!r}。"
                           f"可交的：{'、'.join(sorted(roster)) or '（本机一个都没有）'}"},
                          ensure_ascii=False)
    usable, why_not = availability(agent)
    if not usable:
        return json.dumps({"ok": False, "agent": agent_name, "error": why_not,
                           "asked": task[:280]}, ensure_ascii=False)

    if not cwd:
        # 不传 cwd 时通道会落到它自己的默认产物区（PLOBI_HOME 下，D 盘合规）。
        # 但"真的推进了项目"要求落在项目自己的目录 ⇒ 这里明说，不静默替用户选。
        cwd = str(channel.default_workdir())

    # 谁派的活，就让它回给谁。读路用现成的那只（与 `cronjob_tools._speaker_recipient_name`
    # 同一个来源）。名字对不对**不在这里判**：`say` 投递时按名册拒，并把可发的人列回来，
    # 这里再查一遍就是第二份真源（裁 94）。空身份 = L1，名册里没有那一行 ⇒ 不附回话那一格，
    # 也不硬编一个收件人。
    from gateway.session_context import get_session_env

    reply_to = (get_session_env("PLOBI_AGENT_ID") or "").strip()

    result = channel.run_task(task, cwd=cwd, agent=agent_name,
                              permission_mode=args.get("permission_mode") or None,
                              reply_to=reply_to)
    ledger_row = to_ledger_result(result)
    RoundLedger().append(ledger_row, brief=task)

    detail = getattr(result, "error", "") or ""
    payload: Dict[str, Any] = {
        "ok": bool(result.ok),
        "agent": agent_name,
        "cwd": str(cwd),
        "permission_flag": ledger_row.permission_flag,
        "duration_seconds": ledger_row.duration_seconds,
        "artifacts": [{"path": p, "sha256_12": _sha(p)} for p in ledger_row.artifact_paths],
        "usage": ledger_row.usage,
        "cost": getattr(result, "cost", None),
        "cost_status": ledger_row.cost_status,
        "report": summarize_round([ledger_row]),
        "asked": task[:280],
    }
    if reply_to:
        payload["reply_to"] = reply_to
    # 回执这一族照实报：`not_filed` 是"它没回"，与"它回了但被我们读坏了"是两句话。
    payload["job"] = ledger_row.job
    payload["reply"] = ledger_row.reply
    payload["reply_status"] = ledger_row.reply_status
    if ledger_row.external_session:
        # 有值才提这一格，没值不提、也不写"未知会话"糊弄（判据：空串不是可跳的标识）。
        payload["external_session"] = ledger_row.external_session
        payload["external_session_source"] = ledger_row.external_session_source
    if not result.ok:
        payload["error"] = failure_note(ledger_row.failure) if ledger_row.failure else detail
        payload["raw_error"] = detail
    return json.dumps(payload, ensure_ascii=False)


def _sha(path: str) -> str:
    try:
        import hashlib
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]
    except OSError:
        return "unreadable"


# --------------------------------------------------------------------------- #
# schemas + registration
# --------------------------------------------------------------------------- #

STATUS_SCHEMA = {
    "name": "external_offload_status",
    "description": (
        "列出本机上可以外包过去的外部 AI 软件，每个都带『能不能用 + 为什么不能』。"
        "在决定把一段活交出去之前先问它；can_use=false 的不要交。"
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

RUN_SCHEMA = {
    "name": "external_offload_run",
    "description": (
        "把**一整段能独立验收**的活交给本机的外部 AI 软件（Qoder / CodeBuddy / agy），"
        "你自己只负责拆活、验收、把结果拼回用户的话。"
        "**默认先想到它**：用户点名某台软件，或这段活是一整块（写文件、跑脚本、查资料出成品）"
        "且不需要本会话的上下文时，先 external_offload_status 看谁可用，再交出去——"
        "不要自己从头吭哧吭哧做完。"
        "别外包：一句话能答的、必须用本会话上下文的（继续上一轮、引用我们刚说的话）、"
        "以及目的地在 Plobi 自己那几个代码仓里的（会被拒）。"
        "交完必须回报：哪一段是它干的、产出落在哪个路径 + sha256、耗时、用量或『未知』。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "要外包的那一段，一句话说清做到什么程度。"},
            "cwd": {"type": "string", "description": "它干活的项目目录（产出就落这里）。"},
            "agent": {"type": "string", "description": "交给谁；先查 external_offload_status。默认 codebuddy。"},
            "permission_mode": {"type": "string", "description":
                "该 Agent 自带的权限档名，档名各家不通用（CodeBuddy 是 `bypassPermissions`，"
                "Qoder 是 `bypass_permissions`）。留空＝用该家登记的默认档："
                "CodeBuddy 是最放开那一档（实测过：不放开就只烧钱不写文件），"
                "Qoder 是最保守那一档（没实测过，不拿未测当放开）。档名不对会当场报错，不会静默降级。"},
        },
        "required": ["task"],
    },
}


SCAN_SCHEMA = {
    "name": "external_sessions_scan",
    "description": (
        "列出本机上各家 AI 软件的会话（Qoder / CodeBuddy / Marvis），并带上**已经自动算好的**相关判断："
        "`related=true`（项目目录或标题点到你）／`null`（还没判断）／`false`（你标过无关）。"
        "用户问「有哪些跟我有关的对话」、或你要决定跟哪条继续谈之前，先扫这一遍；"
        "**判不准的别猜**，看完标题与项目再决定，标完用 external_sessions_mark 记下来。"
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

MARK_SCHEMA = {
    "name": "external_sessions_mark",
    "description": (
        "把你对会话归属的判断记下来（左栏与下一次 scan 都按它过滤）。只标你**确定**的："
        "有关系的标 related=true，确认无关的标 related=false，其余留空让它继续待判断。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "vendor": {"type": "string", "description": "哪家软件：qoder / codebuddy / marvis。"},
                        "id": {"type": "string", "description": "scan 回来的会话号。"},
                        "related": {"type": "boolean", "description": "跟你有关＝true，无关＝false。"},
                    },
                    "required": ["vendor", "id", "related"],
                },
            },
        },
        "required": ["items"],
    },
}


def _agent_identity() -> str:
    from gateway.session_context import get_session_env

    return (get_session_env("PLOBI_AGENT_ID") or "").strip()


def _handle_scan(args: Dict[str, Any], **_kw: Any) -> str:
    agent = _agent_identity()
    if not agent:
        return json.dumps({"ok": False, "error": "这个会话没有 Agent 名，判不了归属"}, ensure_ascii=False)

    from plobi.delegation.external_sessions import sessions_for_agent

    payload = sessions_for_agent(agent)
    rows = [
        {
            "id": item["id"],
            "project": item.get("project") or "",
            "related": item.get("related"),
            "related_source": item.get("related_source"),
            "title": item.get("title") or "",
            "updated_at": item.get("updated_at") or "",
            "vendor": item["vendor"],
        }
        for item in payload["sessions"]
    ]
    counts = {"related": 0, "unknown": 0, "unrelated": 0}
    for row in rows:
        counts["related" if row["related"] is True else "unrelated" if row["related"] is False else "unknown"] += 1

    return json.dumps({"counts": counts, "ok": True, "sessions": rows[:80], "sources": payload.get("sources", [])},
                      ensure_ascii=False)


def _handle_mark(args: Dict[str, Any], **_kw: Any) -> str:
    agent = _agent_identity()
    if not agent:
        return json.dumps({"ok": False, "error": "这个会话没有 Agent 名，记不了归属"}, ensure_ascii=False)
    items = args.get("items")
    if not isinstance(items, list):
        return json.dumps({"ok": False, "error": "items 要给一个数组"}, ensure_ascii=False)

    from plobi.delegation.external_sessions import mark_for_agent

    result = mark_for_agent(agent, [item for item in items if isinstance(item, dict)])

    return json.dumps(result, ensure_ascii=False)


def _check_drivable() -> bool:
    """只判一个当场事实：这台有没有可驱动的外部 Agent 命令。**不当默认关**（见模块头 2）。"""
    from agent import external_offload as channel

    # 遍历声明的全家，而不是写死两家：Qoder 已经是一等公民（10-23）。
    return any(channel.resolve_command(name) for name in channel.EXTERNAL_AGENTS)


registry.register(
    name="external_offload_status",
    toolset=TOOLSET,
    schema=STATUS_SCHEMA,
    handler=_handle_status,
    check_fn=_check_drivable,
    emoji="📤",
)

registry.register(
    name="external_offload_run",
    toolset=TOOLSET,
    schema=RUN_SCHEMA,
    handler=_handle_run,
    check_fn=_check_drivable,
    emoji="📤",
)

registry.register(
    name="external_sessions_scan",
    toolset=TOOLSET,
    schema=SCAN_SCHEMA,
    handler=_handle_scan,
    check_fn=_check_drivable,
    emoji="🗂",
)

registry.register(
    name="external_sessions_mark",
    toolset=TOOLSET,
    schema=MARK_SCHEMA,
    handler=_handle_mark,
    check_fn=_check_drivable,
    emoji="🗂",
)
