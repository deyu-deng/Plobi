"""Standalone system-proxy guard for Plobi egress (WP-EGRESS-ROUTING, 11-01).

Why this exists, and why it is *detached* from Plobi:

  On Windows, closing Plobi hard-kills the whole process tree (`taskkill /T /F`) —
  the desktop, the backend, the egress engine **and the in-tree watchdog** all die
  in the same instant, so nobody restores the system proxy. The machine is left
  pointing at ``127.0.0.1:<mixed_port>`` with nothing listening, and every app that
  reads the system proxy (browsers, DingTalk, Marvis) reads "no internet".

  This script is spawned from :func:`agent.egress.system_proxy.enable` with
  ``DETACHED_PROCESS``, so it is NOT in Plobi's process tree and survives the
  tree-kill. It polls the registry: if Plobi's *loopback* proxy is still set but
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


def _kill_old_guard(pid_path: Path) -> None:
    """One guard per machine: if an older instance is alive, replace it."""
    if not pid_path.exists():
        return
    try:
        old = int(pid_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return
    if old == os.getpid():
        return
    try:
        subprocess.run(
            ["taskkill", "/PID", str(old), "/T", "/F"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError:
        try:
            os.kill(old, 9)
        except OSError:
            pass


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
    p.add_argument("--once", action="store_true", help="check once then exit (for tests/manual runs)")
    args = p.parse_args(argv)

    home = args.home
    runtime = sp._home(home) / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    _setup_logging(runtime)

    pid_path = runtime / "proxy-guard.pid"
    _kill_old_guard(pid_path)
    pid_path.write_text(str(os.getpid()), encoding="utf-8")

    port = sp.load_table().engine_mixed_port
    target = f"127.0.0.1:{port}" if port else ""
    log.warning("proxy guard: started (pid %s, watching %s)", os.getpid(), target or "n/a")

    while True:
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
            sp._write_registry(plan)
            sp._notify_wininet()
            if bp.exists():
                bp.unlink(missing_ok=True)
            try:
                sp._watchdog().stop(home=home)
            except Exception:
                pass
            log.warning(
                "proxy guard: restored system proxy (was %s) -> %s and exiting",
                current.get("ProxyServer"), plan,
            )
            return 0

        if args.once:
            return 0
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
