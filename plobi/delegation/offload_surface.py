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
from typing import Any

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


def _login_evidence(last: dict[str, Any]) -> dict[str, Any]:
    """从**已经发生的那一趟**读登录态。没有证据就 `None`，不猜。"""
    if not last:
        return {"value": None, "source": "unknown", "detail": "还没派过活，没有可读的证据"}
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
        login = _login_evidence(last)

        capabilities = base.capabilities if base is not None else {}
        decision = ExternalAgent(
            name=name,
            installed=bool(binary),
            logged_in=login["value"],
            channel_ready=bool(binary),
            permission_flag=base.permission_flag if base is not None else "",
            capabilities=capabilities,
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
