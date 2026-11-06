"""Standalone system-proxy guard for Plobi egress (WP-EGRESS-ROUTING, 11-01).

Why this exists, and why it is *detached* from Plobi:

  On Windows, closing Plobi hard-kills the whole process tree (`taskkill /T /F`) —
  the desktop, the backend, the egress engine **and the in-tree watchdog** all die
  in the same instant, so nobody restores the system proxy. The machine is left
  pointing at ``127.0.0.1:<mixed_port>`` with nothing listening, and every app that
  reads the system proxy (browsers, DingTalk, Marvis) reads "no internet".

This script is spawned from :func:`agent.egress.system_proxy.enable` through a
``cmd /c start`` shim that exits immediately, so the guard is a **true orphan**
— its parent PID is already dead when Plobi quits, and ``taskkill /T /F`` (which
walks the *live* process tree) cannot reach it.  (Measured 11-04: spawning it
directly with ``DETACHED_PROCESS`` was **not** enough — that detaches the
console, not the parent/child link, so the guard was still a descendant of the
backend and was reaped with it, every single time.  The old comment here
claimed otherwise and was simply wrong.)

It polls the registry: if Plobi's *loopback* proxy is still set but
its port is dead, it hands the machine back — to the pre-takeover value, or to
DIRECT when that too is a dead loopback (a long-uninstalled peer client's stale
port) — then exits. A dead port means no exit, which reads as "no internet", so
restoring is always the safe choice.

This is complementary to the Electron ``before-quit`` handler (which restores
instantly on a clean quit) — the guard covers the hard-kill / crash case, and also
the "user just yanks the power / kills the terminal" case that ``before-quit``
never sees.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

log = logging.getLogger("plobi.egress.proxy_guard")

POLL_SECONDS = 5
REG_VALUES = ("ProxyEnable", "ProxyServer", "AutoConfigURL", "ProxyOverride")

#: The one guard per machine. A named mutex lives exactly as long as the process
#: that created it, so it says "one per machine" without ever naming a pid.
_MUTEX_NAME = "PlobiProxyGuardSingleton"
_MUTEX_HANDLE = None


def _code_root() -> Path:
    # proxy_guard.py lives at <code>/agent/egress/proxy_guard.py
    return Path(__file__).resolve().parents[2]


def _ensure_importable() -> None:
    root = _code_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


def _probe_dead(server: str) -> bool:
    """True only if ``server`` is a **loopback** ``host:port`` nothing is listening on.

    Used as the restore gate: a loopback proxy whose port is dead reads, from every
    app's point of view, as "no internet".
    """
    raw = (server or "").strip()
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
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.6)
    try:
        return sock.connect_ex((host or "127.0.0.1", int(port))) != 0
    except OSError:
        return True
    finally:
        sock.close()


def plan_restore(current: dict, backup: dict | None, probe=_probe_dead) -> dict | None:
    """Decide what to write to the registry, or ``None`` if nothing should change.

    Only touches a *loopback* proxy whose port is dead (`probe` returns True): a dead
    port means no exit, which reads as "no internet" to every app. The machine is
    restored to the pre-takeover value, or to DIRECT when that too is a dead loopback
    (e.g. a long-uninstalled peer client's stale ``127.0.0.1:7890``).
    """
    enabled = int(current.get("ProxyEnable") or 0) == 1
    server = str(current.get("ProxyServer") or "")
    if not enabled or not server:
        return None
    if not server.startswith(("127.0.0.1:", "localhost:", "[::1]:")):
        return None  # not our loopback proxy; a real remote proxy is the user's own config
    if not probe(server):
        return None  # engine still up — leave the takeover in place

    if backup:
        restore = {k: backup.get(k) for k in REG_VALUES if k in backup}
    else:
        restore = {k: None for k in REG_VALUES}
    # Fail-safe: if the saved "before" was itself a *dead loopback* (e.g. a long
    # uninstalled peer client's stale 127.0.0.1:7890), DIRECT is the only honest
    # answer. A real remote proxy (10.x, a corporate PAC host) is the user's own
    # config and is trusted verbatim — we never decide their network for them.
    if restore.get("ProxyEnable"):
        bs = str(restore.get("ProxyServer") or "")
        if bs.startswith(("127.0.0.1:", "localhost:", "[::1]:")) and probe(bs):
            restore = {"ProxyEnable": 0, "ProxyServer": None}
    if not restore.get("ProxyEnable"):
        restore = {"ProxyEnable": 0, "ProxyServer": None}
    return restore


def _engine_running() -> bool:
    """True if the egress engine (mihomo) is still a live process.

    Used to avoid clobbering the takeover during a *legitimate* engine restart: a
    brief dead-port window there should not be read as "Plobi is gone, hand back".
    """
    try:
        out = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "mihomo" in out.lower()


def _ancestors(pid: int) -> set[int]:
    """This process and its parents — the ones we must never kill."""
    try:
        import psutil

        proc = psutil.Process(pid)
    except Exception:  # noqa: BLE001
        return {pid}
    out: set[int] = set()
    while proc is not None:
        out.add(proc.pid)
        try:
            proc = proc.parent()
        except Exception:  # noqa: BLE001
            break
    return out


def _claim_singleton() -> bool:
    """True if **this** process is the one guard for this machine.

    A named mutex is the honest way to say "one per machine": the object exists
    exactly as long as the process that created it, every other process can see
    it, and it can never be confused with a recycled pid.

    The pid-file approach kept failing on both counts (measured 11-04): the file
    names a pid that has long since died and been recycled by Windows, so killing
    it took down innocent bystanders — twice including our own launcher — and it
    also cannot see the *other* half of the problem: the venv's ``python.exe`` is
    a launcher stub that spawns the real interpreter as a child, so "the guard"
    is two processes and killing the stub with ``/T`` kills the worker with it.
    """
    global _MUTEX_HANDLE
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
        if not handle:
            return True  # cannot tell — better to guard than to not guard
        if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            kernel32.CloseHandle(handle)
            return False
        _MUTEX_HANDLE = handle
        return True
    except Exception:  # noqa: BLE001 - a guard that cannot claim still tries to guard
        return True


def _kill_old_guard(pid_path: Path) -> None:
    """Retire any guard that is not us.

    ``taskkill`` is used **without** ``/T`` on purpose: the venv's ``python.exe`` is
    a launcher stub whose child is the real worker, so a tree-kill of the stub
    takes the worker down with it — which is how the guard that had just started
    kept killing itself (measured 11-04).  Killing the worker itself is enough;
    the stub exits on its own once its child is gone.
    """
    try:
        import psutil
    except ImportError:
        return
    mine = _ancestors(os.getpid())
    for proc in psutil.process_iter(["pid", "cmdline"]):
        pid = proc.info.get("pid")
        if pid is None or pid in mine:
            continue
        cmdline = " ".join(proc.info.get("cmdline") or [])
        if "proxy_guard.py" not in cmdline:
            continue
        parts = cmdline.strip().split()
        if parts and parts[0].lower().endswith("cmd.exe"):
            continue  # the spawn shim, not a guard
        if "--once" in parts:
            continue  # the scheduled stateless check — short-lived, not a rival guard
        _kill_pid(pid)


def _kill_pid(pid: int) -> None:
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError:
        try:
            os.kill(pid, 9)
        except OSError:
            pass


def _user_quit_since(sp, home, started_at: float) -> bool:
    """Did the desktop mark this session as a deliberate quit? (WP-QUIT-CLEAN, 11-05)

    The 1s tolerance is not cosmetic: the marker is written just before the tree-kill while this
    guard started earlier in the same session, but the marker's timestamp comes from the desktop's
    clock and ``started_at`` from ours.  Anything older than that belongs to a previous session and
    must not retire a guard that is doing its job.
    """
    at = sp.quit_marker_since(home)

    return at is not None and at >= started_at - 1.0


def _retire_on_user_quit(sp, home, runtime: Path) -> None:
    """Hand the machine back, remove our pid file, then leave.

    The pid file goes **first**: ``disable()`` ends with ``_stop_guard()``, which taskkills whatever
    pid that file names — and a taskkill triggered from inside our own process would cut this
    function off before the file was removed, leaving exactly the stale-pid residue (38384) this
    fix exists for.  With the file already gone, ``_stop_guard`` finds nothing and returns.
    """
    pid_path = runtime / "proxy-guard.pid"
    try:
        pid_path.unlink(missing_ok=True)
    except OSError:
        pass
    try:
        _ok, detail = sp.disable(home=home)
    except Exception as exc:  # noqa: BLE001 - a best-effort hand-back, never a crash on exit
        log.warning("proxy guard: 用户退出但交还出错（%s:%s）——留给系统代理的下一个读取者",
                    type(exc).__name__, exc)
        return
    log.warning("proxy guard: 用户主动退出 → %s；本进程退出，pid 文件已清", detail)


def _setup_logging(runtime: Path) -> None:
    try:
        runtime.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(runtime / "proxy-guard.log", encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(fh)
        log.setLevel(logging.WARNING)
    except OSError:
        pass


def main(argv=None) -> int:
    _ensure_importable()
    from agent.egress import system_proxy as sp

    p = argparse.ArgumentParser()
    p.add_argument("--home", default=None)
    p.add_argument("--once", action="store_true",
                   help="one stateless check then exit — the scheduled-task safety net")
    args = p.parse_args(argv)

    home = args.home
    runtime = sp._home(home) / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    _setup_logging(runtime)

    if args.once:
        # **Stateless sanity check.** This is the Windows-scheduled fallback: it is
        # spawned by the Task Scheduler, outside Plobi's process tree, so no
        # tree-kill / Job close / crash can take it with the app — and because it
        # runs afresh every few minutes, it heals itself after any single failure.
        # It must therefore leave NO state behind: no mutex (a stuck perpetual
        # guard must not be able to block the check), no pid file (writing ours
        # would clobber the real guard's pid and break `_stop_guard`), no process
        # sweep, no "started" line every five minutes.
        # It also does not read the config table: the decision needs only the
        # registry, the backup file and a port probe, so it keeps working even
        # when Plobi's .env credentials are not loaded in this process.
        pass
    else:
        pid_path = runtime / "proxy-guard.pid"
        if not _claim_singleton():
            log.warning("proxy guard: 已经有守卫在跑（互斥体被占），本进程退出")
            return 0
        _kill_old_guard(pid_path)
        pid_path.write_text(str(os.getpid()), encoding="utf-8")

        # Defensive: if the table cannot be read at startup (malformed config, racing
        # write), still run — a dead loopback proxy is restored by reading the registry,
        # not the table, so the guard must not die here.
        try:
            port = sp.load_table().engine_mixed_port
        except Exception as exc:  # noqa: BLE001 - survive a bad read, keep guarding
            log.warning("proxy guard: load_table failed (%s:%s) — watching without target port",
                        type(exc).__name__, exc)
            port = 0
        target = f"127.0.0.1:{port}" if port else ""
        log.warning("proxy guard: started (pid %s, watching %s)", os.getpid(), target or "n/a")

    # `--once` is for manual runs / tests: check a single pass then exit.
    # The detached guard (no --once) is PERPETUAL and crash-resilient: it keeps
    # polling so one launch covers every future up/down cycle, and a transient
    # error (e.g. a registry lock) never kills it. It only leaves when disable()
    # kills it via the pid file (a clean `proxy off`) — or when the desktop has left a **user-quit
    # marker** (WP-QUIT-CLEAN, 11-05), which is the only way a deliberate quit ever reaches a guard
    # whose parent is an intentionally dead `cmd /c start` shim.
    started_at = time.time()

    while True:
        if not args.once and _user_quit_since(sp, home, started_at):
            _retire_on_user_quit(sp, home, runtime)

            return 0
        try:
            current = sp.read_current()
            backup = None
            bp = sp._backup_path(home)
            if bp.exists():
                try:
                    backup = json.loads(bp.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    backup = None

            plan = plan_restore(current, backup, _probe_dead)
            if plan is not None:
                # Don't hand back the machine while the engine is mid-restart — only
                # when Plobi/egress is genuinely gone (covers the legit restart blip).
                if _engine_running():
                    pass
                else:
                    sp._write_registry(plan)
                    sp._notify_wininet()
                    if bp.exists():
                        bp.unlink(missing_ok=True)
                    # Only the perpetual guard retires the watchdog: it is the one
                    # that takes over supervision when Plobi is genuinely gone. The
                    # scheduled `--once` check must leave the watchdog alive — it is
                    # the component that can still bring the engine (and therefore
                    # the takeover) back.
                    if not args.once:
                        try:
                            sp._watchdog().stop(home=home)
                        except Exception:
                            pass
                    log.warning(
                        "proxy guard: restored system proxy (was %s) -> %s",
                        current.get("ProxyServer"), plan,
                    )
                    if args.once:
                        return 0
                    # Perpetual: keep watching for the next time Plobi goes away.
        except Exception as exc:  # noqa: BLE001 - survive a bad tick, keep guarding
            log.warning("proxy guard: tick error (%s:%s) — retrying", type(exc).__name__, exc)

        if args.once:
            return 0
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
