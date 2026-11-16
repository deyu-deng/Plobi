"""设置 → 外包（Offload）那一屏的取数。

**它与「Providers / 模型」那一屏是两件事，不许混**：

* Providers 那屏借的是**外部软件的订阅/API，拿来当模型用**（一条推理腿）；
* 这一屏管的是**把一段活外包给那家软件、让它在自己界面里干**（一个人、一趟活、一条它自己的会话）。

三条设计约束（都在这台机器上实测过）：

1. **首屏不跑探针。** `qodercli`/`codebuddy` 是 shim，超时只杀得掉外壳、孙进程还攥着管道，
   实测一次 `--list-models` 能把取数拖过两分钟。登录态改用**上一趟真跑的证据**
   （台账里那句 `Not logged in · Please run /login`），零子进程、瞬时出数。
2. **读不出的不许写成"不能"**：没有证据时 `logged_in.value = None`，`source = "unknown"`。
3. **能不能用只有一处判据**（`availability()`）；这一层只搬字节，不自己判第二遍。
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Optional

from agent import external_offload as channel
from plobi.delegation.offload import ExternalAgent, availability, total_usd
from plobi.delegation.offload_contract import cached_answers

#: 这一屏的读数缓存 120s（探针不进读路径，见文件头第 1 条）。
PROBE_TTL_SECONDS = 120.0

#: 「它自己说没登录」的原文标记（小写）。只在**台账已有的一趟**上匹配，不做探针。
_ABSENT_MARKERS = ("not logged in", "please run /login", "login required",
                   "not authenticated", "no api key")

_CACHE: dict[str, Any] = {"at": 0.0, "payload": None}

#: 后台那一轮 ACP 握手的进度（``running`` 只是防重复起）。
_SWEEP: dict[str, Any] = {"running": False}


def _acp_pending(vendors: list[str]) -> list[str]:
    """还没探过的那几家：装上了、契约表里有 ACP 入口、缓存里还没有答案。"""
    from plobi.delegation.offload_contract import OFFLOAD_CONTRACT, cached_answers

    have = cached_answers()
    out: list[str] = []
    for name in vendors:
        spec = OFFLOAD_CONTRACT.get(name)
        if spec is None or not spec.acp_args:
            continue
        if name in have:
            continue
        out.append(name)
    return out


def maybe_start_acp_sweep() -> list[str]:
    """后台起一轮 ACP 握手（**路由那一层调它**：用户把那一屏打开就是那个信号）。

    起完了返回本轮在探的那几家（空 = 没什么要探的，或已经在跑）。

    取数那一层保持纯的：它只报「还有几家没探」，**不自己起线程**——
    否则每个读这一屏的调用方（包括测试）都会莫名其妙起一堆 CLI 进程。
    """
    from plobi.delegation.offload_contract import OFFLOAD_CONTRACT, cached_answers

    have = cached_answers()
    pending = [
        name for name, spec in OFFLOAD_CONTRACT.items()
        if spec.acp_args and name not in have
    ]
    if not pending:
        return []
    if _SWEEP.get("running"):
        return pending
    _start_acp_sweep(pending)
    return pending


def _start_acp_sweep(vendors: list[str]) -> None:
    """后台跑一轮 ACP 握手——用户把这一屏打开就够了，不必再点一次「再读一次」。

    **首屏不跑探针**那一条管的是「不许在读路径上同步跑」；这里是后台线程：
    跑完把答案落进契约表的缓存，再把这一屏的结果缓存丢掉，下一次读数就是新的
    （那一格自己就从「＋」长成开关）。
    """
    import threading

    from plobi.delegation.offload_contract import probe_all

    def _run() -> None:
        try:
            probe_all(vendors, max_workers=4, deadline_seconds=90.0)
        finally:
            _SWEEP["running"] = False
            forget_rows_cache()

    _SWEEP["running"] = True
    threading.Thread(target=_run, name="plobi-offload-acp-sweep", daemon=True).start()


def _which(binary: str) -> str:
    """找得到它的命令吗（找得到才算装上了；Windows 上 ``which`` 会按 PATHEXT 找 ``.cmd``）。"""
    import shutil

    if not binary:
        return ""
    try:
        return shutil.which(binary) or ""
    except OSError:
        return ""


def _version_of(binary: str, scrub: tuple[str, ...]) -> str:
    """`--version` 那一格：读不到就空串（空是读数，不拿占位充）。"""
    import os
    import subprocess
    import tempfile

    env = dict(os.environ)
    for key in scrub:
        env.pop(key, None)
    try:
        done = subprocess.run([binary, "--version"], cwd=tempfile.gettempdir(), env=env,
                              stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, errors="replace", timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    text = ((done.stdout or "") + (done.stderr or "")).strip().splitlines()
    return text[0][:80] if text else ""


def _rows_by_agent() -> dict[str, list[dict[str, Any]]]:
    from plobi.delegation.offload import RoundLedger

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in RoundLedger().rows():
        grouped.setdefault(str(row.get("agent") or ""), []).append(row)
    for name in grouped:
        grouped[name].sort(key=lambda r: str(r.get("ts") or ""), reverse=True)
    return grouped


def _login_evidence(last: dict[str, Any], local: Optional[bool] = None,
                    acp: Any = None) -> dict[str, Any]:
    """登录态：**厂商亲口说的**优先 —— 先是「ACP 腿此刻的答复」，再是「上一趟真跑的证据」。

    11-14 改：ACP 那一腿（``plobi/delegation/offload_contract.py``）排在**最前**。
    它是**此刻**的答复：它自己说「没登录」就比三天前那一趟的旧话准；它自己开了会话，
    就说明此刻登录着。**它探不到（没回话／拒的原因不是登录）时不算数** —— 往下落到旧的两档。

    11-08 21:2x 现读：这一格原来**只读「上一趟真跑的证据」** ⇒ 刚装上、还没派过活的那几家
    （Copilot CLI、Kilo Code CLI）永远是「配置好了」⇒ 主人看见一个开关，以为能用，
    一点上去才撞「它没登录」——**屏上写了假话**（而点「+」拉起登录的入口压根不出现）。

    三条证据按可信度排队：

    1. **厂商自己说的话** —— 活的 ACP 答复（``session/new`` 给不给会话）、上一趟它自己说的
       「Not logged in · Please run /login」；
    2. **本地痕迹**：登录后才会落盘的那个文件（oauth token / creds json）、凭据库条目、
       登录用的环境变量 ⇒ 能判 True／False。**但那只是痕迹**：屏上写的是「凭据痕迹在，
       但那只是提示」，不写成「还保持登录」（不许把「有文件」说成「登上了」）；
    3. 别的（目录里有过痕迹、压根没证据）⇒ **探不到（None）**，不判成不能。
    """
    if acp is not None and getattr(acp, "value", None) is not None:
        return {
            "value": bool(acp.value),
            "source": str(getattr(acp, "source", "acp") or "acp"),
            "detail": str(getattr(acp, "detail", "") or ""),
        }
    if last:
        haystack = " ".join(
            str(last.get(key) or "") for key in ("answer_excerpt", "reply", "failure")
        ).lower()
        if any(marker in haystack for marker in _ABSENT_MARKERS):
            return {
                "value": False,
                "source": "last-run",
                "detail": "上一趟它自己说没登录（原文留在那一行的 answer_excerpt 里）——登录要本人做一次。",
            }
        if str(last.get("reply") or "").strip() or str(last.get("answer_excerpt") or "").strip():
            return {"value": True, "source": "last-run", "detail": ""}
    if local is True:
        return {"value": True, "source": "local", "detail": "本地就能读到它的登录痕迹"}
    if local is False:
        return {"value": False, "source": "local", "detail": "本地读到它没登录"}
    if not last:
        return {"value": None, "source": "unknown", "detail": "还没派过活，也没有本地证据"}
    return {"value": None, "source": "last-run", "detail": "上一趟没带回话，判不出登录态"}


def _install_payload(vendor: str) -> Optional[dict[str, Any]]:
    """这一家**怎么装**（用户 11-15 13:0x：全新电脑上第一次点「+」时给的就是它）。

    表里没有这一族的 ⇒ 返回 ``None``（屏上照旧说那句泛泛的话，**不编一条命令**）。
    """
    from agent.vendor_registry import INSTALL_SPECS

    spec = INSTALL_SPECS.get(vendor)
    if spec is None:
        return None
    return {
        "command": spec.command,
        "package": spec.package,
        "homepage": spec.homepage,
        "binary": spec.binary,
        "note": spec.note,
        "verified": spec.verified,
    }


def _selected_account(vendor: str) -> str:
    """主人为这一族选的身份标识（空 = 没选 ⇒ 厂商用它自己当前登录的那个）。"""
    try:
        from plobi.delegation.external_accounts import selected_account

        return selected_account(vendor)
    except Exception:  # noqa: BLE001 - 读不到就当没选
        return ""


def external_agent_rows(*, refresh: bool = False) -> dict[str, Any]:
    """一家外部软件一行。**能不能用**只问 `availability()`；**为什么**原样带出。"""
    from tools.offload_tools import agent_roster

    now = time.time()
    cached = _CACHE.get("payload")
    if cached and not refresh and now - float(_CACHE.get("at") or 0.0) < PROBE_TTL_SECONDS:
        return cached

    roster = {agent.name: agent for agent in agent_roster()}
    grouped = _rows_by_agent()
    # ACP 那一腿**已经探到**的答案（缓存里的）。**读路径上不许跑握手**——首屏要在秒级出数，
    # 探针在后台跑（见 `_start_acp_sweep`），落地后这一屏自己就更新。
    acp_answers = cached_answers()
    today = datetime.now(timezone.utc).date().isoformat()
    rows: list[dict[str, Any]] = []

    for name in channel.EXTERNAL_AGENTS:
        base = roster.get(name)
        binary = channel.resolve_command(name)
        spec = channel.agent_spec(name)
        mine = grouped.get(name, [])
        last = mine[0] if mine else {}
        # 本地证据（探针/凭据痕迹）优先；`base.logged_in` 就是 `availability()` 用的那一份，
        # 两处口径不许分家（裁定 94）。
        login = _login_evidence(last, getattr(base, "logged_in", None), acp_answers.get(name))

        capabilities = base.capabilities if base is not None else {}
        decision = ExternalAgent(
            name=name,
            installed=bool(binary),
            logged_in=login["value"],
            channel_ready=bool(binary),
            permission_flag=base.permission_flag if base is not None else "",
            capabilities=capabilities,
            allowed=bool(getattr(base, "allowed", True)),
        )
        can_use, why_not = availability(decision)
        day_rows = [r for r in mine if str(r.get("ts") or "")[:10] == today]
        amount, cost_status = total_usd(day_rows)

        resume_argv: tuple[str, ...] = ()
        try:  # 懒导入：白名单在控制台那边，运行期取，避免 import 环
            from plobi.console.router import OFFLOAD_RESUME_ARGV

            resume_argv = OFFLOAD_RESUME_ARGV.get(name, ())
        except Exception:  # noqa: BLE001 - 取不到就当"这一家没登记回跳口"
            resume_argv = ()

        rows.append({
            "name": name,
            "installed": bool(binary),
            "binary": binary,
            "version": _version_of(binary, getattr(spec, "scrub_env", ()) or ()) if binary else "",
            "logged_in": login,
            "login_hint": bool(base.logged_in) if base is not None else False,
            "permission_flag": base.permission_flag if base is not None else "",
            "permission_modes": list(getattr(spec, "permission_modes", ()) or ()),
            "capabilities": {key: bool(value) for key, value in (capabilities or {}).items()},
            "can_use": can_use,
            # 主人这一格的开关（设置 → 外包）：关掉只是不派活，卡面照常显示这一家。
            "enabled": bool(getattr(base, "allowed", True)),
            "why_not": why_not,
            "resume_supported": bool(resume_argv),
            # 这一家**能不能派活**（真源表那一行说了算）＋**怎么装**（新用户的第一扇门）。
            "dispatch_ready": True,
            "install": _install_payload(name),
            # 多账号（11-16）：这一族下面有哪几个身份、主人选了哪一个。
            "accounts": channel.read_accounts(name),
            "selected_account": _selected_account(name),
            "today": {
                "rounds": len(day_rows),
                "total_usd": amount,
                "cost_status": cost_status,
                "last_ts": str(last.get("ts") or ""),
            },
            "last_round": {
                "ts": str(last.get("ts") or ""),
                "cwd": str(last.get("cwd") or ""),
                "duration_seconds": last.get("duration_seconds"),
                "reply_status": str(last.get("reply_status") or ""),
                "answer_excerpt": str(last.get("answer_excerpt") or ""),
                "external_session": str(last.get("external_session") or ""),
            } if last else None,
        })

    # ── 还没接派活腿的那几家（用户 11-15 13:0x）────────────────────────
    #
    # 假设用户装的是一台**全新的电脑**，只装了 Plobi ⇒ 屏上要能看见这些家，
    # 点「+」时给出**它们各自**的下载引导（装好之后那一格仍然是「＋」，
    # 并照实说「还没接派活腿」——派活腿本刀没实测过，不许给一颗点不动的开关）。
    from agent.vendor_registry import INSTALL_SPECS, VENDOR_ROWS

    for row in VENDOR_ROWS:
        if row.offloadable or row.id not in INSTALL_SPECS:
            continue
        spec = INSTALL_SPECS[row.id]
        binary = _which(spec.binary or row.binary_name)
        rows.append({
            "name": row.id,
            "installed": bool(binary),
            "binary": binary,
            "version": "",
            "logged_in": {
                "value": None,
                "source": "unknown",
                "detail": "这一家还没接派活腿：登录判据没实测过 ⇒ 探不到，不判没登录。",
            },
            "login_hint": False,
            "permission_flag": "",
            "permission_modes": [],
            "capabilities": {},
            "can_use": False,
            "enabled": True,
            "why_not": "这一家还没接派活腿",
            "resume_supported": False,
            "dispatch_ready": False,
            "install": _install_payload(row.id),
            # 多账号（11-16）：同一套规矩，与能不能派活无关。
            "accounts": channel.read_accounts(row.id),
            "selected_account": _selected_account(row.id),
            "today": {
                "rounds": 0,
                "total_usd": None,
                "cost_status": "",
                "last_ts": "",
            },
            "last_round": None,
        })

    # 还有几家没探（答案落地后那一格自己就从「＋」长成开关）。
    # **取数这一层不起线程**（它得是纯的）：起手在路由那一层（见 `maybe_start_acp_sweep`）。
    pending = _acp_pending([row["name"] for row in rows if row.get("installed")])

    payload = {
        "agents": rows,
        "probed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "probe_ttl_seconds": PROBE_TTL_SECONDS,
        "acp_probe_pending": len(pending),
        "note": ("这一屏管的是「把活外包给那家软件」；把它的订阅/API 当模型用，在 Providers 那一屏，"
                 "两件事不共用一张表。登录态问的是厂商自己（ACP 那一腿），首屏不跑探针。"),
    }
    _CACHE["at"] = now
    _CACHE["payload"] = payload
    return payload


def forget_rows_cache() -> None:
    """开关改了：下一次取数别再拿缓存里的旧 `enabled` 糊弄人（探针不动，只丢结果）。"""
    _CACHE.clear()


def _probe_login_state(agent: str) -> Any:
    """问那家软件自己（ACP 那一腿）：它此刻登录着没有。

    探不到（没回话／这一家没法用 ACP 问）⇒ 返回 ``None``，**不挡主人**：
    调用方照旧走登录命令。探针不许把屏带崩。
    """
    try:
        from plobi.delegation.offload_contract import probe_acp_login

        return probe_acp_login(agent, use_cache=True)
    except Exception:  # noqa: BLE001 — 探不到不是错误状态
        return None


def start_login(agent: str) -> dict[str, Any]:
    """把这一家**官方那条登录命令**拉起来（用户 11-08 19:4x：「点 + 看看会不会自动拉起登录流程」）。

    这一层只做三件事，如实分三种结果：

    * ``started=True`` —— 命令拉起来了。**登录本身仍要本人做**（copilot 那条是浏览器 OAuth，
      人点一下同意才算登录；本进程不代点、不代填）。
    * ``started=False`` + 一句人话 —— 这一家**没有能一键拉起的登录流程**（要终端交互的
      比如 kilo 的 ``auth login`` 要人挑 provider），或本机压根没装它。**不假装拉起了。**
    * 命令只来自名册那张表（``login_argv``），**不许把用户输入拼进命令行**。
    """
    spec = channel.agent_spec(agent)
    if spec is None:
        return {"started": False, "reason": "名册里没有这一家。"}
    if not spec.login_argv:
        return {"started": False, "reason": "这一家没有能一键拉起的登录流程：去它自己的应用里登录。"}
    binary = channel.resolve_command(agent)
    if not binary:
        return {"started": False, "reason": f"本机没有 {agent}：先装上再登录。"}

    # **先问它自己**（ACP 那一腿）：已经登录着就别再拉一遍登录命令
    # （浏览器 OAuth 那一下要人点同意，白跑一次只是添乱）。
    answer = _probe_login_state(agent)
    if answer is not None and getattr(answer, "value", None) is True:
        return {"started": False, "reason": "它已经是登录状态：它自己的 ACP 腿刚给了会话。"}

    import os
    import subprocess
    import tempfile
    from pathlib import Path

    argv = channel.spawn_argv([binary, *spec.login_argv])
    log_dir = Path(tempfile.gettempdir()) / "plobi-offload-login"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{agent}.log"
        with open(log_path, "w", encoding="utf-8") as handle:
            subprocess.Popen(  # noqa: S603 — argv 是名册那张表里的固定值
                argv, stdin=subprocess.DEVNULL, stdout=handle, stderr=subprocess.STDOUT,
                cwd=tempfile.gettempdir(), env=dict(os.environ),
            )
    except OSError as exc:
        return {"started": False, "reason": f"起不来：{exc}"}
    return {"started": True, "command": " ".join(argv[1:]), "log": str(log_path)}
