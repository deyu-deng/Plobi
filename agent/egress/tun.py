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
import os
import subprocess
import sys
from typing import Any, Optional

from agent.egress.rules import Table
from agent.egress.winspawn import NO_WINDOW

log = logging.getLogger(__name__)

#: How long a machine probe may take before we call it unknown.  These run on the
#: supervision path, not a hot loop, but a hung `Get-NetRoute` must not hang a start.
PROBE_TIMEOUT = 8.0

#: Adapter classes that own — or are built to own — the default route.
TUN_CLASSES = ("wintun", "tap", "utun", "vpn", "tunnel")

#: What a TUN this product turns on looks like, when the table has not said otherwise.
#: Named by the **product** because the guard tells ours from somebody else's by name
#: (`preflight` filters `foreign_tun` on it), and the stack is the one the pinned engine
#: was compiled with (`engine_files.BINARY.note`: windows amd64 **with_gvisor**).
DEFAULT_NAME = "PlobiTun"
DEFAULT_STACK = "gvisor"


def default_block(*, allow_foreign: Optional[bool] = None, keep: dict[str, Any] | None = None) -> dict[str, Any]:
    """The ``engine.tun`` block the switch writes.

    One place owns the shape, so a pane, the CLI and the tray cannot each invent an adapter
    name — and ``allow-foreign`` stays an explicit decision instead of a default: it means
    "start our TUN next to theirs and let the metrics fight", which is 纸 §25's escape hatch,
    not this product's assumption about a machine.

    `allow_foreign=None` means **no new statement**: whatever the table already said is kept,
    because re-picking the carrier is not a way to silently revoke a decision somebody made.
    Only an explicit True/False overrides.

    Only keys mihomo itself reads go in here (plus our two guard-only keys): the compiler
    passes the block through, so a comment field would become a config field.
    """
    block: dict[str, Any] = dict(keep or {})
    block.setdefault("name", DEFAULT_NAME)
    block.setdefault("stack", DEFAULT_STACK)
    if allow_foreign is not None or "allow-foreign" not in block:
        block["allow-foreign"] = bool(allow_foreign)
    return block

