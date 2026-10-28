"""Serve the machine's PAC over ``http://`` — WinINET will not load a ``file://`` one.

Measured on this box (2026-10-25, WP-EGRESS-ROUTING): with the **static** proxy
pointed at a dead port and ``AutoConfigURL`` pointing at our ``file:///`` PAC,
WinINET used the *static* entry and ignored the PAC; with the very same PAC
served over ``http://``, WinINET used the PAC.  So the PAC we wrote to disk was
documentation for WinINET, not a route it takes — the machine was actually being
carried by the static entry alone.

Two things this server has to get right:

* **It outlives whoever enabled the proxy.**  The engine is spawned detached for
  the same reason; a PAC server that dies with the CLI would leave the registry
  pointing at a dead port (measured: WinINET then falls back to the static entry,
  so nothing leaks — but the PAC is pointless again).
* **It recomputes the PAC per request** from the live table, so editing the
  table does not need a restart, and the PAC cannot drift from the table it
  claims to describe (裁定 94: one source of truth).

The server binds ``127.0.0.1`` only, and answers two paths: the PAC itself and
a health marker used to decide whether it is already up.
"""

from __future__ import annotations

import http.server
import json
import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

#: The marker a healthy PAC server answers with — used to tell "ours" from "a
#: stranger holding the port".
HEALTH_MARKER = "plobi-egress-pac/1"
HEALTH_PATH = "/healthz"
PAC_PATH = "/plobi-egress.pac"
LOG_BASENAME = "pac-server.log"
RECORD_BASENAME = "pac-server.json"


def repo_root() -> Path:
    """Where ``agent.egress.pac_server`` lives, so the child can import it."""
    return Path(__file__).resolve().parents[2]


def _home(home: str | Path | None = None) -> Path:
    from agent.egress.engine import egress_dir

    return egress_dir(home)


def _record_path(home: str | Path | None = None) -> Path:
    return _home(home) / RECORD_BASENAME


def _log_path(home: str | Path | None = None) -> Path:
    return _home(home) / LOG_BASENAME


def url_for(port: int) -> str:
    return "http://127.0.0.1:%d%s" % (port, PAC_PATH)


def _live_table():
    """The table as it is **now** — not the one that was loaded when we started."""
    from agent.egress.rules import load_table

    return load_table()


def _pac_body() -> bytes:
    from agent.egress.system_proxy import pac_text

    table = _live_table()
    return pac_text(table, table.engine_mixed_port).encode("utf-8")


class _Handler(http.server.BaseHTTPRequestHandler):
    server_version = "PlobiEgressPac/1"

    def do_GET(self) -> None:  # noqa: N802 - http.server's naming
        path = self.path.split("?", 1)[0]
        if path == HEALTH_PATH:
            body = HEALTH_MARKER.encode("ascii")
            ctype = "text/plain"
        elif path == PAC_PATH:
            try:
                body = _pac_body()
            except Exception as exc:  # a table we cannot read is a 500, not a wrong PAC
                log.warning("pac server: cannot render the PAC: %s", exc)
                self.send_error(500, "cannot render the PAC")
                return
            ctype = "application/x-ns-proxy-autoconfig"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:
        log.debug("pac server: " + fmt, *args)


def health(port: int, *, timeout: float = 1.0) -> tuple[bool, str]:
    """Is a PAC server of ours answering on that port?"""
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, HEALTH_PATH),
                                    timeout=timeout) as resp:
            body = resp.read().decode("ascii", "replace").strip()
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return False, "%s: %s" % (type(exc).__name__, exc)
    if body != HEALTH_MARKER:
        return False, "port %d answered %r, which is not ours" % (port, body)
    return True, "PAC 服务在 %d 上应答" % port


def serve(port: int) -> None:
    """Run the server in the foreground (this is what the detached child does)."""
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    httpd.daemon_threads = True
    log.warning("pac server: listening on 127.0.0.1:%d", port)
    httpd.serve_forever()


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="agent.egress.pac_server")
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        serve(args.port)
    except KeyboardInterrupt:
        return 0
    return 0


def _read_record(home: str | Path | None = None) -> Optional[dict[str, Any]]:
    path = _record_path(home)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return None
    return raw if isinstance(raw, dict) else None


def _write_record(rec: dict[str, Any], home: str | Path | None = None) -> None:
    path = _record_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rec, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _clear_record(home: str | Path | None = None) -> None:
    try:
        _record_path(home).unlink()
    except FileNotFoundError:
        pass


