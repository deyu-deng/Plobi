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

#: 这一屏的读数缓存 120s（探针不进读路径，见文件头第 1 条）。
PROBE_TTL_SECONDS = 120.0

#: 「它自己说没登录」的原文标记（小写）。只在**台账已有的一趟**上匹配，不做探针。
_ABSENT_MARKERS = ("not logged in", "please run /login", "login required",
                   "not authenticated", "no api key")

_CACHE: dict[str, Any] = {"at": 0.0, "payload": None}


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


def _login_evidence(last: dict[str, Any], local: Optional[bool] = None) -> dict[str, Any]:
    """登录态：**上一趟厂商亲口说的**优先，其次**本地强证据**（登录后才会落盘的那种）。

    11-08 21:2x 现读：这一格原来**只读「上一趟真跑的证据」** ⇒ 刚装上、还没派过活的那几家
    （Copilot CLI、Kilo Code CLI）永远是「配置好了」⇒ 主人看见一个开关，以为能用，
    一点上去才撞「它没登录」——**屏上写了假话**（而点「+」拉起登录的入口压根不出现）。

    三条证据按可信度排队：

    1. **上一趟厂商自己说的话**（「Not logged in · Please run /login」）——它亲口说的，最可靠；
    2. **本地强证据**：登录后才会落盘的那个文件（oauth token / creds json）、凭据库条目、
       登录用的环境变量 ⇒ 能判 True／False；
    3. 别的（目录里有过痕迹、压根没证据）⇒ **探不到（None）**，不判成不能。
    """
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


def external_agent_rows(*, refresh: bool = False) -> dict[str, Any]:
    """一家外部软件一行。**能不能用**只问 `availability()`；**为什么**原样带出。"""
    from tools.offload_tools import agent_roster

    now = time.time()
    cached = _CACHE.get("payload")
    if cached and not refresh and now - float(_CACHE.get("at") or 0.0) < PROBE_TTL_SECONDS:
        return cached

    roster = {agent.name: agent for agent in agent_roster()}
    grouped = _rows_by_agent()
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
        login = _login_evidence(last, getattr(base, "logged_in", None))

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

    payload = {
        "agents": rows,
        "probed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "probe_ttl_seconds": PROBE_TTL_SECONDS,
        "note": ("这一屏管的是「把活外包给那家软件」；把它的订阅/API 当模型用，在 Providers 那一屏，"
                 "两件事不共用一张表。登录态读的是**上一趟真跑的证据**，首屏不跑探针。"),
    }
    _CACHE["at"] = now
    _CACHE["payload"] = payload
    return payload


def forget_rows_cache() -> None:
    """开关改了：下一次取数别再拿缓存里的旧 `enabled` 糊弄人（探针不动，只丢结果）。"""
    _CACHE.clear()


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