#: 排序口径 = 路由 metric + 接口 metric。Windows 挑默认路由用的是两者之和；只按路由 metric 排，
#: 两条都是 0 的时候就退化成「谁先返回谁赢」，于是 TUN 明明压到了 im=1 还被判成没抢到（11-24）。
_PS_DEFAULT_ROUTE = (
    "$ErrorActionPreference='SilentlyContinue';"
    "$all=Get-NetRoute -DestinationPrefix '0.0.0.0/0' | ForEach-Object{"
    "$a=Get-NetAdapter -IncludeHidden -InterfaceIndex $_.ifIndex;"
    "$i=Get-NetIPInterface -InterfaceIndex $_.ifIndex -AddressFamily IPv4 | Select-Object -First 1;"
    "$im=0; if($i -and $i.InterfaceMetric){$im=[int]$i.InterfaceMetric};"
    "[pscustomobject]@{ifIndex=$_.ifIndex;name=$a.Name;description=$a.InterfaceDescription;"
    "status=[string]$a.Status;metric=$_.RouteMetric;im=$im;effective=([int]$_.RouteMetric+$im)}};"
    "$all | Sort-Object effective | Select-Object -First 4 | ForEach-Object{$_|ConvertTo-Json -Compress}"
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
            creationflags=NO_WINDOW,
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


def _wintun_side(binary: str = "", *, workdir: str | None = None) -> dict[str, Any]:
    """Import seam for the driver check — a guard must be testable without a real DLL."""
    from agent.egress import engine_files

    return engine_files.wintun_side(binary, workdir=workdir)


#: Named here so the refusal sentence does not depend on importing the manifest module.
WINTUN_BASENAME = "wintun.dll"
WINTUN_BYTES = 427_552


#: A TUN that does not own the default route is a TUN that does nothing: the machine keeps
#: its old interface and the user sees "TUN 开了但没生效". 11-02 这台实测到的正是这一种——
#: WLAN 2 的 route metric 是 0，我们那块网卡建出来了却抢不过它。
#: 所以起完之后必须回头看一眼路由在谁手上，而不是信 `enable: true` 那句话。
ROUTE_SETTLE_SECONDS = 3.0

#: 把**我们自己造的**那块网卡的接口 metric 压到最低。11-24 这台量到的是 WLAN 2 im=45，
#: 而 wintun 网卡由 Windows 按链路速度自动分配 metric，往往比物理网卡更高 —— 于是
#: `enable: true`、`auto-route: true`、`strict-route: true` 全写了，默认路由仍然留在 WLAN 2。
#: 这条只改我们自己造的那块网卡（名字必须是表里的 tun.name），撤 TUN 时网卡随之消失、无残留，
#: 与「改别人的 metric」是两件事。
#: 用占位符而不是 str.format：脚本里有 powershell 的 `@{...}`，format 会把那个花括号当字段名。
_PS_PREFER_OWN = (
    "$ErrorActionPreference='Stop';"
    "$a=Get-NetAdapter -IncludeHidden -Name '__NAME__' | Select-Object -First 1;"
    "if(-not $a){throw 'no adapter named __NAME__'};"
    "Set-NetIPInterface -InterfaceIndex $a.ifIndex -AddressFamily IPv4 -InterfaceMetric __METRIC__ | Out-Null;"
    "$i=Get-NetIPInterface -InterfaceIndex $a.ifIndex -AddressFamily IPv4 | Select-Object -First 1;"
    "[pscustomobject]@{name=$a.Name;metric=$i.InterfaceMetric}|ConvertTo-Json -Compress"
)


def prefer_own_adapter(name: str, metric: int = 1) -> tuple[bool, str]:
    """让**我们的** TUN 网卡在默认路由竞争里赢：把它的接口 metric 压到 ``metric``。

    ``(ok, said)``。只动 ``name`` 这块网卡——不是别人的隧道、不是物理网卡，也不需要
    ``strict-route`` 那种「删掉别人默认路由」的明示接管。失败不抛：调用方照旧去查路由，
    查不到就把 TUN 撤掉，口径与 :func:`route_taken` 一致。
    """
    if not supported():
        return False, f"这一格只实现 Windows（这台是 {sys.platform}）"
    if not name:
        return False, "表里没写 tun.name，不知道哪块网卡是我们的"
    script = (_PS_PREFER_OWN
              .replace("__NAME__", name.replace("'", "''"))
              .replace("__METRIC__", str(int(metric))))
    items, err = _ps_objects(script)
    if not items:
        return False, err or "设了 metric 但读不回来"
    got = items[0].get("metric")
    return True, f"{items[0].get('name') or name} 的接口 metric 压到 {got}"


def route_taken(name: str) -> tuple[bool, str, list[dict[str, Any]]]:
    """Did our adapter actually become the default route?  ``(taken, said, holders)``.

    A failed probe is **not** a pass: 口径 27 — we do not hand the machine a half-TUN
    because we could not read it. The caller decides what to do about it; nothing here
    changes a route or a metric.
    """
    holders, err = default_route_holders()
    if err:
        return False, f"读不到默认路由（{err}）——不知道路由在谁手上就不算 TUN 生效", holders
    if not holders:
        return False, "这台机器现在没有默认路由可读，TUN 建了也没人走它", holders
    top = holders[0]
    if str(top.get("name") or "") == name:
        return True, f"默认路由已在 {name}（metric {top.get('metric')}）", holders
    return False, (
        f"默认路由还在 {top.get('name') or '?'}（metric {top.get('metric')}），我们的网卡 {name} 没抢到——"
        f"TUN 建出来了但流量不会进来。要么把 {top.get('name')} 的接口 metric 调高，"
        f"要么在表里写明 network.egress.engine.tun.strict-route: true（那会删掉别人的默认路由，是一次明示的接管）"
    ), holders


def _sidecar_reading() -> dict[str, Any]:
    """提权侧车现在什么样：在不在岗、它**自己**是不是真拿到了管理员。

    从此「TUN 能不能开」有两个来源：本进程就是管理员（开发态势——从提权过的终端启动
    Plobi），或者侧车在岗且确实提了权（装好的机器上的常态）。只认前者的后果是装好的机器
    永远亮红三角，因为用户永远不会主动用管理员权限去开一个聊天软件。
    """
    from agent.egress import helper_client

    if helper_client.is_serving():
        # 本进程就是侧车：admin 已经写在 preflight 之外那一格里，别自己连自己
        return {"available": True, "ready": False, "detail": "本进程即侧车", "pid": os.getpid()}
    if not helper_client.supported():
        return {"available": False, "ready": False,
                "detail": f"这个平台没有侧车这条线（{sys.platform}）", "pid": None}
    reply = helper_client.ping()
    if not reply.get("ok"):
        return {"available": False, "ready": False,
                "detail": str(reply.get("said") or reply.get("error") or "侧车没答话"), "pid": None}
    result = reply.get("result") or {}
    elevated = bool(result.get("admin"))
    return {
        "available": True,
        "ready": elevated,
        "detail": "" if elevated else f"侧车在岗但没提权（{result.get('admin_detail') or '原因不明'}）",
        "pid": result.get("pid"),
    }


def preflight(table: Optional[Table] = None, *, workdir: str | None = None) -> dict[str, Any]:
    """Can we hand this machine's default route to our engine — with the reasons."""
    tun = dict((table.engine_tun if table is not None else {}) or {})
    out: dict[str, Any] = {
        "requested": bool(tun.get("enabled")),
        "name": str(tun.get("name") or ""),
        "stack": str(tun.get("stack") or ""),
        "supported": supported(),
        "admin": False,
        "sidecar": {},
        "holders": [],
        "holders_error": "",
        "adapters_error": "",
        "foreign_tun": [],
        "allow_foreign": False,
        "overridden": False,
        "wintun": {},
        "block": default_block(allow_foreign=bool(tun.get("allow-foreign")), keep=tun),
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
    sidecar = _sidecar_reading()
    out["sidecar"] = sidecar
    if not (admin or sidecar.get("ready")):
        # 文案换了（2026-11-22）：原来的「以管理员身份重启 Plobi 再拨这个开关」是一条
        # 用户永远走不通的路——产品不会让用户用管理员权限去开一个聊天软件。现在这只手
        # 是提权侧车，所以要说的不是「你去提权」，而是「那只手里现在没人」。
        said = (
            "没有能提权的手 —— TUN 要建网卡就得有一个管理员进程。装好的机器上这只手是提权侧车"
            "（登录时由计划任务「Plobi Egress Helper」拉起）；它现在不在岗：要么安装器那一步没跑成，"
            "要么它没起来（看 logs/egress-helper.log）"
        )
        if sidecar.get("detail"):
            said += f"。侧车读数：{sidecar['detail']}"
        if why:
            said += f"（本进程也不是管理员：{why}）"
        out["reasons"].append(said)

    #: The driver, not the permission.  Measured 11-01: the guard passed on an elevated
    #: process and mihomo still came up with **no TUN at all** because nothing was loading
    #: `wintun.dll` — "admin yes, adapter no" is a refusal nobody can guess from the log.
    side = _wintun_side(getattr(table, "engine_binary", "") if table is not None else "", workdir=workdir)
    out["wintun"] = side
    if not side["found"]:
        out["reasons"].append(
            "场上没有 wintun.dll —— TUN 网卡建不出来。"
            + (
                f"我们随包的那份在 {side['source']}，起引擎前会复制到 {side['target']}"
                if side["source"]
                else f"把 {WINTUN_BASENAME}（{WINTUN_BYTES} 字节）放到 {side['target']}"
            )
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
    out["allow_foreign"] = bool(tun.get("allow-foreign"))

    #: Refuse to fight for the default route.  This is the whole point of the guard:
    #: two TUNs on one box is not "whoever set the lower metric wins", it is a machine
    #: that stops having working networking until somebody reboots.
    #: 口径 27 keeps the default conservative; 纸 §25 buys the escape with a field in the
    #: **table** (no UI switch), and the refusal has to say why a tunnel that is quiet
    #: *today* is still a reason to refuse — Tailscale's exit-node flips it on mid-session.
    if foreign:
        names = ", ".join(str(e.get("name") or e.get("description")) for e in foreign[:4])
        holding = {h.get("name") for h in holders if h.get("name")}
        quiet = [e for e in foreign if e.get("name") not in holding]
        if out["allow_foreign"]:
            out["overridden"] = True
        else:
            reason = (
                f"场上已经有别人的 TUN 类网卡（{names}）—— 我们不抢默认路由，也不改别人的 metric；"
                f"要么先把那个 VPN 关掉，要么在表里写明 network.egress.engine.tun.allow-foreign: true"
            )
            if quiet:
                qn = ", ".join(str(e.get("name") or e.get("description")) for e in quiet[:4])
                reason += (
                    f"（其中 {qn} 现在不持默认路由，但之后可能抢走 —— 现在放行就等于在那一刻整机断网，"
                    f"所以这条现在也挡）"
                )
            out["reasons"].append(reason)

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


def _wintun_said(info: dict[str, Any]) -> str:
    side = info.get("wintun") or {}
    if not side:
        return "wintun 驱动: 没查"
    if side.get("found"):
        return f"wintun 驱动: {side.get('where')}"
    return "wintun 驱动: 不在场（引擎旁边与 System32 都没有）"


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
    lines = [head, f"  {_holders_said(info)}", f"  {_adapters_said(info)}", f"  {_wintun_said(info)}"]
    if info.get("overridden"):
        names = ", ".join(str(e.get("name") or e.get("description")) for e in (info.get("foreign_tun") or [])[:4])
        lines.append(
            f"  ! 表里 network.egress.engine.tun.allow-foreign: true ⇒ 带着别人的 tunnel 网卡继续（{names}）"
            "—— 默认路由真要打起来是我们抢的，这一条由表负责，不回头改别人的 metric"
        )
    lines += [f"  - {r}" for r in info.get("reasons", [])]
    return lines
