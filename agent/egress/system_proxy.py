"""Hand the *machine's* egress to our engine — the "Plobi 当 VPN 用" slice.

用户 10-13 11:5x 改口：范围不再限于"Plobi 自己起的流量"，要整机。这一格做的是
**接管系统代理（WinINET）+ 由表生成 PAC**，不是 TUN：

* 不需要管理员权限，写的是 ``HKCU\\...\\Internet Settings``（当前用户）；
* **可精确回退** —— 接管前系统代理是什么，原样备份，`off` 一字不改地写回去；
* 覆盖范围照实说：走系统代理的程序（浏览器、多数 Electron/Qt 应用、curl/npm/pip
  的默认行为）会进来；**不走代理的原生 TCP/UDP（多数游戏、命令行里硬编直连的
  工具）不会** —— 那一档要 TUN，是下一格，且要管理员权限并会和现存的 VPN 抢默认路由。

PAC 只负责"哪些绝不进代理"（本机、私网、表里点名 direct 的域名），其余**全部交给
引擎按同一张表选路** —— 出口判定只有一处真源（裁定 94），PAC 里不复制 `geosite:`
之类的判断，引擎收到请求后自己会算。

**Windows 上不要把 PAC 挂到系统上**（2026-10-30 实测，A/B/A 两轮）：`AutoConfigURL`
一挂上（`file://` 或 `http://` 都一样），WinINET 的 **HTTPS 就全断** ——
`https://www.google.com/generate_204` 报「基础连接已经关闭: 发送时发生错误」，
把 `AutoConfigURL` 摘掉只留静态代理，同一发请求就是 204。HTTP 不受影响，所以这个
形状能藏很久：网页看着"部分能开"，TLS 站点全废。
⇒ 接管只写 `ProxyEnable`/`ProxyServer`/`ProxyOverride`；PAC **仍然生成并留在盘上**
（给人看、给会自己配 PAC 的客户端用），但不挂到系统上。
（上一笔 10-28 反过来做过：那时测的是"谁在承运"，把 PAC 挂成 http —— 结果就是这台
机器打不开 google。静态那一路本来就带着同一张表要它带的一切：一切都交给引擎。）
"""

from __future__ import annotations

import ctypes
import ipaddress
import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from agent.egress.rules import Table, load_table

log = logging.getLogger(__name__)

REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
REG_VALUES = ("ProxyEnable", "ProxyServer", "AutoConfigURL", "ProxyOverride")
PAC_BASENAME = "plobi-egress.pac"
BACKUP_BASENAME = "system-proxy.json"

INTERNET_OPTION_SETTINGS_CHANGED = 39
INTERNET_OPTION_REFRESH = 37


def supported() -> bool:
    """Windows only for now — and say so rather than half-doing it on POSIX."""
    return sys.platform == "win32"


def _watchdog():
    """The watchdog module, imported late: it calls back into the engine."""
    from agent.egress import watchdog

    return watchdog