def _pid_is_ours(rec: dict[str, Any]) -> tuple[bool, str]:
    """Is the recorded pid still the PAC server we started?

    Windows recycles pids, so the name and the creation time both have to match.
    """
    try:
        import psutil
    except ImportError:
        return False, "psutil 不可用，没法确认这个 pid 是不是我们的"
    pid = rec.get("pid")
    if not isinstance(pid, int):
        return False, "记录里没有可用的 pid"
    try:
        proc = psutil.Process(pid)
        cmdline = " ".join(proc.cmdline())
        created = proc.create_time()
    except Exception as exc:  # psutil.Error and plain OS errors both mean "gone"
        return False, "pid %s 已经不在了（%s）" % (pid, type(exc).__name__)
    if "pac_server" not in cmdline:
        return False, "pid %s 现在是别的东西（%s）" % (pid, cmdline[:120])
    if abs(created - float(rec.get("created", 0.0))) > 1.0:
        return False, "pid %s 的创建时间与我们记的不一样" % pid
    return True, "pid %d 是我们起的 PAC 服务" % pid


def start(port: int, *, home: str | Path | None = None, timeout: float = 6.0) -> tuple[bool, str]:
    """Make sure a PAC server of ours is answering on ``port``.

    Returns ``(ok, reason)``; ``ok`` is False when we could not get one up, and the
    caller must then fall back to the file — and **say so**, because a silent
    narrowing is the 裁定 94 failure again.
    """
    alive, detail = health(port)
    if alive:
        record = _read_record(home)
        if record and record.get("port") != port:
            # The engine moved, so our PAC moved with it — do not leave the old one running.
            stop(home=home)
        else:
            return True, detail

    record = _read_record(home)
    if record:
        ours, why = _pid_is_ours(record)
        if ours:
            stop(home=home)
        elif record.get("port") != port:
            log.info("pac server: 旧记录不是我们的了（%s）——不管它", why)

    cmd = [sys.executable, "-m", "agent.egress.pac_server", "--port", str(port)]
    flags = 0
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    log_path = _log_path(home)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(log_path, "ab")  # noqa: SIM115 - the child keeps the handle
    try:
        proc = subprocess.Popen(  # noqa: S603 - our own interpreter and our own module
            cmd,
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            cwd=str(repo_root()),
            creationflags=flags,
            **kwargs,
        )
    except OSError as exc:
        return False, "起 PAC 服务失败（%s: %s）" % (type(exc).__name__, exc)
    finally:
        handle.close()

    _write_record({"pid": proc.pid, "created": time.time(), "port": port,
                   "binary": sys.executable, "cmd": cmd}, home=home)

    deadline = time.time() + timeout
    while time.time() < deadline:
        alive, detail = health(port, timeout=0.5)
        if alive:
            return True, detail
        time.sleep(0.15)
    return False, "PAC 服务起来了但没应答（pid %d，日志 %s）" % (proc.pid, log_path)


def _terminate_tree(proc: Any, psutil: Any) -> list:
    """Kill the recorded process **and its children**.

    On Windows a venv's ``python.exe`` is a launcher that re-execs the base
    interpreter as a child, so the pid we recorded is not the one holding the
    port (measured 10-26: pid 37696 → child 37736 owned :7896).  Terminating only
    the parent leaves a server behind that nothing can stop.
    """
    family = []
    try:
        family = proc.children(recursive=True)
    except Exception:
        family = []
    family = family + [proc]
    for p in family:
        try:
            p.terminate()
        except Exception:
            pass
    try:
        psutil.wait_procs(family, timeout=3)
    except Exception:
        pass
    for p in family:
        try:
            if p.is_running():
                p.kill()
        except Exception:
            pass
    return family


def stop(*, home: str | Path | None = None) -> tuple[bool, str]:
    """Stop the PAC server we started — and only that one."""
    record = _read_record(home)
    if not record:
        return False, "没有 PAC 服务的记录，没东西可停"
    ours, why = _pid_is_ours(record)
    if not ours:
        _clear_record(home=home)
        return False, "记录的 PAC 服务已经不是我们的了（%s）——没碰它" % why
    try:
        import psutil
        proc = psutil.Process(record["pid"])
        family = _terminate_tree(proc, psutil)
    except Exception as exc:  # already gone counts as stopped
        _clear_record(home=home)
        return True, "PAC 服务本来就不在了（%s）" % type(exc).__name__
    _clear_record(home=home)
    port = record.get("port")
    if isinstance(port, int) and port:
        deadline = time.time() + 3.0
        while time.time() < deadline:
            alive, _ = health(port, timeout=0.3)
            if not alive:
                return True, "已停掉 PAC 服务（pid %d，共 %d 个进程）" % (record["pid"], len(family))
            time.sleep(0.15)
        return False, "已杀 pid %d 及其 %d 个子进程，但 %d 上还有东西在应答——去查是谁" % (
            record["pid"], len(family) - 1, port)
    return True, "已停掉 PAC 服务（pid %d，共 %d 个进程）" % (record["pid"], len(family))


def status(port: int, *, home: str | Path | None = None) -> dict[str, Any]:
    """What the machine's PAC path looks like right now."""
    alive, detail = health(port)
    record = _read_record(home)
    return {
        "port": port,
        "url": url_for(port),
        "alive": alive,
        "detail": detail,
        "pid": (record or {}).get("pid"),
        "recorded": bool(record),
    }


if __name__ == "__main__":  # the detached child runs exactly this
    sys.exit(main())
