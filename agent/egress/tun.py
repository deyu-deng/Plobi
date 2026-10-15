"""May this machine host a TUN adapter right now — the guard, not the feature.

用户 10-13 把范围改成"Plobi 当整机出口"，10-14 又拍了"连 TUN 一起做（要管理员）"。
这一格做的是**先拒**：TUN 会改写这台的默认路由，抢错了就是"整台没网"，而这条路已经验证过一次
——静态验过的配置照样能把引擎起成一个自环（§12），所以能跑得动的东西必须先过这道守卫。

三条不改口的形状：

* **不自己提权**。要管理员就说不清怎么来（以管理员身份重启 Plobi），绝不悄悄弹 UAC 或绕。
* **不跟别人的 TUN 抢默认路由**。本机现读 iKuuu / 各类 VPN 都用 wintun；只要场上有**别人的**
  TUN 类网卡或它正压着默认路由，我们就拒，而不是把它的 route metric 改掉——那是改别人的机器状态。
* **判据只认结构化查询**。`Get-NetRoute` / `Get-NetAdapter` 给字段，不给文本；
  这台用 `netstat | grep :7895` 数出过临时端口的假命中，字符串匹配在这盘上不算证据。
"""

from __future__ import annotations

import ctypes
import json
import logging
import subprocess
import sys
from typing import Any, Optional

from agent.egress.rules import Table

log = logging.getLogger(__name__)

#: How long a machine probe may take before we call it unknown.  These run on the
#: supervision path, not a hot loop, but a hung `Get-NetRoute` must not hang a start.
PROBE_TIMEOUT = 8.0

#: Adapter classes that own — or are built to own — the default route.
TUN_CLASSES = ("wintun", "tap", "utun", "vpn", "tunnel")

_PS_DEFAULT_ROUTE = (
    "$ErrorActionPreference='SilentlyContinue';"
    "$r=Get-NetRoute -DestinationPrefix '0.0.0.0/0' | Sort-Object RouteMetric | Select-Object -First 4;"
    "foreach($x in $r){$a=Get-NetAdapter -IncludeHidden -InterfaceIndex $x.ifIndex;"
    "[pscustomobject]@{ifIndex=$x.ifIndex;name=$a.Name;description=$a.InterfaceDescription;"
    "status=[string]$a.Status;metric=$x.RouteMetric}|ConvertTo-Json -Compress}"
)

_PS_TUN_ADAPTERS = (
    "$ErrorActionPreference='SilentlyContinue';"
    "Get-NetAdapter -IncludeHidden | Where-Object{"
    "$d=(\"{0} {1}\" -f $_.InterfaceDescription,$_.ComponentID).ToLower();"
    "$d -match 'wintun|tap|utun|vpn|tunnel'}|"
    "ForEach-Object{[pscustomobject]@{name=$_.Name;description=$_.InterfaceDescription;status=[string]$_.Status}}|"
    "ConvertTo-Json -Compress"
)


def supported() -> bool:
    """Windows only for now — and say so, rather than half-doing it on POSIX."""
    return sys.platform == "win32"


def is_admin() -> tuple[bool, str]:
    """Am I elevated?  Never asks — a UAC prompt from inside a product is its own event."""
    if not supported():
        return False, f"这一格只实现 Windows（这台是 {sys.platform}）"
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin()), ""
    except Exception as exc:  # noqa: BLE001 - unknown beats assumed
        return False, f"问不到管理员状态：{type(exc).__name__}: {exc}"


def _ps_objects(script: str) -> tuple[list[dict[str, Any]], str]:
    """Run a PowerShell probe and parse its JSON objects — never its prose."""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            timeout=PROBE_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return [], f"{type(exc).__name__}: {exc}"
    out = r.stdout.decode("utf-8", "replace").strip()
    if r.returncode != 0 or not out:
        err = r.stderr.decode("utf-8", "replace").strip()
        return [], (err.splitlines()[-1] if err else f"rc={r.returncode}，无输出")
    items: list[dict[str, Any]] = []
    for chunk in out.splitlines():
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            loaded = json.loads(chunk)
        except ValueError:
            continue
        items.extend(loaded if isinstance(loaded, list) else [loaded])
    return items, ""


def default_route_holders() -> tuple[list[dict[str, Any]], str]:
    """What carries `0.0.0.0/0` right now, best metric first."""
    return _ps_objects(_PS_DEFAULT_ROUTE)


def tun_class_adapters() -> tuple[list[dict[str, Any]], str]:
    """Adapters whose class is a TUN — ours included, so callers must filter by name."""
    return _ps_objects(_PS_TUN_ADAPTERS)


def _is_tun_class(entry: dict[str, Any]) -> bool:
    said = f"{entry.get('description') or ''} {entry.get('component-id') or ''}".lower()
    return any(needle in said for needle in TUN_CLASSES)


