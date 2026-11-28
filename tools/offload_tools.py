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
from datetime import datetime, timezone
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agent.vendor_registry import login_hints_map
from plobi.delegation.external_capabilities import (
    PERMISSION_LEVELS,
    capabilities,
    permission_alias,
    permission_levels_text,
    supports,
    unsupported_note,
    vendor_list_text,
)
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
#:
#: 各家外部 Agent 的**登录痕迹**落点（只探在不在，**不读内容**——里面是凭据）。
#:
#: **真源在 `agent/vendor_registry.py`**（一行一家 ⇒ 加一家只改那一处），这里是它的派生表。
#:
#: 探针取**保守方向**：指名位置里没有凭据 ⇒ 判「未登录」，不判「探不到所以能用」
#: ——清单把一个点了必失败的东西列成可用，是这条刀明令禁止的形状。
#: 但**没登记探针的那一家判「探不到」（None），不判「没登录」**：那是我们没核到，
#: 不是它没登录；`availability()` 只看 `False` 才拦，`None` 不拦（见 `offload.py`）。
#: 今天 opencode / kiro 就在这一档：盘上找不到它们的凭据落点（它们可能走自己的账号体系），
#: 而纸上的 10-30 实测记着 kiro 那一轮**自己跑工具并答对了当前时间**。
_LOGIN_EVIDENCE: dict[str, tuple[str, ...]] = login_hints_map()