#: Spawn the standalone guard detached so it survives Plobi's tree-kill on quit.
def _start_guard(home: str | Path | None = None) -> bool:
    """Launch the detached proxy guard (covers the hard-kill / crash case).

    The guard polls for a dead loopback proxy and hands the machine back — see
    ``proxy_guard.py``.

    **How it survives the tree-kill** (measured 11-04, the reason it kept failing):
    the desktop's quit path runs ``taskkill /PID <backend> /T /F``, and ``/T``
    walks the **live** process tree.  ``DETACHED_PROCESS`` alone is *not*
    enough on Windows — it detaches the console, not the parent/child link, so
    a directly-spawned guard is still a descendant of the backend and gets
    reaped with it.  (The old docstring claimed otherwise; it was wrong, and
    the guard died every single quit without ever logging a restore.)

    So the guard is spawned through a ``cmd /c start`` shim that exits
    immediately: the guard's parent is then a *dead* PID, which the tree walk
    cannot reach.  The shim is gone within milliseconds of the spawn, long
    before any tree-kill, so the guard is a true orphan by quit time.
    """
    guard = Path(__file__).resolve().parent / "proxy_guard.py"
    try:
        args = [sys.executable, str(guard)]
        # Pass the *raw* home (or none at all) — `proxy_guard.main` resolves it via
        # `sp._home()` itself.  Pre-resolving here would double the `egress` segment
        # (egress/egress) and split the pid/backup/log into the wrong directory.
        if home is not None:
            args += ["--home", str(home)]
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
        # `cmd /c start "" /b <exe> <args>`: the shim exits at once, orphaning the
        # guard. `""` is `start`'s (empty) window title, not an argument.
        subprocess.Popen(
            ["cmd.exe", "/c", "start", "", "/b", *args],
            creationflags=flags,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception as exc:  # noqa: BLE001 - guard is a best-effort safety net
        log.warning("system proxy: 独立守卫没起来（%s）——Plobi 硬退出时可能留死代理", exc)
        return False


def _stop_guard(home: str | Path | None = None) -> None:
    """Best-effort: kill the detached guard (e.g. when `proxy off` restores cleanly).

    The pid is **verified before it is killed** (measured 11-04): the file names the
    pid of the guard that was running when we last started, and Windows recycles
    pids — so that number may well belong to something else by now. ``taskkill`` on a
    recycled pid takes down an innocent bystander.
    """
    pid_path = _home(home) / "runtime" / "proxy-guard.pid"
    if not pid_path.exists():
        return
    try:
        old = int(pid_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        old = None
    if old is not None and _pid_is_guard(old):
        try:
            # No ``/T``: the venv's ``python.exe`` is a launcher stub whose child is
            # the real worker, and a tree-kill of the stub takes the worker with it.
            # The pid file names the *worker*, so killing it exactly is enough — the
            # stub exits once its child is gone.
            subprocess.run(
                ["taskkill", "/PID", str(old), "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except OSError:
            pass
    try:
        pid_path.unlink(missing_ok=True)
    except OSError:
        pass


def _pid_is_guard(pid: int) -> bool:
    """Is this pid one of our proxy guards right now?

    The ``cmd /c start`` shim mentions the script in its command line but is **not**
    the guard — and killing it with ``/T`` kills the guard it just launched.
    """
    try:
        import psutil
    except ImportError:
        return False
    try:
        cmdline = " ".join(psutil.Process(pid).cmdline())
    except Exception:  # noqa: BLE001 - gone, or not ours to look at
        return False
    parts = cmdline.strip().split()
    if parts and parts[0].lower().endswith("cmd.exe"):
        return False
    return "proxy_guard.py" in cmdline


def _home(home: str | Path | None = None) -> Path:
    from agent.egress.engine import egress_dir

    return egress_dir(home)


#: The desktop's "this quit was deliberate" note — written by `egress-proxy.ts` right before it
#: tree-kills us (WP-QUIT-CLEAN, 11-05).  Without it both detached helpers keep doing their job:
#: the watchdog's ``taken-over`` rule re-takes the machine one tick after the registry hand-back,
#: and the perpetual guard never leaves, leaving a pid file that names a dead pid.
QUIT_MARKER_BASENAME = "user-quit.json"


def _quit_marker_path(home: str | Path | None = None) -> Path:
    return _home(home) / "runtime" / QUIT_MARKER_BASENAME


def quit_marker_since(home: str | Path | None = None) -> Optional[float]:
    """Epoch **seconds** of a user-quit marker, or ``None`` when there is no usable one.

    Timestamped on purpose: a stale file left behind by a previous session must never retire a
    helper that was started *after* it, so callers compare this against their own start time
    rather than trusting the file's mere existence.  The desktop writes milliseconds.
    """
    try:
        raw = json.loads(_quit_marker_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    at = raw.get("at") if isinstance(raw, dict) else None
    if not isinstance(at, (int, float)):
        return None
    return float(at) / 1000.0


def clear_quit_marker(home: str | Path | None = None) -> None:
    """Forget a quit intent — a fresh takeover supersedes it (see :func:`enable`)."""
    try:
        _quit_marker_path(home).unlink(missing_ok=True)
    except OSError:
        pass


@dataclass(frozen=True)
class ProxyPlan:
    """What enabling would do, computed before anything is written."""

    ok: bool
    reason: str
    port: int = 0
    pac_path: str = ""
    pac_text: str = ""
    registry: Optional[dict[str, Any]] = None
    was: Optional[dict[str, Any]] = None
    #: Bypass rules `ProxyOverride` cannot express (non-byte-boundary CIDR, IPv6, geosite:).
    #: Named, never dropped — a silent narrowing here is the 裁定 94 failure again.
    override_unexpressible: tuple[str, ...] = ()

    def lines(self) -> list[str]:
        out = [f"system proxy: {'ready' if self.ok else 'refused'} — {self.reason}"]
        if self.ok:
            out.append(f"pac: {self.pac_path}")
            out.append(f"registry: {self.registry}")
            if self.was is not None:
                previous = self.was.get("ProxyEnable") and self.was.get("ProxyServer") or "（无）"
                out.append(f"接管前系统代理是: {previous}（off 会原样写回）")
            if self.override_unexpressible:
                out.append(
                    f"⚠️ ProxyOverride 表达不了 {len(self.override_unexpressible)} 条 bypass"
                    f"（{', '.join(self.override_unexpressible[:6])}）—— 会执行 PAC 的程序按表走，"
                    f"只读静态代理的那些会把这些也交给引擎，由引擎按同一张表判"
                )
        return out


def _pac_condition(rule: Any) -> Optional[str]:
    """One table rule → one PAC predicate. `None` = cannot be said in PAC."""
    value = rule.value
    if rule.kind == "exact":
        return f'host.toLowerCase() === "{value}"'
    if rule.kind == "suffix":
        return f'dnsDomainIs(host, ".{value}")'
    if rule.kind == "cidr":
        try:
            net = ipaddress.ip_network(value, strict=False)
        except ValueError:
            return None
        if net.version != 4:
            # PAC's isInNet is IPv4-only; an IPv6 range would silently not match.
            return None
        return f'isInNet(host, "{net.network_address}", "{net.netmask}")'
    return None


def pac_text(table: Table, port: int) -> str:
    """The PAC itself: what never enters the proxy; everything else goes to the engine."""
    lines = [
        "// Generated by Plobi from network.egress — do not edit by hand.",
        "// Anything not listed below is handed to the engine on 127.0.0.1:%d," % port,
        "// which applies the same table (including geosite:, which PAC cannot express).",
        "function FindProxyForURL(url, host) {",
        '  host = host.toLowerCase();',
        '  if (isPlainHostName(host) || host === "localhost") return "DIRECT";',
        '  if (dnsDomainIs(host, ".local") || dnsDomainIs(host, ".lan")) return "DIRECT";',
        '  if (isInNet(host, "0.0.0.0", "0.0.0.0")) return "DIRECT";',
        '  if (isInNet(host, "127.0.0.0", "255.0.0.0")) return "DIRECT";',
        '  if (isInNet(host, "10.0.0.0", "255.0.0.0")) return "DIRECT";',
        '  if (isInNet(host, "172.16.0.0", "255.240.0.0")) return "DIRECT";',
        '  if (isInNet(host, "192.168.0.0", "255.255.0.0")) return "DIRECT";',
        '  if (isInNet(host, "169.254.0.0", "255.255.0.0")) return "DIRECT";',
    ]
    for rule in table.rules:
        if rule.via != "direct" or rule.kind in ("geosite", "all"):
            continue
        condition = _pac_condition(rule)
        if condition is None:
            lines.append(f"  // network.egress rule {rule.match} -> direct: not expressible in PAC")
            continue
        lines.append(f"  if ({condition}) return \"DIRECT\"; // {rule.match}")
    lines.append(f'  return "PROXY 127.0.0.1:{port}";')
    lines.append("}")
    return "\n".join(lines) + "\n"


def read_current() -> dict[str, Any]:
    """The system's own proxy settings, verbatim (missing values are absent, not zeroed)."""
    if not supported():
        return {"unsupported": sys.platform}
    import winreg

    out: dict[str, Any] = {}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH) as key:
            for name in REG_VALUES:
                try:
                    value, kind = winreg.QueryValueEx(key, name)
                except FileNotFoundError:
                    continue
                out[name] = value
    except OSError as exc:
        out["unreadable"] = f"{type(exc).__name__}: {exc}"
    return out


def _write_registry(values: dict[str, Any]) -> None:
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_SET_VALUE) as key:
        for name in REG_VALUES:
            if name not in values or values[name] is None:
                try:
                    winreg.DeleteValue(key, name)
                except FileNotFoundError:
                    pass
                continue
            value = values[name]
            kind = winreg.REG_DWORD if isinstance(value, int) else winreg.REG_SZ
            winreg.SetValueEx(key, name, 0, kind, value)


def _notify_wininet() -> None:
    """Tell running apps the settings changed; without this many wait for a restart."""
    try:
        wininet = ctypes.windll.wininet
        wininet.InternetSetOptionW(0, INTERNET_OPTION_SETTINGS_CHANGED, 0, 0)
        wininet.InternetSetOptionW(0, INTERNET_OPTION_REFRESH, 0, 0)
    except Exception as exc:  # not fatal: the registry write already happened
        log.warning("system proxy: InternetSetOption failed: %s", exc)


def _backup_path(home: str | Path | None = None) -> Path:
    return _home(home) / BACKUP_BASENAME


#: WinINET's `ProxyOverride` grammar: host wildcards and dotted prefixes only.
#: No CIDR, no `geosite:` — what does not fit is **named**, never silently dropped.
_OVERRIDE_ALWAYS = ("localhost", "*.local", "*.lan")
_OVERRIDE_PRIVATE = ("127.*", "10.*", "192.168.*", "169.254.*")


def override_entries(table: Table) -> tuple[list[str], list[str]]:
    """(`entries`, `unexpressible`) — the bypass list a PAC-less client can still obey.

    用户 10-15 拍的丙：PAC 之外**也要**写静态 `ProxyServer`/`ProxyOverride`，因为只读静态代理的
    客户端（.NET 一批、不少 CLI 库）根本不执行 PAC，对它们"接管"等于"直连"。这里能表达多少就写多少，
    表达不了的（非字节边界的 CIDR、IPv6、`geosite:`）照实进第二项，由 plan 的 lines 点名。
    """
    entries: list[str] = list(_OVERRIDE_ALWAYS) + list(_OVERRIDE_PRIVATE)
    unexpressible: list[str] = []
    for rule in table.rules:
        if rule.via != "direct" or rule.kind in ("geosite", "all"):
            continue
        if rule.kind in ("exact", "suffix"):
            # `match` keeps the author's spelling (`*.internal.test`); `value` is the
            # normalised host the grammar wants.
            entries.append(rule.value if rule.kind == "exact" else f"*.{rule.value}")
            continue
        if rule.kind == "cidr":
            try:
                net = ipaddress.ip_network(rule.match, strict=False)
            except ValueError:
                unexpressible.append(rule.match)
                continue
            if net.version != 4 or net.prefixlen % 8:
                # IPv6 has no wildcard grammar here, and a /12 mask cannot be a prefix star.
                unexpressible.append(rule.match)
                continue
            octets = str(net.network_address).split(".")
            keep = net.prefixlen // 8
            entries.append(".".join(octets[:keep]) + ".*" if keep else "0.*")
            continue
        unexpressible.append(rule.match)
    return list(dict.fromkeys(entries)), unexpressible


def plan(table: Optional[Table] = None, *, home: str | Path | None = None) -> ProxyPlan:
    """Everything enabling would do — computed, nothing written."""
    if not supported():
        return ProxyPlan(False, f"这一格目前只实现 Windows（这台是 {sys.platform}）")
    table = table if table is not None else load_table()
    if not table.active:
        return ProxyPlan(False, "没有声明 network.egress 表：接管系统代理会把整机交给一个不存在的出口")
    port = table.engine_mixed_port
    if not port:
        return ProxyPlan(False, "network.egress.engine.mixed_port 没写：PAC 不知道该把流量交给谁")

    from agent.egress.rules import EgressConfigError

    catch_all = table.rules[-1]
    if catch_all.via.startswith("proxy:"):
        # Handing the machine to an exit that is not carrying traffic right now is
        # worse than not handing it over: nothing would leave this box.
        from agent.egress.engine import usable_exit

        try:
            exit_url = table.client_url(catch_all.via[len("proxy:") :])
        except EgressConfigError as exc:
            return ProxyPlan(False, f"兜底出口不能用：{exc}")
        if not exit_url:
            return ProxyPlan(
                False,
                f"兜底出口 {catch_all.via} 说不清该往哪儿送：它既不是一个 URL 出口，"
                f"也没有引擎的前门（network.egress.engine.mixed_port 没写）——"
                f"整机的流量没有去处，不能接管",
            )
        carried, reason = usable_exit(exit_url)
        if not carried:
            return ProxyPlan(False, f"出口 {exit_url} 此刻带不动流量（{reason}）——接管整机会让这台没有出口")

    directory = _home(home)
    directory.mkdir(parents=True, exist_ok=True)
    pac = directory / PAC_BASENAME
    was = read_current()
    if was.get("unreadable"):
        return ProxyPlan(False, f"读不到系统当前代理设置：{was['unreadable']}（不许在不知道原值的情况下改它）")

    already = _backup_path(home)
    if already.exists():
        try:
            json.loads(already.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return ProxyPlan(False, f"上一次的备份读不出来（{type(exc).__name__}）——先跑 plobi network proxy off 或删 {already}")

    entries, unexpressible = override_entries(table)
    return ProxyPlan(
        True,
        f"表 {len(table.rules)} 条，其余全部交给引擎按表选路；只写静态代理那一路"
        f"（PAC 留在盘上但不挂系统——挂上 WinINET 的 HTTPS 会全断，10-30 实测）",
        port,
        str(pac),
        pac_text(table, port),
        {"ProxyEnable": 1,
         "ProxyServer": f"127.0.0.1:{port}",
         "ProxyOverride": ";".join(entries)},
        was,
        tuple(unexpressible),
    )


def enable(table: Optional[Table] = None, *, home: str | Path | None = None, apply: bool = True) -> ProxyPlan:
    """Take over the system proxy, remembering exactly what was there before."""
    # A takeover supersedes any earlier "the user quit" note (WP-QUIT-CLEAN, 11-05): the helpers
    # compare the marker against their own start time, so a leftover file is already harmless —
    # clearing it here keeps a fresh session from starting out with a stale intent on disk.
    clear_quit_marker(home)

    p = plan(table, home=home)
    if not p.ok:
        if apply:
            # A refusal now must not mean "and nobody will ever look again": the
            # watchdog's next tick re-plans (it may be the engine that was missing).
            _watchdog().start(home=home)
        return p
    if not apply:
        return p

    backup = _backup_path(home)
    if not backup.exists():
        backup.write_text(json.dumps(p.was, ensure_ascii=False, indent=1), encoding="utf-8")
    Path(p.pac_path).write_text(p.pac_text, encoding="utf-8")
    # `_write_registry` deletes any REG_VALUES key the plan omits, so an
    # ``AutoConfigURL`` we advertised earlier (or that a stale takeover left) is
    # cleared here — leaving it set would keep breaking HTTPS.
    _write_registry(dict(p.registry or {}))
    _notify_wininet()
    # The machine now depends on the engine being up, and nothing else supervises it
    # when the desktop is closed (10-29: it went down twice and stayed down).
    alive, detail = _watchdog().start(home=home)
    if not alive:
        log.warning("system proxy: 看门狗没起来（%s）——引擎掉了就只能靠人发现", detail)
    # Detached survivor: if Plobi is hard-killed, this guard (not in our process tree)
    # restores the system proxy so Marvis/DingTalk/browsers don't read "no internet".
    if not _start_guard(home=home):
        log.warning("system proxy: 独立守卫没起来——Plobi 硬退出时可能留死代理")
    log.warning("system proxy: Plobi 接管了系统代理（PAC %s）——回退：plobi network proxy off", p.pac_path)
    return p


def _localhost_port_dead(server: str) -> bool:
    """True only if ``server`` is a **loopback** ``host:port`` nothing is listening on.

    Used as the fail-safe gate when restoring: handing the machine a loopback proxy
    whose port is dead reads, from every app's point of view, as "no internet".
    """
    raw = (server or "").strip()
    if not raw:
        return False
    if "://" in raw:  # tolerate `http://127.0.0.1:7890`
        raw = raw.split("://", 1)[1]
    raw = raw.split(";")[0].strip()  # `http=h:p;https=h:p` — first entry is enough
    if "=" in raw:
        raw = raw.split("=", 1)[1].strip()
    host, sep, port = raw.rpartition(":")
    if not sep or not port.isdigit():
        return False
    host = host.strip().strip("[]").lower()
    if host not in ("127.0.0.1", "localhost", "::1", ""):
        return False
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.6)
    try:
        return sock.connect_ex((host or "127.0.0.1", int(port))) != 0
    except OSError:
        return True
    finally:
        sock.close()


def disable(*, home: str | Path | None = None) -> tuple[bool, str]:
    """Restore the pre-takeover settings byte for byte, or say why we cannot."""
    if not supported():
        return False, f"这一格只实现 Windows（这台是 {sys.platform}）"
    backup = _backup_path(home)
    if not backup.exists():
        current = read_current()
        port = (load_table()).engine_mixed_port
        if port and str(current.get("ProxyServer") or "") == f"127.0.0.1:{port}":
            return False, ("系统代理指着我们的引擎端口，但没有接管记录：不敢替你猜原值，"
                           "请手工核对 Internet Settings")
        return False, f"没有接管记录（{backup}）——系统代理不是我们开的，不碰它"
    try:
        was = json.loads(backup.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return False, f"备份读不出来（{type(exc).__name__}）：{backup}"
    restore = {k: was.get(k) for k in REG_VALUES if k in was} if was else {k: None for k in REG_VALUES}
    # Fail-safe (measured 10-31): the saved "before" was often another proxy client's
    # own ``127.0.0.1:7890`` — long since dead once that client was uninstalled. Restoring
    # it byte-for-byte would hand the machine a corpse and read as "no internet". If what
    # we'd put back points at a loopback port nothing listens on, hand back DIRECT instead.
    if restore.get("ProxyEnable") and _localhost_port_dead(str(restore.get("ProxyServer") or "")):
        log.warning("system proxy: 备份里的代理 %s 已无监听，改为还原成直连", was.get("ProxyServer"))
        restore = {"ProxyEnable": 0, "ProxyServer": None}
    _write_registry(restore)
    _notify_wininet()
    backup.unlink(missing_ok=True)
    _watchdog().stop(home=home)
    _stop_guard(home=home)
    return True, f"已还原接管前的系统代理设置：{was}"


def ensure(table: Optional[Table] = None, *, home: str | Path | None = None) -> Optional[ProxyPlan]:
    """Make the machine's proxy agree with the table — the boot path calls this.

    ``None`` means "the table has no opinion, say nothing".  A table that turned
    the switch off while the registry still points at our PAC gets **handed back**,
    because the table is the only source of truth (裁定 94) — not because we guess.
    """
    table = table if table is not None else load_table()
    if not table.system_proxy_enabled:
        if supported() and status(table, home=home)["ours"]:
            ok, detail = disable(home=home)
            return ProxyPlan(ok, "表里已关掉，系统代理交还原主" if ok else f"想交还但没成：{detail}")
        return None
    if not getattr(table, "engine_enabled", True):
        # 接管整机是引擎的**下游**：engine 关掉之后，注册表里只剩下一个不会有人监听的端口。
        # 11-08：只关了引擎、接管那一格忘了关（或手改 config.yaml 只改了一处），下面照样
        # `enable`，把全机指过去 —— 浏览器一片 ERR_PROXY_CONNECTION_FAILED，比不接管更像断网。
        # 表里两格自相矛盾时按「先交还」办：没有引擎可指，接管就没有内容。
        if supported() and status(table, home=home)["ours"]:
            ok, detail = disable(home=home)
            return ProxyPlan(ok, "引擎已关掉，系统代理交还原主" if ok else f"想交还但没成：{detail}")
        return ProxyPlan(True, "引擎已关掉，不做接管")
    # **Boot order matters.**  ``plan()`` refuses a takeover whose exit cannot carry
    # traffic, and a fresh boot has no engine listening yet — measured 10-31: after a
    # reboot nothing started the engine, the takeover was refused for that reason, and
    # the machine sat with no proxy at all.  Light the engine first; if it still cannot
    # come up, the refusal below is honest.
    try:
        from agent.egress import engine as _engine

        _engine.ensure(table, home=home)
    except Exception as exc:  # noqa: BLE001 - the plan() refusal carries the real reason
        log.warning("system proxy: 先起引擎失败（%s: %s）——继续按计划判", type(exc).__name__, exc)
    return enable(table, home=home)


def status(table: Optional[Table] = None, *, home: str | Path | None = None) -> dict[str, Any]:
    """Is the machine's proxy ours right now — read from the registry, not from memory."""
    current = read_current()
    table = table if table is not None else load_table()
    pac = _home(home) / PAC_BASENAME
    port = table.engine_mixed_port
    ours = bool(port) and int(current.get("ProxyEnable") or 0) == 1 \
        and str(current.get("ProxyServer") or "") == f"127.0.0.1:{port}"
    return {
        "supported": supported(),
        "current": current,
        "ours": ours,
        "declared": bool(table.system_proxy_enabled),
        "pac_path": str(pac),
        "pac_advertised": bool(str(current.get("AutoConfigURL") or "")),
        "watchdog": _watchdog().status(home=home),
        "backup_exists": _backup_path(home).exists(),
        "engine_port": port,
    }


#: Always-direct, same list the PAC hard-codes above.
_ALWAYS_DIRECT = ("localhost", "*.local", "*.lan", "127.0.0.1", "[::1]",
                  "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16")


def desktop_view(table: Optional[Table] = None, *, home: str | Path | None = None) -> dict[str, Any]:
    """The table, in Chromium's dialect — what Electron's own sessions should do.

    Why this exists next to the PAC rather than instead of it: ``session.setProxy`` does not
    execute our PAC, and the desktop has traffic of its own (the window, the embed session, the
    remote-OAuth session) that must not quietly follow the machine's settings instead of the
    table.  Chromium's bypass list is *richer* than WinINET's (it takes CIDR and IPv6), so the
    rules ``ProxyOverride`` could not say are said here, and nothing is narrowed on the way.

    It re-decides nothing (裁定 94): ``declared`` is the table's own takeover switch, the port
    is the engine's mixed port, and ``mode`` only says whether we can hand traffic to that port
    at all.  The desktop still follows the system when the table has not taken over.
    """
    table = table if table is not None else load_table()
    port = table.engine_mixed_port
    out: dict[str, Any] = {"declared": bool(table.system_proxy_enabled), "mode": "system",
                           "proxy": "", "bypass": [], "unexpressible": [], "note": ""}

    if not out["declared"]:
        out["note"] = "表没接管系统代理：桌面跟着系统设置走"
        return out
    if not port:
        out["mode"] = "off"
        out["note"] = "接管声明了但 engine.mixed_port 没写：桌面不许在没有出口可交的情况下直连"
        return out

    bypass = list(_ALWAYS_DIRECT)
    for rule in table.rules:
        if rule.via != "direct":
            continue
        if rule.kind in ("geosite", "all"):
            # A `geosite:` classification or a catch-all cannot be said as a bypass — the engine
            # is the one that can answer those, so they stay handed over. Named, never dropped.
            out["unexpressible"].append(rule.match)
            continue
        if rule.kind == "exact":
            bypass.append(rule.value)
        elif rule.kind == "suffix":
            bypass.append(f"*.{rule.value}")
        elif rule.kind == "cidr":
            try:
                net = ipaddress.ip_network(rule.value, strict=False)
            except ValueError:
                out["unexpressible"].append(rule.match)
                continue
            bypass.append(str(net) if net.version == 4 else f"[{net}]")
        else:
            out["unexpressible"].append(rule.match)

    out["mode"] = "engine"
    out["proxy"] = f"http://127.0.0.1:{port}"
    out["bypass"] = list(dict.fromkeys(bypass))
    if out["unexpressible"]:
        out["note"] = f"{len(out['unexpressible'])} 条 direct 规则 Chromium 也说不清，交给引擎按同一张表判"
    return out


def status_lines(info: dict[str, Any]) -> list[str]:
    """The same reading in prose, for the CLI (裁定 94: one reading, several renderings)."""
    if not info.get("supported"):
        return [f"system proxy: 这一格目前只实现 Windows（这台是 {info.get('current', {}).get('unsupported')}）"]
    state = "Plobi 在管" if info["ours"] else ("表里说要接管，但注册表里不是我们的 PAC" if info["declared"] else "不是 Plobi（交还给系统/别的程序）")
    out = [f"system proxy: {state}"]
    cur = info.get("current") or {}
    out.append(
        f"  系统现在: ProxyEnable={cur.get('ProxyEnable', 0)} "
        f"ProxyServer={cur.get('ProxyServer') or '—'} AutoConfigURL={cur.get('AutoConfigURL') or '—'}"
    )
    out.append(f"  我们的 PAC: {info['pac_path']}{'' if Path(info['pac_path']).exists() else '（还没生成）'}")
    out.append(
        "  PAC 挂在系统上吗: %s（挂上 WinINET 的 HTTPS 会全断，所以只留文件不挂）"
        % ("是——建议摘掉" if info.get("pac_advertised") else "没有")
    )
    dog = info.get("watchdog") or {}
    if dog.get("alive"):
        age = dog.get("age_seconds")
        out.append(
            "  看门狗: 在（pid %s，%s）"
            % (dog.get("pid"), "刚看过 %0.0fs 前" % age if isinstance(age, (int, float)) else "还没跑过")
        )
    else:
        out.append("  看门狗: 没有——引擎掉了没人拉（`plobi network proxy on` 会重新起一个）")
    out.append(f"  接管前那份备份: {'在' if info['backup_exists'] else '没有'}（off 靠它原样还原）")
    if info["declared"] and not info["ours"]:
        out.append("  ⚠️ 表里 system_proxy.enabled 是 true，但系统代理不是我们的 —— 跑：plobi network proxy on")
    return out