def preflight(table: Optional[Table] = None) -> dict[str, Any]:
    """Can we hand this machine's default route to our engine — with the reasons."""
    tun = dict((table.engine_tun if table is not None else {}) or {})
    out: dict[str, Any] = {
        "requested": bool(tun.get("enabled")),
        "name": str(tun.get("name") or ""),
        "stack": str(tun.get("stack") or ""),
        "supported": supported(),
        "admin": False,
        "holders": [],
        "holders_error": "",
        "adapters_error": "",
        "foreign_tun": [],
        "ok": False,
        "reasons": [],
        "probe_errors": [],
    }
    if not out["requested"]:
        out["reasons"] = ["表里没要求 TUN（network.egress.engine.tun.enabled 不是 true）"]
        return out
    if not out["supported"]:
        out["reasons"] = [f"这一格只实现 Windows（这台是 {sys.platform}）"]
        return out

    admin, why = is_admin()
    out["admin"] = admin
    if not admin:
        out["reasons"].append(
            "这个进程不是管理员 —— TUN 要建网卡就得提权。我们不自己弹 UAC：以管理员身份重启 Plobi 再拨这个开关"
            + (f"（{why}）" if why else "")
        )

    holders, err = default_route_holders()
    out["holders_error"] = err
    if err:
        out["probe_errors"].append(f"default route: {err}")
    out["holders"] = holders

    adapters, err = tun_class_adapters()
    out["adapters_error"] = err
    if err:
        out["probe_errors"].append(f"tun adapters: {err}")
    mine = {e.get("name") for e in adapters if e.get("name") == out["name"]}
    foreign = [e for e in adapters if _is_tun_class(e) and e.get("name") not in mine]
    out["foreign_tun"] = foreign

    #: Refuse to fight for the default route.  This is the whole point of the guard:
    #: two TUNs on one box is not "whoever set the lower metric wins", it is a machine
    #: that stops having working networking until somebody reboots.
    if foreign:
        names = ", ".join(str(e.get("name") or e.get("description")) for e in foreign[:4])
        out["reasons"].append(
            f"场上已经有别人的 TUN 类网卡（{names}）—— 我们不抢默认路由，也不改别人的 metric；"
            f"要用 Plobi 的 TUN，先把那个 VPN 关掉"
        )

    if out["probe_errors"]:
        out["reasons"].append(
            "读不到本机路由/网卡状态（" + "；".join(out["probe_errors"]) + "）—— 不知道场上有谁就不动默认路由"
        )

    out["ok"] = not out["reasons"]
    return out


def _holders_said(info: dict[str, Any]) -> str:
    holders = info.get("holders") or []
    if info.get("holders_error"):
        return "默认路由: 读不到（" + str(info["holders_error"]) + "）"
    if not holders:
        return "默认路由: 0 条（探针成功，这台没有默认路由）"
    top = holders[0]
    return (
        f"默认路由: {len(holders)} 条，最优 metric 在 {top.get('name') or '?'}"
        f"（{top.get('description') or '无描述'}，metric {top.get('metric')}）"
    )


def _adapters_said(info: dict[str, Any]) -> str:
    if info.get("adapters_error"):
        # A failed probe must never print as "none out there" — that is the exact
        # shape that turns an unreadable machine into a green light. Caught by
        # running this against the real box (10-15 09:0x), see 纸 §25.
        return "场上别人的 tunnel/虚拟网卡: 读不到（" + str(info["adapters_error"]) + "）"
    foreign = info.get("foreign_tun") or []
    if not foreign:
        return "场上别人的 tunnel/虚拟网卡: 无（探针成功）"
    names = ", ".join(str(e.get("name") or e.get("description")) for e in foreign[:4])
    return f"场上别人的 tunnel/虚拟网卡: {names}"


def status_lines(info: dict[str, Any]) -> list[str]:
    """The same reading in prose, for `plobi network status` (裁定 94: one reading).

    Both machine readings are printed **even when the guard passes** — otherwise
    "nobody else owns the default route" would only be somebody's one-time eyeball,
    not something the next reader can re-run (架构师 23:2x ①).
    """
    if not info.get("requested"):
        return ["tun: 没要求（表里 engine.tun.enabled 不是 true）"]
    head = (
        f"tun: 守卫放行 —— {info.get('name')}（stack {info.get('stack')}）"
        if info.get("ok")
        else f"tun: 守卫拒绝开启 —— {info.get('name') or '未命名'}"
    )
    lines = [head, f"  {_holders_said(info)}", f"  {_adapters_said(info)}"]
    lines += [f"  - {r}" for r in info.get("reasons", [])]
    return lines