def _windows_credential_hits(needle: str) -> Optional[bool]:
    """Windows 凭据管理器里有含这段字的条目吗？**判不了就返回 None**。

    `cmdkey /list` 的输出编码跟着系统代码页走（中文机器上是 GBK），也可能被管道转成
    UTF-16 ⇒ 两种形状都试，命中才算数；命令起不来、或这台不是 Windows ⇒ ``None``
    （**不拿它下「没登录」的结论**——那会把一个已登录的目标冤枉掉）。
    """
    if os.name != "nt":
        return None
    try:
        finished = subprocess.run(  # noqa: S603 — 固定命令，没有用户输入
            ["cmdkey", "/list"], capture_output=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    if finished.returncode != 0:
        return None
    body = (finished.stdout or b"") + (finished.stderr or b"")
    flat = body.decode("utf-8", "replace").replace("\x00", "").lower()
    return needle.lower() in flat


def _credential_expiry(path: Path, field: str) -> Optional[bool]:
    """读凭据文件里**那一个**过期字段。过期 ⇒ False；没过期 ⇒ True；判不了 ⇒ None。

    **只读那一个字段**：不读、不返回、不打印任何 token 值（那不是我们的东西）。

    字段两种形状都认（11-08 21:5x 本机两家的真实形状）：
    - ISO 串（agy：`token.expiry` = `2026-11-05T17:59:05.000000Z`）；
    - 毫秒时间戳（gemini 的 `expiry_date` = `1793901545000`）。
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return None
    value = _dig_field(payload, field)
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            # 毫秒时间戳（gemini）；秒级的话乘 1000。
            millis = float(value) * (1000.0 if float(value) < 1e11 else 1.0)
            expires = datetime.fromtimestamp(millis / 1000.0, tz=timezone.utc)
        else:
            text = str(value).strip()
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            expires = datetime.fromisoformat(text)
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
    now = datetime.now(timezone.utc)
    return expires > now


def _dig_field(payload: Any, field: str) -> Any:
    """按 `a.b` 形状取字段；取不到就 None（不是 False）。"""
    current = payload
    for part in field.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _probe_logged_in(agent_name: str) -> Optional[bool]:
    """这一家登录没有？**三态**：True / False（真没登录）/ None（探不到，不判成不能）。

    **证据按可信度排队**（11-08 21:5x）：
    1. **厂商亲口说的** ⇒ 走**上一趟真跑的台账**（`offload_surface._login_evidence` 那一格）；
       **本函数不跑探针**——清单层文件头第 1 条明令「首屏不跑探针」
       （`qodercli --list-models` 实测能把取数拖过两分钟），`agent_roster()` 是它的上游。
    2. **本地强证据**（登录后才会落盘的凭据，且**没过期**）；
    3. 别的 ⇒ 探不到（None），不判成不能。
    """
    hints = _LOGIN_EVIDENCE.get(agent_name)
    if hints is None:
        # 没登记落点 ⇒ 我们没核到，不替它下结论。
        return None
    decided_no = False
    for raw in hints:
        if raw.startswith("env:"):
            # 环境变量这一条途径：有值就是登上了；没有就是没走这条途径。
            if os.environ.get(raw[4:], "").strip():
                return True
            decided_no = True
            continue
        if raw.startswith("cred:"):
            hit = _windows_credential_hits(raw[5:])
            if hit is None:
                continue  # 这台机器判不了它 ⇒ 不拿这一条下结论。
            if hit:
                return True
            decided_no = True
            continue
        if raw.startswith("json-expiry:"):
            # 「凭据文件 + 那一个过期字段」：文件在但**过期** ⇒ 真没登录（11-08 21:5x 实测：
            # agy 的 `token.expiry` 已过期 ⇒ 真跑回 `Authentication required`；
            # 光看文件在不在会把它判成「能用」——**屏上的假话**）。
            body, _, field = raw[len("json-expiry:"):].partition("#")
            candidate = Path(os.path.expanduser(body))
            try:
                if not candidate.is_file():
                    decided_no = True
                    continue
                expired = _credential_expiry(candidate, field)
            except OSError:
                continue
            if expired is None:
                continue  # 判不了过期 ⇒ 不拿这一条下结论
            if not expired:
                return False  # **已过期** ⇒ 没登录（`_credential_expiry` 的 True 是「没过期」）
            return True
        if raw.startswith("json-field:"):
            # 「配置里那一格」：等于期望值就是登上了（gemini 的 `auth.selectedType`）。
            body, _, want = raw[len("json-field:"):].partition("#")
            key, _, expected = want.partition("=")
            candidate = Path(os.path.expanduser(body))
            try:
                if not candidate.is_file():
                    decided_no = True
                    continue
                with open(candidate, encoding="utf-8", errors="replace") as handle:
                    payload = json.load(handle)
            except (OSError, ValueError):
                continue
            got = _dig_field(payload, key)
            if got is None:
                continue
            if str(got).strip() == expected.strip():
                return True
            decided_no = True
            continue
        candidate = Path(os.path.expanduser(raw))
        try:
            if candidate.is_dir():
                # **目录非空 ≠ 登上了**：10-22 实测 qoder／codebuddy 的凭据目录里有东西，
                # 同一刻 Qoder CLI 自己回的却是「Not logged in · Please run /login」。
                # 所以这一条只当「有过痕迹」，**不判 True 也不判 False** ⇒ 探不到（None）。
                continue
            if candidate.is_file():
                # 文件型落点是「登了才有」的凭证文件（oauth token / creds json）⇒ 能判。
                return True
        except OSError:
            continue
        # 知道落点在哪、那儿却没有 ⇒ 这一条上确实没有痕迹。
        decided_no = True
    return False if decided_no else None


def agent_roster() -> List[ExternalAgent]:
    """本机外部 Agent 的名册——只填**结构性事实**，判断交给 `availability()`。

    名单从通道那张表来（`EXTERNAL_AGENTS`），不在这里再列一遍名字：第三家登记进表
    就该在名册里出现，两处各列一份迟早不一致（裁 94）。
    """
    from agent import external_offload as channel
    from plobi.delegation.config import offload_disabled

    disabled = offload_disabled()
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
                allowed=name not in disabled,
            )
        )
    return roster


def pickable_guests() -> List[Dict[str, Any]]:
    """本机**现在能用**的客席（各带能力与权限档）。

    判据只有一条：:func:`plobi.delegation.offload.availability`（清单与工具共用它，
    裁 94 同族）——这里只做**筛**，不重判一次。
    """
    return [row for row in selectable(agent_roster()) if row.get("can_use")]


def default_guest() -> Tuple[str, str]:
    """**默认那家**：本机真正可用的那一家。

    回 ``(名字, 一句人话)``：名字为空 ⇒ 本机一台可用的都没有，第二格是
    「装了谁、为什么现在不能用」——**不替用户点名一台他没装的**。

    为什么不再写死某一家（``WP-GUEST-CALL-ATOMIC``，11-15）：原先「留空＝用默认那家」
    对着的是**硬编码的一家** ⇒ 本机装的不是它就必然失败，那句错还把用户引向一个他
    没装的东西。真的默认＝**本机可用清单里的第一行**（登记顺序 ⇒ 可预期）。
    """
    rows = pickable_guests()
    if rows:
        return str(rows[0]["name"]), ""
    everything = selectable(agent_roster())
    if not everything:
        return "", "本机一台外部 AI 软件都没有装：先装一台再来派活。"
    detail = "；".join(
        f"{row['name']}（{row.get('why_not') or '现在不可用'}）" for row in everything
    )
    return "", f"本机没有一台现在能用的外部 AI 软件：{detail}。"


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
        # 归属与命名那一族（`WP-EXTERNAL-THREAD-OWNER` §2.3）：台账要留一行「谁派的名叫
        # 什么的活、是不是续上的」，所以这几格跟着通道结果一起过去。`owners` 是名单
        # （用户 11-22：一条会话可以多归属），`owner` 留着当老读侧能认的一格逗号串。
        owners=list(getattr(channel_result, "owners", ()) or ()),
        owner=str(getattr(channel_result, "owner", "") or ""),
        title=str(getattr(channel_result, "title", "") or ""),
        purpose=str(getattr(channel_result, "purpose", "") or ""),
        resumed=bool(getattr(channel_result, "resumed", False)),
        outcome=str(getattr(channel_result, "outcome", "") or ""),
        waiting_on=str(getattr(channel_result, "waiting_on", "") or ""),
    )


# --------------------------------------------------------------------------- #
# handlers
# --------------------------------------------------------------------------- #

def _vendor_facts() -> Dict[str, Dict[str, Any]]:
    """每家外部软件的**决策事实**，全从真源表派生（这里不另列一份名字/描述）。

    为什么要这一层：以前 status 只回 `name / can_use / why_not / capabilities`——
    Principal 拿到的是一串 id，**看不出这一家是干什么的、能不能接着说话**，
    于是只能按名字眼熟程度挑。名册要能让它自己决策，就得把「是什么 + 能干什么」
    一起递过去；但**不许在工具层手抄一份**（第三家进来就要改两处 ⇒ 迟早漂）。

    所以：人工那句人话，**实测过的那几家**在 `external_capabilities.VENDORS[..].blurb`
    （写的是「会话长什么样 + 能拿它干什么」），**没实测过的**照官方公开信息写在
    真源表 `VendorRow.blurb`（写的是「它是谁家的什么东西」）——
    `blurb_source` 这一格就是用来分清这两种的，别把它们混成一样可信。
    其余能力位一律从 `VENDOR_ROWS` 的真字段反射出来——**表里没有的格子就不补**，
    空着比编一句强。
    """
    from agent.vendor_registry import VENDOR_ROWS

    facts: Dict[str, Dict[str, Any]] = {}
    for row in VENDOR_ROWS:
        spec = capabilities(row.id)  # 只在「接进派活/会话腿」的那几家有；没有就是 None
        # 实测那几家的描述压过公开定位（同一家两处都有时，以本机现核的为准）
        measured = bool(spec is not None and spec.blurb)
        facts[row.id] = {
            # 这一家叫什么（人话名），没登记显示名就照 id 原样，不硬编一个"漂亮名字"
            "label": spec.label if spec is not None else row.id,
            # 一句人话：这一家是干什么的。空串 = 还没写过（不装成有）
            "blurb": (spec.blurb if measured else row.blurb),
            # 上面那句的出处：measured＝本机实测过、public＝照官方公开信息写的、空＝还没写
            "blurb_source": "measured" if measured else ("public" if row.blurb else ""),
            # 能不能派活（False = 只能读它的会话，比如 Marvis / Cursor）
            "offloadable": bool(row.offloadable),
            # 它有几条派活腿：cli（命令行）/ acp（协议）
            "legs": list(row.legs),
            # 派完活能不能接着那条会话继续说（续不上就只能新开）
            "resumable": bool(spec.acp_resume) if spec is not None else bool(row.resume_argv),
            # 它的会话在本机落哪儿（空 = 还没接读法 ⇒ 会话面看不见它）
            "session_source": row.session.vendor if row.session else "",
            # 怎么装它（空 = 没登记安装命令，别硬给一条）
            "install": row.install.command if row.install else "",
        }
    return facts


def _handle_status(_args: Dict[str, Any], **_kw: Any) -> str:
    rows = selectable(agent_roster())
    facts = _vendor_facts()
    for row in rows:
        row.update(facts.get(str(row.get("name") or ""), {}))
    return json.dumps(
        {
            "agents": rows,
            "note": (
                "can_use=false 的那一行点了必失败，别当备选。"
                "挑之前先看这几格：`blurb`（这一家是干什么的，空＝还没写）、"
                "`blurb_source`（measured＝本机实测过、public＝照官方公开信息写的，"
                "后者只保证「它是谁家的什么东西」，不含本机实测结论）、"
                "`offloadable`（false 只能读它的会话，派不出去）、`legs`（有哪几条派活腿）、"
                "`resumable`（true 才能派完接着那条会话说）、"
                "`session_source`（空＝它的会话在这儿看不见，别指望 scan 到它）。"
            ),
        },
        ensure_ascii=False,
    )


def _handle_run(args: Dict[str, Any], **_kw: Any) -> str:
    from agent import external_offload as channel

    task = str(args.get("task") or "").strip()
    if not task:
        return json.dumps({"ok": False, "error": "task 是空的：不知道要交什么过去。"}, ensure_ascii=False)

    agent_name = str(args.get("agent") or "").strip()
    if not agent_name:
        # 「留空＝用默认那家」＝**本机真正可用的那一家**（`WP-GUEST-CALL-ATOMIC`）。
        # 原先这里写死一家 ⇒ 本机装的不是它就必然失败，那句错还把用户引向一个他没装的东西。
        agent_name, why_not = default_guest()
        if not agent_name:
            return json.dumps({"ok": False, "error": why_not, "asked": task[:280]},
                              ensure_ascii=False)
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

    # 归属与名字（`WP-EXTERNAL-THREAD-OWNER` §2.3）：**这一趟就把话说清**，不留给
    # 事后按标题猜。`owners` 留空 ⇒ 归现在派活的那个人（L1 派就是 L1，L2 派就是它自己）；
    # 也可以一次给几个人（用户 11-22：一条会话可以多归属），传字符串也认（内部包成数组）。
    from plobi.delegation.external_sessions import normalize_owners

    session_id = str(args.get("session_id") or "").strip()
    title = str(args.get("title") or "").strip()
    purpose = str(args.get("purpose") or "").strip()
    owners = normalize_owners(args.get("owners", args.get("owner"))) or ([reply_to] if reply_to else [])

    # 派活四要素（`WP-OFFLOAD-BRIEF`，11-22）：目标/产出/可用/边界。
    # 给了哪几样就拼进正文——缺的不留空标题，免得它照着空标题自己编边界出来；
    # 缺哪样也不拒，只是当场说出来（缺边界的活十有八九会停下来等一句授权）。
    from plobi.delegation.brief import check_brief, compose_brief, gaps_hint, rationale_hint

    deliverable = str(args.get("deliverable") or "").strip()
    tools_hint = str(args.get("tools") or "").strip()
    bounds = str(args.get("bounds") or "").strip()
    # 第五段是软项（`WP-DISPATCH-VERDICT`，11-25）：写清"为什么拆成这样、替它定死了哪几条"。
    # 只传结论不传决策，回来就是两份互相打架的成品；但缺了不拦，只把代价说一句。
    rationale = str(args.get("rationale") or "").strip()
    brief_gaps = check_brief(task, deliverable, tools_hint, bounds)
    if deliverable or tools_hint or bounds or rationale:
        task = compose_brief(task, deliverable, tools_hint, bounds, rationale)

    # 裁定 215：外包改成**派发前**预授权票，不再是事后补的追溯票。
    # 这一发是人（或域 Router）主动调用工具，等于已放行 ⇒ 直接 active；
    # 外部不可信事件走 webhook 的 observe 门，不经过这里。
    import dataclasses

    from plobi.delegation.tickets import ACTIVE, TicketLedger

    tickets = TicketLedger()
    ticket = tickets.open_ticket(
        parent_agent=reply_to or "l1",
        leg="offload",
        goal=task[:120],
        scope={
            "allowed_capabilities": ["offload"],
            "agent": agent_name,
            "cwd": str(cwd),
        },
        triggered_by="offload_tool",
        status=ACTIVE,
    )
    result = channel.run_task(task, cwd=cwd, agent=agent_name,
                              permission_mode=args.get("permission_mode") or None,
                              reply_to=reply_to,
                              session_id=session_id, owners=owners,
                              title=title, purpose=purpose)
    # 票号跟着结果走，不扩 RoundLedger.append 的签名（测试里的假 ledger 与老调用方
    # 都照旧）；真账的 append 会从 result.ticket_id 认出预授权票并替它落终态。
    ledger_row = dataclasses.replace(to_ledger_result(result), ticket_id=ticket.id)
    RoundLedger().append(ledger_row, brief=task)

    # ── 纯 ACP 客席：会话号落我们自己的台账（会话面才看得见）──────────────
    # 判据是「**这家有没有本地会话读法**」，不是「哪个信封给的号」——
    # 11-22 实测：qwen 走的是 CLI 腿，号从 `result.session_id` 回来（不带 `acp.` 前缀），
    # 按信封前缀判就把它漏掉了，会话面照样看不见。凡能力表里没有读法的家，
    # 号一回来就该进账；有读法的那 12 家本来就扫得到，别在这儿记重影。
    if ledger_row.external_session and capabilities(agent_name) is None:
        from plobi.delegation import acp_ledger

        acp_ledger.record(
            agent_name,
            str(ledger_row.external_session),
            project=str(cwd or ""),
            title=title or task[:60],
            purpose=purpose,
            # 回执文件与随包那句话分开存着（见 `OffloadResult.answer` 的注释）：
            # 会话面要说「刚才说到哪儿」，两处哪个有就取哪个。
            reply=str(getattr(result, "reply", "") or getattr(result, "answer", "") or ""),
            source=str(ledger_row.external_session_source or ""),
            resume_state=acp_ledger.RESUME_OK if ledger_row.resumed else acp_ledger.RESUME_UNKNOWN,
        )

    # ── 派完立刻记名（§2.3）──────────────────────────────────────────────
    # 拿到的会话号就写进 `_owners`，不等下一次扫。**有什么记什么**：owners / title /
    # purpose 全都空着就一条记录也不写——别把「没提」悄悄写成「判过了＝无主」。
    notes: list[str] = []
    if ledger_row.external_session and (owners or title or purpose):
        from plobi.delegation.external_sessions import assign_owner

        assigned = assign_owner(
            [{
                "vendor": agent_name,
                "id": ledger_row.external_session,
                "owners": list(owners),
                "title": title,
                "purpose": purpose,
            }],
            by=reply_to or "l1",
        )
        if not assigned.get("ok"):
            for item in assigned.get("rejected") or ():
                notes.append(f"账本没记上这条：{item.get('reason') or '（没给原因）'}")
        notes.extend(assigned.get("notes") or ())

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
    # 三态：`waiting` 与 `failed` 的下一步动作不一样——前者等人回一句，
    # 后者才是重试或换人。只报 `ok` 的话，这两种形状在回包里长得一模一样。
    payload["outcome"] = ledger_row.resolved_outcome
    if brief_gaps:
        # 缺项不是错误，是这一趟的**已知风险**：读的人知道它为什么可能半路停下来。
        payload["brief_gaps"] = brief_gaps
        payload["brief_hint"] = gaps_hint(brief_gaps)
    why_gap = rationale_hint(rationale)
    if why_gap:
        # 软项，和 brief_gaps 分开放：它不拦派活，也不该被读成"这趟要出问题"。
        payload["rationale_hint"] = why_gap
    if ledger_row.waiting_on:
        payload["waiting_on"] = ledger_row.waiting_on
    if ledger_row.external_session:
        # 有值才提这一格，没值不提、也不写"未知会话"糊弄（判据：空串不是可跳的标识）。
        payload["external_session"] = ledger_row.external_session
        payload["external_session_source"] = ledger_row.external_session_source
    # 归属与命名照实回报：报了没生效（比如名单里有人不在名册）要在这一屏说清，别让人以为记上了。
    if ledger_row.owners:
        payload["owners"] = list(ledger_row.owners)
    if ledger_row.title:
        payload["title"] = ledger_row.title
    if ledger_row.purpose:
        payload["purpose"] = ledger_row.purpose
    payload["resumed"] = bool(ledger_row.resumed)
    if session_id and ledger_row.external_session and not ledger_row.resumed:
        # 用户拍了要复用，这里却只能新开 ⇒ **必须说人话**，不许悄悄换个会话交差。
        payload["resume_note"] = (
            f"这条没能续上，是新开的（给你的是 {ledger_row.external_session}）："
            "这家走的是 CLI 腿，或者它不支持 session/load——`session_id` 只对 ACP 腿有效。"
        )
    if notes:
        payload["notes"] = notes
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
        "列出本机上可以外包过去的外部 AI 软件，**每家一行、一行里带齐你挑它要看的几件事**："
        "能不能用（`can_use` / `why_not`）、这一家是干什么的（`blurb`）、"
        "能不能派活（`offloadable`）、有哪几条派活腿（`legs`）、"
        "派完能不能接着那条会话说（`resumable`）、它的会话在这儿看不看得见（`session_source`）。"
        "在决定把一段活交出去之前先问它；can_use=false 的不要交。"
        "**它是唯一的名册**：别凭名字眼熟挑软件，也别自己猜某家支持什么。"
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

RUN_SCHEMA = {
    "name": "external_offload_run",
    "description": (
        "把**一整段能独立验收**的活交给本机的外部 AI 软件，"
        "你自己只负责拆活、验收、把结果拼回用户的话。"
        "**它不是默认选项**：动文件、跑命令、开网页、动系统这四类活默认给名手"
        "（`delegate_virtuoso` 派出的名手），只有**名手够不着**才交客席，够不着只有三种："
        "① 要用这家软件里的登录态或账号；② 要用这家模型独有的判断力或独家能力；"
        "③ 上次就是这家干的、这次要接着那条会话往下说。三条一条都不成立就别开客席——"
        "它派出去就飞了，我们只能等，且它一趟失败别派第二趟。（用户点名了某台软件时例外："
        "点了就交给点名那家。）"
        "别外包：一句话能答的、必须用本会话上下文的（继续上一轮、引用我们刚说的话）、"
        "以及目的地在 Plobi 自己那几个代码仓里的（会被拒）。"
        "交完必须回报：哪一段是它干的、产出落在哪个路径 + sha256、耗时、用量或『未知』。"
        "**同一个项目的老会话还能接着用**：scan 里看到合适的就把 `session_id` 一起交上去（"
        "续上就复用那条，不再开新的），顺手用 `title` / `purpose` 给它起名打标签。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description":
                "要外包的那一段：说清**做到什么程度算完**（目标）。"
                "能独立验收的活，把「产出/可用/边界」也一并写（见下面三个参数）——"
                "四样齐了它才不用半路来问你。"},
            "deliverable": {"type": "string", "description":
                "四要素之二：要什么形状的产出、落在哪儿（如「改好的 src/app.ts + 一句改动摘要」）。"
                "只说目标不说产出，回来的东西常常对不上你要的形状。"},
            "tools": {"type": "string", "description":
                "四要素之三：它可以从哪儿拿工具、命令、资料（如「只准用仓库里的 pnpm 脚本」）。"
                "留空的活它会自己找路，可能绕远。"},
            "bounds": {"type": "string", "description":
                "四要素之四：不许做什么（改别的文件、装东西、动远端、花钱）。"
                "**这一项最省事也最值钱**：写清边界，它就不会半路停下来等你授权。"},
            "rationale": {"type": "string", "description":
                "为什么拆成这样、把哪几条替它定死了（如「只取 2025 之后的数据，因为旧的那批口径改过」）。"
                "**动作本身带着一串你没说出口的决定**：为什么切两半、先做哪半、哪儿不许自己发挥。"
                "只传结论不传这些，两个手下各自按不同假设动手，回来是两份互相打架的成品，还得你去合。"
                "不写不拦，只是这一趟回来你可能得从成品里反推它当初怎么想的。"},
            "cwd": {"type": "string", "description": "它干活的项目目录（产出就落这里）。"},
            "agent": {"type": "string", "description":
                "交给谁；名字只认 external_offload_status 返回的那份本机清单，别照下面任何一句示例猜。"
                "留空＝用默认那家。"},
            "session_id": {"type": "string", "description":
                "要接着用的那条外部会话号（`external_sessions_scan` 里的 `id`）。"
                "留空＝新开一条。只有走 ACP 那一路的软件能续；续不上会如实告诉你『这条没能续上，是新开的』。"},
            "owners": {"type": "array", "items": {"type": "string"}, "description":
                "这条会话归谁，**可多个**（写名册上的名字，如 [\"写稿助手\", \"建模助手\"]）："
                "一条会话的活横跨两个 L2 时就都写上。留空＝归现在派活的人；"
                "要清空归属（回到待判）用 external_session_assign 传 []。"},
            "title": {"type": "string", "description":
                "给这条会话起个名，建议成「[<你的名字>] <这件事>」的样子（软约束，不必照抄）。"},
            "purpose": {"type": "string", "description":
                "这条会话在干什么，一句话的标签（如「建模仿真」「代码改动」「资料调研」；软约束，不是词表，照实写）。"},
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
        "列出本机上各家 AI 软件的会话（" + vendor_list_text() + "），并带上**已经自动算好的**相关判断："
        "`related=true`（项目目录或标题点到你）／`null`（还没判断）／`false`（你标过无关）。"
        "用户问「有哪些跟我有关的对话」、或你要决定跟哪条继续谈之前，先扫这一遍；"
        "**判不准的别猜**，看完标题与项目再决定，标完用 external_sessions_mark 记下来。"
        "要给别人（或某条无主的会话）重新定归属时，用 `scope=\"all\"` 把**全量候选**连"
        "`owners`（名单）/ `purpose` 一起取回来，再用 external_session_assign 改。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "scope": {"type": "string", "enum": ["mine", "all"], "description":
                "mine（默认）＝只看跟我有关的这一屏；all＝全量候选，每条带 owners / purpose，"
                "用来一条条判归属（要改归属先扫这个）。"},
        },
        "required": [],
    },
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
                        "vendor": {"type": "string", "description": "哪家软件：" + vendor_list_text() + "。"},
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



RENAME_SCHEMA = {
    "name": "external_sessions_rename",
    "description": (
        "给本机某一条外部会话改名字。**能写回的家会真写进它自己的标题位**（CodeBuddy／Kiro／"
        "Marvis／agy／OpenCode／Kilo／Copilot）——改完在对方软件里也看得见；写不了的家"
        "（Gemini／Cline／Cursor／Qoder 那一族，标题就是它的第一句人话）只改 Plobi 这边，"
        "回话里的 title_note 会说清是哪一种。对方软件正开着时会拒改（它会拿旧名字盖回去），"
        "先关掉再改。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "vendor": {"type": "string", "description": "哪家软件：" + vendor_list_text() + "。"},
            "id": {"type": "string", "description": "scan 回来的会话号。"},
            "title": {"type": "string", "description": "新名字，一句话。"},
        },
        "required": ["vendor", "id", "title"],
    },
}

ARCHIVE_SCHEMA = {
    "name": "external_sessions_archive",
    "description": "把一条外部会话收进「已归档」（还在，只是从主列表折起来）；传 archived=false 放回来。",
    "parameters": {
        "type": "object",
        "properties": {
            "vendor": {"type": "string", "description": "哪家软件：" + vendor_list_text() + "。"},
            "id": {"type": "string", "description": "scan 回来的会话号。"},
            "archived": {"type": "boolean", "description": "true 收起来，false 放回来。默认 true。"},
        },
        "required": ["vendor", "id"],
    },
}

DELETE_SCHEMA = {
    "name": "external_sessions_delete",
    "description": (
        "删掉一条外部会话。**不是真删**：文件搬到本机回收站（可恢复）；Marvis/Cursor 那两家只隐藏、"
        "不动它们的库。动手前先跟用户确认是哪一条。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "vendor": {"type": "string", "description": "哪家软件：" + vendor_list_text() + "。"},
            "id": {"type": "string", "description": "scan 回来的会话号。"},
        },
        "required": ["vendor", "id"],
    },
}


# ── 通用原子工具（11-04 用户拍的形状）────────────────────────────────────
#
# 六枚会话工具共享同一对定位参数 `vendor + id`，模型不必为每家记一套工具名。
# 各家差异全在 `external_capabilities.py`：不支持的动作当场报错，绝不静默成功。
# 「派新活」的 status/run 是另一组动词，不并进来——它们的参数是调用参数，不是会话操作。

SEND_SCHEMA = {
    "name": "external_session_send",
    "description": (
        "往一条外部 AI 软件的会话里说一句话（像人一样接着聊）。"
        "先 external_sessions_scan 看 capabilities：只有带 `send` 的那家能说话，"
        "带 `attachments` 的才收附件。不支持就别试，工具会当场拒。"
        "回包里带 prefs（这条会话记下的模型/权限预设）与 prefs_applied——"
        "今天没有一家能在续接时自动应用预设，看到 false 是实话，不是失败。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "vendor": {"type": "string", "description": "哪家软件：" + vendor_list_text() + "。"},
            "id": {"type": "string", "description": "scan 回来的会话号。"},
            "text": {"type": "string", "description": "要说的话。"},
            "attachments": {
                "type": "array",
                "items": {"type": "string"},
                "description": "附件路径。今天没有一家支持，给了会当场报错而不是悄悄丢掉。",
            },
        },
        "required": ["vendor", "id", "text"],
    },
}

MODEL_SCHEMA = {
    "name": "external_session_model",
    "description": (
        "给一条外部会话记下「下次从 Plobi 续接时想用的模型」。"
        "**这是 Plobi 侧的会话预设，不是去改对方软件里那条会话**——对方多数不允许改已创建会话的模型。"
        "传空串清掉预设。能不能在续接时自动带上，看 scan 里的 `apply_on_send`。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "vendor": {"type": "string", "description": "哪家软件：" + vendor_list_text() + "。"},
            "id": {"type": "string", "description": "scan 回来的会话号。"},
            "model": {"type": "string", "description": "模型名；传空串＝清掉预设，回到那家自己的默认。"},
        },
        "required": ["vendor", "id", "model"],
    },
}

PERMISSION_SCHEMA = {
    "name": "external_session_permission",
    "description": (
        "给一条外部会话记下「下次续接时想用的权限档」。档位只认归一化的三档 "
        + permission_levels_text()
        + "，各家的真名由能力表翻译，不用你猜。同样是 Plobi 侧预设，不是改对方软件。"
        "传空串清掉预设。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "vendor": {"type": "string", "description": "哪家软件：" + vendor_list_text() + "。"},
            "id": {"type": "string", "description": "scan 回来的会话号。"},
            "level": {
                "type": "string",
                "enum": ["", *PERMISSION_LEVELS],
                "description": "归一化档位；" + permission_levels_text() + "。传空串＝清掉预设。",
            },
        },
        "required": ["vendor", "id", "level"],
    },
}


def _handle_rename(args: Dict[str, Any], **_kw: Any) -> str:
    agent = _agent_identity()
    if not agent:
        return json.dumps({"ok": False, "error": "这个会话没有 Agent 名，记不了账"}, ensure_ascii=False)
    from plobi.delegation.external_sessions import update_for_agent

    result = update_for_agent(agent, str(args.get("vendor") or ""), str(args.get("id") or ""),
                              {"title": str(args.get("title") or "")})

    return json.dumps(result, ensure_ascii=False)


def _handle_archive(args: Dict[str, Any], **_kw: Any) -> str:
    agent = _agent_identity()
    if not agent:
        return json.dumps({"ok": False, "error": "这个会话没有 Agent 名，记不了账"}, ensure_ascii=False)
    from plobi.delegation.external_sessions import update_for_agent

    archived = args.get("archived", True)
    result = update_for_agent(agent, str(args.get("vendor") or ""), str(args.get("id") or ""),
                              {"archived": bool(archived)})

    return json.dumps(result, ensure_ascii=False)


def _handle_delete(args: Dict[str, Any], **_kw: Any) -> str:
    agent = _agent_identity()
    if not agent:
        return json.dumps({"ok": False, "error": "这个会话没有 Agent 名，记不了账"}, ensure_ascii=False)
    from plobi.delegation.external_sessions import delete_for_agent

    result = delete_for_agent(agent, str(args.get("vendor") or ""), str(args.get("id") or ""))

    return json.dumps(result, ensure_ascii=False)


def _agent_identity() -> str:
    from gateway.session_context import get_session_env

    return (get_session_env("PLOBI_AGENT_ID") or "").strip()


def _handle_scan(args: Dict[str, Any], **_kw: Any) -> str:
    agent = _agent_identity()
    scope = str(args.get("scope") or "").strip().lower() or "mine"
    if scope not in ("mine", "all"):
        return json.dumps({"ok": False, "error": "scope 只认 mine / all"}, ensure_ascii=False)
    # `all` 这一屏是给 **L1（Principal）判归属**用的，而 L1 这一行**本来就没有 Agent 名**，
    # 所以这里**不限制谁调**（§2.4）；`mine` 才需要知道「我」是谁。
    if scope == "mine" and not agent:
        return json.dumps({"ok": False, "error": "这个会话没有 Agent 名，判不了归属"}, ensure_ascii=False)

    from plobi.delegation.external_sessions import sessions_for_agent, sessions_overview

    payload = sessions_overview(agent) if scope == "all" else sessions_for_agent(agent)
    rows = [
        {
            "id": item["id"],
            "capabilities": (capabilities(item["vendor"]).as_list() if capabilities(item["vendor"]) else []),
            "owners": list(item.get("owners") or ()),
            "project": item.get("project") or "",
            "purpose": item.get("purpose") or "",
            "related": item.get("related"),
            "related_source": item.get("related_source"),
            "title": item.get("title") or "",
            "updated_at": item.get("updated_at") or "",
            "vendor": item["vendor"],
        }
        for item in payload["sessions"]
    ]
    for row, item in zip(rows, payload["sessions"]):
        if item.get("owner_note"):
            row["owner_note"] = item["owner_note"]
    # 能力位也挂在 source 上：一家软件整体能做什么，不必逐行翻。
    sources = []
    for source in payload.get("sources", []):
        spec = capabilities(source.get("vendor"))
        sources.append({**source, "capabilities": spec.as_list() if spec else []})
    counts = {"related": 0, "unknown": 0, "unrelated": 0}
    for row in rows:
        counts["related" if row["related"] is True else "unrelated" if row["related"] is False else "unknown"] += 1

    return json.dumps({"counts": counts, "ok": True, "scope": scope,
                       "sessions": rows[:80 if scope == "mine" else 200], "sources": sources},
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


def _session_target(args: Dict[str, Any]) -> tuple[str, str]:
    return str(args.get("vendor") or "").strip().lower(), str(args.get("id") or "").strip()


def _handle_send(args: Dict[str, Any], **_kw: Any) -> str:
    agent = _agent_identity()
    if not agent:
        return json.dumps({"ok": False, "error": "这个会话没有 Agent 名，判不了归属"}, ensure_ascii=False)

    vendor, session_id = _session_target(args)
    if not supports(vendor, "send"):
        # 能力表没写这一家能发，但它要是我们派过活的纯 ACP 客席（台账上有这号），
        # 这条路照样走得通 —— 挡在这儿等于把 29 家刚接上的续接又掐回去。
        from plobi.delegation import acp_ledger

        if acp_ledger.find(vendor, session_id) is None:
            note = unsupported_note(vendor, "send")
            if capabilities(vendor) is None and vendor in _vendor_facts():
                # 它在名册上（派得动活），只是能力表里没登记 `send` 这一腿：
                # 纯 ACP 客席的会话号只能从我们自己派过的那趟里来 ⇒ 说清怎么才有的接，
                # 别拿"不认识这家软件"去糊弄一句它明明能接活的话。
                note = (f"{vendor} 还没从 Plobi 派过活：这类纯 ACP 客席要先派一趟、"
                        f"拿到会话号，才接得上后面的话")
            return json.dumps({"ok": False, "error": note}, ensure_ascii=False)
    if args.get("attachments") and not supports(vendor, "attachments"):
        # 附件不能悄悄丢：模型以为带上了、对方其实什么都没收到，是最坏的一种假成功。
        return json.dumps({"ok": False, "error": unsupported_note(vendor, "attachments")}, ensure_ascii=False)

    from plobi.delegation.external_sessions import prefs_for_agent, send_for_agent

    result = send_for_agent(agent, vendor, session_id, str(args.get("text") or ""))
    if result.get("ok"):
        spec = capabilities(vendor)
        data = result.setdefault("data", {})
        data["prefs"] = prefs_for_agent(agent, vendor, session_id)
        data["prefs_applied"] = bool(spec and spec.apply_on_send)

    return json.dumps(result, ensure_ascii=False)


def _handle_model(args: Dict[str, Any], **_kw: Any) -> str:
    agent = _agent_identity()
    if not agent:
        return json.dumps({"ok": False, "error": "这个会话没有 Agent 名，记不了预设"}, ensure_ascii=False)

    vendor, session_id = _session_target(args)
    from plobi.delegation.external_sessions import set_prefs_for_agent

    result = set_prefs_for_agent(agent, vendor, session_id, model=str(args.get("model") or "").strip())

    return json.dumps(result, ensure_ascii=False)


def _handle_permission(args: Dict[str, Any], **_kw: Any) -> str:
    agent = _agent_identity()
    if not agent:
        return json.dumps({"ok": False, "error": "这个会话没有 Agent 名，记不了预设"}, ensure_ascii=False)

    vendor, session_id = _session_target(args)
    level = str(args.get("level") or "").strip().lower()
    if level and level not in PERMISSION_LEVELS:
        return json.dumps({"ok": False, "error": f"权限档只认 {permission_levels_text()}"}, ensure_ascii=False)

    from plobi.delegation.external_sessions import set_prefs_for_agent

    result = set_prefs_for_agent(agent, vendor, session_id, permission_level=level)
    if result.get("ok"):
        # 把归一化档对应的这家真名一起交回去，省得模型下次拿别家的档名来凑。
        result["data"]["level"] = level or None
        result["data"]["vendor_flag"] = permission_alias(vendor, level) if level else None

    return json.dumps(result, ensure_ascii=False)


ASSIGN_SCHEMA = {
    "name": "external_session_assign",
    "description": (
        "改外部会话的**归属名单**（归哪几个 Agent），顺手能改名和打语义标签。"
        "归属由你自己判：**判过的说了算**，之后所有 Agent 看这一条的 related 都按它算——"
        "在名单里的人 saw=True，不在的人 saw=False，名单空着＝判过了但无主（related=null）。"
        "**一条会话可以多归属**（前半程聊建模、后半程说写稿，就两个 L2 都写上）；"
        "`owners` 传 [] ＝清空归属、回到待判，只给一个人就传 [\"某人\"]（传字符串也认）。"
        "改之前先 `external_sessions_scan(scope=\"all\")` 把候选连同 `owners` / `purpose` 取回来，"
        "照着标题、项目、上线事由判，别猜。"
        "**软约束**（只提示不拦截）：名字建议起成「[<你的名字>] <这件事>」，别超过 40 字；"
        "语义标签照实写一句话，词表只是建议（建模仿真／代码改动／资料调研／文档写作／数据处理／排障诊断／其他）。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "vendor": {"type": "string", "description": "哪家软件：" + vendor_list_text() + "。"},
                        "id": {"type": "string", "description": "scan 回来的会话号。"},
                        "owners": {"type": "array", "items": {"type": "string"}, "description":
                            "归给哪几个 Agent（名册上的名字，可多个：一条会话能多归属）。"
                            "传 [] ＝清空归属（回到待判）；传单个名字的字符串也认。"},
                        "title": {"type": "string", "description":
                            "顺手改名（只在你这里生效）。不建议超过 40 字，别改就整格省掉它。"},
                        "purpose": {"type": "string", "description":
                            "顺手打语义标签，一句话。别改就整格省掉它。"},
                    },
                    "required": ["vendor", "id"],
                },
            },
        },
        "required": ["items"],
    },
}


def _handle_assign(args: Dict[str, Any], **_kw: Any) -> str:
    """改归属（§2.4）：`_owners` 里那一格，判了就算，不再让代码按标题猜。"""
    items = args.get("items")
    if not isinstance(items, list) or not items:
        return json.dumps({"ok": False, "error": "items 要给一个数组，一条会话一项"}, ensure_ascii=False)

    from plobi.delegation.external_sessions import assign_owner

    agent = _agent_identity()
    result = assign_owner([item for item in items if isinstance(item, dict)], by=agent or "l1")
    if not result.get("ok"):
        # 逐条不成的摆出来（别只回一句"失败了"）。
        kept = result.get("error") or "；".join(
            f"{item.get('vendor')}:{item.get('id')} → {item.get('reason')}"
            for item in (result.get("rejected") or ())
        )
        result["error"] = kept

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

registry.register(
    name="external_session_assign",
    toolset=TOOLSET,
    schema=ASSIGN_SCHEMA,
    handler=_handle_assign,
    check_fn=_check_drivable,
    emoji="🗂",
)

registry.register(
    name="external_sessions_rename",
    toolset=TOOLSET,
    schema=RENAME_SCHEMA,
    handler=_handle_rename,
    check_fn=_check_drivable,
    emoji="🗂",
)

registry.register(
    name="external_sessions_archive",
    toolset=TOOLSET,
    schema=ARCHIVE_SCHEMA,
    handler=_handle_archive,
    check_fn=_check_drivable,
    emoji="🗂",
)

registry.register(
    name="external_sessions_delete",
    toolset=TOOLSET,
    schema=DELETE_SCHEMA,
    handler=_handle_delete,
    check_fn=_check_drivable,
    emoji="🗂",
)

registry.register(
    name="external_session_send",
    toolset=TOOLSET,
    schema=SEND_SCHEMA,
    handler=_handle_send,
    check_fn=_check_drivable,
    emoji="💬",
)

registry.register(
    name="external_session_model",
    toolset=TOOLSET,
    schema=MODEL_SCHEMA,
    handler=_handle_model,
    check_fn=_check_drivable,
    emoji="🗂",
)

registry.register(
    name="external_session_permission",
    toolset=TOOLSET,
    schema=PERMISSION_SCHEMA,
    handler=_handle_permission,
    check_fn=_check_drivable,
    emoji="🗂",
)
