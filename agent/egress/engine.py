"""Start, watch and stop the engine the egress table may point at.

``network.egress.engine.enabled`` used to be a key nothing read: an engine could
only be running because somebody started it by hand, which is not a product
capability.  This module is the one reader of that switch.  It deliberately does
three things and nothing else — find out who owns the listen port, bring the
engine up if nobody does, and answer *"is it actually carrying traffic?"* by
shooting a request with a known answer through it.

Two measured facts on this box (mihomo v1.19.32, 2026-10-12) shape the design:

* **A listening port is not a working exit.**  ``netstat`` says a port is open
  while the engine behind it returns nothing useful.  So :func:`probe_engine`
  asks ``http://www.gstatic.com/generate_204`` through the engine and expects
  the ``204`` it only gives when it really reached the far end.
* **A node pool is a workdir, not a config key.**  ``include:`` is ignored by the
  engine (see :mod:`agent.egress.mihomo_config`), so when the table references a
  pool file we start ``mihomo -d <the directory holding that file>`` and never
  rewrite the pool.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import socket
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import yaml

from agent.egress.mihomo_config import CONFIG_BASENAME, compile_config, verify_with_binary
from agent.egress.rules import Table, load_table

#: The known answer.  ``generate_204`` returns 204 with an empty body only if the
#: request actually left the machine; a captive portal or a dead proxy gives
#: anything else.
PROBE_URL = "http://www.gstatic.com/generate_204"
PROBE_EXPECTED = 204

STATE_BASENAME = "plobi-engine.json"
RUNTIME_BASENAME = "runtime"

_log = logging.getLogger(__name__)

OFF = "off"
OURS = "running-ours"
FOREIGN = "running-foreign"
STOPPED = "stopped"
FAILED = "failed"

_POOL_OWNED = ("mixed-port", "port", "socks-port")


@dataclass(frozen=True)
class EngineReport:
    """What the engine looks like from the product's side.

    :meth:`lines` exists so the visible surface prints *this* object rather than
    re-deriving it — 裁定 94 forbids a second, narrower reader of the same truth.
    """

    state: str
    detail: str
    mixed_port: int = 0
    workdir: str = ""
    config_source: str = ""
    pid: Optional[int] = None
    probe: str = ""
    notes: tuple[str, ...] = ()

    def lines(self) -> list[str]:
        out = [f"engine: {self.state} — {self.detail}"]
        if self.mixed_port:
            out.append(f"listen: 127.0.0.1:{self.mixed_port} ({self.config_source or 'no config'})")
        if self.pid:
            out.append(f"pid: {self.pid}")
        if self.probe:
            out.append(f"probe: {self.probe}")
        out.extend(f"note: {n}" for n in self.notes)
        return out


@dataclass(frozen=True)
class _Snapshot:
    enabled: bool
    binary: str
    port: int
    workdir: Optional[Path]
    config_path: Optional[Path]
    source: str
    record: Optional[dict]
    notes: tuple[str, ...] = ()


def egress_dir(home: str | Path | None = None) -> Path:
    return Path(home or _default_home()) / "egress"


def _default_home() -> str:
    from plobi_constants import get_plobi_home

    return get_plobi_home()


def _state_path(home: str | Path | None = None) -> Path:
    return egress_dir(home) / STATE_BASENAME


def _read_record(home: str | Path | None = None) -> Optional[dict]:
    path = _state_path(home)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        # A record we cannot parse is not "no engine of ours"; say so rather than
        # starting a second one on top of the first.
        return {"unreadable": f"{path}: {type(exc).__name__}: {exc}"}
    return raw if isinstance(raw, dict) else {"unreadable": f"{path}: not a mapping"}


def _write_record(rec: dict, home: str | Path | None = None) -> None:
    path = _state_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rec, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _clear_record(home: str | Path | None = None) -> None:
    try:
        _state_path(home).unlink()
    except FileNotFoundError:
        pass


_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}

#: The known answer and the patience it needs.  ``ensure``/``status`` may wait
#: ``PROBE_TIMEOUT``; a **spawn path** may not — a command should not stall 8s
#: behind a proxy that is never going to answer, so it waits ``SPAWN_PROBE_TIMEOUT``.
PROBE_TIMEOUT = 8.0
SPAWN_PROBE_TIMEOUT = 1.5
#: How long a verdict may be reused across spawns before we shoot again.
REACH_TTL = 5.0

UP = "up"
DOWN = "down"
UNKNOWN = "unknown"

#: (verdict, reason) per local exit, with the moment it was taken.
_reach_cache: dict[tuple[str, int], tuple[str, str, float]] = {}


def _probe_once(
    port: int, *, url: str = PROBE_URL, timeout: float = PROBE_TIMEOUT
) -> tuple[str, str]:
    """Ask the exit its known question and return **three** states, not two.

    ``up`` is only the known answer (204).  Anything we got back that is not it —
    a refusal, a reset, a 200 from something that is not the far end — is ``down``,
    because the exit is demonstrably not carrying traffic the way the table assumes.
    A **timeout is ``unknown``, and ``unknown`` keeps the table in force**:
    downgrading a slow-but-alive engine would send children direct precisely when
    direct is the route the user said not to use, which is worse than the bug we
    set out to fix.  (用户 10-12 拍的就是这个三态，不是两值。)
    """
    proxy = f"http://127.0.0.1:{port}"
    if port <= 0:
        return UNKNOWN, "no listen port declared (network.egress.engine.mixed_port)"

    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy})
    )
    try:
        with opener.open(url, timeout=timeout) as resp:
            status = resp.status
    except Exception as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, (TimeoutError, OSError)) and not isinstance(reason, ConnectionError):
            # socket.timeout is an alias of TimeoutError; a bare 10060 also lands here.
            return UNKNOWN, f"timed out after {timeout}s via {proxy}"
        return DOWN, f"via {proxy}: {type(exc).__name__} {str(exc)[:80]}"
    if status == PROBE_EXPECTED:
        return UP, f"{status} via {proxy}"
    return DOWN, f"{status} via {proxy} (expected {PROBE_EXPECTED})"


def probe_engine(port: int, *, url: str = PROBE_URL, timeout: float = PROBE_TIMEOUT) -> tuple[bool, str]:
    """Ask the engine a question with a known answer — the two-state view.

    Returns ``(ok, detail)`` and never raises: a supervision path that dies
    because a probe timed out would take the product down with it.
    """
    if port <= 0:
        return False, "no listen port declared (network.egress.engine.mixed_port)"
    verdict, detail = _probe_once(port, url=url, timeout=timeout)
    return verdict == UP, detail


def exit_verdict(proxy_url: str, *, ttl: float = REACH_TTL) -> tuple[str, str]:
    """``('up'|'down'|'unknown', why)`` for an exit we are about to hand a child.

    Only an exit on **this machine** is judged: that is the case that silently
    poisons every spawn when the engine is gone, and it is the one we can test in
    0.4s without touching somebody else's proxy.  A remote exit passes through
    unprobed — ``listening`` could not tell unreachability from a firewall anyway.

    Measured on this box, 2026-10-12 09:3x: the table pointed every child at
    ``http://127.0.0.1:7895`` while no mihomo process existed (count 0), and
    direct fetches to ``github.com`` / ``www.google.com`` returned **200** — the
    table was making Plobi worse than no table.  That is what this notices.
    """
    parsed = urlparse(proxy_url)
    host = (parsed.hostname or "").lower()
    if host not in _LOOPBACK_HOSTS:
        return UP, f"{proxy_url} is not a local exit — not probed"
    port = parsed.port or 0
    key = (host, port)
    now = time.monotonic()
    cached = _reach_cache.get(key)
    if cached is not None and now - cached[2] < ttl:
        return cached[0], f"cached {ttl:.0f}s: {cached[1]}"
    if not listening(port):
        verdict, why = DOWN, f"nothing is listening on {host}:{port}"
    else:
        verdict, why = _probe_once(port, timeout=SPAWN_PROBE_TIMEOUT)
    _reach_cache[key] = (verdict, why, now)
    return verdict, why


def exit_reachable(proxy_url: str, *, ttl: float = REACH_TTL) -> tuple[bool, str]:
    """Two-state view of :func:`exit_verdict` — ``False`` only on a refusal."""
    verdict, why = exit_verdict(proxy_url, ttl=ttl)
    return verdict != DOWN, why


def reset_reach_cache() -> None:
    _reach_cache.clear()


#: Downgrades decided by :func:`usable_exit`, newest last, capped so a dead exit
#: cannot grow memory.  The visible surface reads :func:`recent_downgrades` — a
#: downgrade that only appears in a log file is not one the user can see (裁定 94).
_DOWNGRADES: list[dict[str, str]] = []
_MAX_DOWNGRADES = 20
#: Exits already recorded as down, so a second failure after a recovery is said
#: out loud again instead of being swallowed by "same text as last time".
_currently_down: set[str] = set()


def recent_downgrades() -> list[dict[str, str]]:
    return list(_DOWNGRADES)


def reset_downgrades() -> None:
    _DOWNGRADES.clear()
    _currently_down.clear()


def usable_exit(proxy_url: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """The exit to hand a caller, after liveness.  ``(None, reason)`` = go direct.

    One reader for both consumers — spawned children
    (:mod:`agent.egress.env_projection`) and our own in-process HTTP clients
    (``agent.process_bootstrap``) — because the same dead port has to produce the
    same answer in both, and 裁定 94 forbids a second, narrower reading of it.

    Only ``DOWN`` steps aside.  ``UNKNOWN`` (a slow probe) keeps the table in
    force — downgrading a live-but-slow engine would send children out the route
    the table explicitly refuses.
    """
    if not proxy_url:
        return None, None
    verdict, why = exit_verdict(proxy_url)
    if verdict != DOWN:
        _currently_down.discard(proxy_url)
        return proxy_url, None
    if proxy_url not in _currently_down:
        _currently_down.add(proxy_url)
        _DOWNGRADES.append({"exit": proxy_url, "reason": why})
        del _DOWNGRADES[:-_MAX_DOWNGRADES]
        _log.warning(
            "network.egress: exit %s is not carrying (%s) — traffic is left unrouted rather than "
            "handed to a dead port",
            proxy_url,
            why,
        )
    return None, why


def _psutil():
    """Import seam: ownership checks are tested against a fake, not a real pid."""
    import psutil

    return psutil


def _pid_is_ours(rec: dict) -> tuple[bool, str]:
    """Is the process we recorded still that same process?

    A bare ``pid_exists`` is not enough: Windows recycles pids, so the check is
    name *and* creation time against what we recorded.
    """
    try:
        psutil = _psutil()
    except ImportError:
        return False, "psutil is unavailable, so ownership of the recorded pid cannot be checked"
    pid = rec.get("pid")
    if not isinstance(pid, int):
        return False, "record has no usable pid"
    try:
        proc = psutil.Process(pid)
        name = proc.name().lower()
        created = proc.create_time()
    except Exception as exc:  # psutil.Error and plain OS errors both mean "gone"
        return False, f"pid {pid} is not ours any more ({type(exc).__name__})"
    stem = os.path.basename(str(rec.get("binary", ""))).lower()
    if stem and name != stem:
        return False, f"pid {pid} is now '{name}', not '{stem}'"
    if abs(created - float(rec.get("created", 0.0))) > 1.0:
        return False, f"pid {pid} was created at a different time than we recorded"
    return True, f"pid {pid} ({name}) is the process we started"


def listening(port: int, *, host: str = "127.0.0.1", timeout: float = 0.4) -> bool:
    if port <= 0:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _pool_config_path(table: Table) -> Optional[Path]:
    """Where the engine's own ``config.yaml`` is, when the table references a pool.

    ``mihomo -d <dir>`` validates ``<dir>/config.yaml`` and nothing else, so a
    reference to some other filename is a refusal, not a guess.
    """
    if not table.engine_include:
        return None
    first = Path(table.engine_include[0])
    if first.name != CONFIG_BASENAME:
        candidate = first.parent / CONFIG_BASENAME
        if not candidate.exists() and first.exists():
            return None
        return candidate
    return first if first.exists() else None


def snapshot(table: Table, *, home: str | Path | None = None) -> _Snapshot:
    """Everything a caller needs to know, without starting or stopping anything."""
    notes: list[str] = []
    port = table.engine_mixed_port
    if not table.engine_enabled:
        return _Snapshot(False, table.engine_binary, port, None, None, "", None,
                         ("network.egress.engine.enabled is false",))
    binary = table.engine_binary
    if not binary:
        notes.append("network.egress.engine.binary is empty — nothing to start")
    elif not os.path.isfile(binary if os.path.isabs(binary) else _which(binary) or binary):
        notes.append(f"engine binary '{binary}' is not on disk — installing it is its own slice")

    pool = _pool_config_path(table)
    if table.engine_include and pool is None:
        return _Snapshot(True, binary, port, None, None, "pool", None,
                         (f"the pool reference '{table.engine_include[0]}' does not resolve to an "
                          f"existing <dir>/{CONFIG_BASENAME}; mihomo validates exactly that name",
                          *notes))
    if pool is not None:
        workdir = pool.parent
        source = "pool"
        try:
            doc = yaml.safe_load(pool.read_text(encoding="utf-8")) or {}
        except (OSError, ValueError) as exc:
            return _Snapshot(True, binary, port, workdir, pool, source, None,
                             (f"cannot read the pool config '{pool}': {type(exc).__name__}", *notes))
        pool_ports = {doc.get(k) for k in _POOL_OWNED if doc.get(k)}
        if port not in pool_ports:
            notes.append(
                f"the table says the engine listens on {port}, the pool config says "
                f"{sorted(p for p in pool_ports if isinstance(p, int))} — spawned children get the "
                f"table's number and would hand traffic to a port nobody is on"
            )
    else:
        workdir = egress_dir(home) / RUNTIME_BASENAME
        source = "compiled"
        pool = workdir / CONFIG_BASENAME

    rec = _read_record(home)
    if rec and rec.get("unreadable"):
        notes.append(f"engine record is unreadable: {rec['unreadable']}")
        rec = None
    return _Snapshot(True, binary, port, workdir, pool, source, rec, tuple(notes))


def _which(binary: str) -> Optional[str]:
    import shutil

    return shutil.which(binary)


def _render(snap: _Snapshot, table: Table) -> tuple[bytes, tuple[str, ...]]:
    """The bytes the engine will read, plus what we had to do to get them."""
    notes: list[str] = []
    if snap.source == "pool":
        assert snap.config_path is not None
        return snap.config_path.read_bytes(), ()
    assert snap.workdir is not None
    text = compile_config(table)
    notes.append(f"compiled from the egress table into {snap.config_path}")
    return text.encode("utf-8"), tuple(notes)


def status(table: Table, *, home: str | Path | None = None) -> EngineReport:
    snap = snapshot(table, home=home)
    if not snap.enabled:
        return EngineReport(OFF, "engine projection is switched off in config", snap.port,
                            notes=snap.notes)
    listening_now = listening(snap.port)
    ours = bool(snap.record) and not snap.record.get("unreadable") and _pid_is_ours(snap.record)[0]
    if listening_now and ours:
        ok, detail = probe_engine(snap.port)
        return EngineReport(OURS, "started by Plobi and still running", snap.port,
                            str(snap.workdir or ""), snap.source, snap.record.get("pid"),
                            detail if ok else f"FAIL {detail}", snap.notes)
    if listening_now:
        ok, detail = probe_engine(snap.port)
        who = _pid_is_ours(snap.record)[1] if snap.record else "no Plobi engine record"
        return EngineReport(FOREIGN, f"Plobi did not start this engine and will not stop it ({who})",
                            snap.port, str(snap.workdir or ""), snap.source, None,
                            detail if ok else f"FAIL {detail}", snap.notes)
    if ours:
        return EngineReport(FAILED, "the process we recorded is alive but its port is not accepting",
                            snap.port, str(snap.workdir or ""), snap.source,
                            snap.record.get("pid"), "", snap.notes)
    return EngineReport(STOPPED, "nothing is listening on the declared port", snap.port,
                        str(snap.workdir or ""), snap.source, None, "", snap.notes)


def _spawn(snap: _Snapshot, log_path: Path) -> tuple[int, float]:
    cmd = [snap.binary, "-d", str(snap.workdir)]
    flags = 0
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    log = open(log_path, "ab")  # noqa: SIM115 - the child keeps the handle, we close ours below
    try:
        proc = subprocess.Popen(  # noqa: S603 - the binary path comes from the user's own config
            cmd,
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            cwd=str(snap.workdir),
            creationflags=flags,
            **kwargs,
        )
    finally:
        log.close()
    return proc.pid, proc.create_time() if hasattr(proc, "create_time") else time.time()


def ensure(table: Table, *, home: str | Path | None = None, wait_s: float = 6.0) -> EngineReport:
    """Bring the engine up if it is ours to bring up.

    Never stops a process Plobi did not start, and never restarts a healthy one:
    a supervision path that retries on its own turns one outage into a loop.
    """
    snap = snapshot(table, home=home)
    current = status(table, home=home)
    if not snap.enabled:
        return current
    if current.state == OURS:
        recorded = snap.record or {}
        want = _sha(_render(snap, table)[0]) if snap.config_path else ""
        if want and recorded.get("config_sha") and recorded["config_sha"] != want:
            stopped = stop_ours(table, home=home)
            if stopped.state != STOPPED:
                return replace(current, notes=(*snap.notes, f"reload refused: {stopped.detail}"))
        else:
            return current
    if current.state == FOREIGN:
        return current
    if current.state == FAILED:
        return current

    if not snap.binary or not os.path.isfile(snap.binary):
        return replace(current, state=FAILED,
                       detail=f"cannot start: engine binary '{snap.binary}' is not on disk "
                              f"(shipping it is its own decision)")
    if snap.workdir is None or snap.config_path is None:
        return replace(current, state=FAILED, detail=",".join(snap.notes) or "no workdir resolved")

    snap.workdir.mkdir(parents=True, exist_ok=True)
    data, render_notes = _render(snap, table)
    acl_notes: list[str] = []
    if snap.source != "pool":
        snap.config_path.write_bytes(data)
        if sys.platform == "win32":
            locked, why = restrict_file_acl(snap.config_path)
            # Say it either way: the file holds resolved credentials, and "we meant to
            # lock it" must not read as "it is locked".
            acl_notes.append(f"compiled config ACL: {why}" if locked
                             else f"compiled config ACL NOT tightened — {why}")
        verdict, why = verify_with_binary(snap.binary, str(snap.config_path))
        if verdict != "VERIFIED":
            return EngineReport(FAILED, f"the engine refused the config we compiled ({verdict})",
                                snap.port, str(snap.workdir), snap.source, None, "",
                                (*snap.notes, *render_notes, *acl_notes, why[:300]))
    else:
        acl_notes.append(
            f"engine reads {snap.config_path} (pool source) — that file is not ours to rewrite "
            f"or re-ACL, and it carries the same credentials"
        )

    log_path = snap.workdir / "plobi-engine.log"
    pid, created = _spawn(snap, log_path)
    deadline = time.time() + wait_s
    while time.time() < deadline and not listening(snap.port):
        time.sleep(0.2)
    _write_record({"pid": pid, "created": created, "binary": snap.binary,
                   "workdir": str(snap.workdir), "mixed_port": snap.port,
                   "config_sha": _sha(data), "source": snap.source,
                   "started_by": "plobi", "started_at": time.time()}, home=home)
    ok, detail = probe_engine(snap.port)
    notes = [*snap.notes, *render_notes, *acl_notes]
    if not ok:
        # Measured on this box (09:10): an engine workdir without GeoSite.dat
        # starts, prints "Can't find GeoSite.dat, start download", and never
        # binds — the download needs the egress the engine is supposed to provide.
        # The engine's own last words are the difference between that diagnosis
        # and "connection refused".
        tail = _log_tail(log_path)
        if tail:
            notes.append(f"engine log says: {tail}")
    return EngineReport(OURS if ok else FAILED,
                        "started by Plobi" if ok else "started but is not carrying traffic",
                        snap.port, str(snap.workdir), snap.source, pid,
                        detail if ok else f"FAIL {detail}", tuple(notes))


def _log_tail(path: Path, limit: int = 400) -> str:
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            fh.seek(max(0, size - limit))
            raw = fh.read()
    except OSError:
        return ""
    lines = [ln for ln in raw.decode("utf-8", "replace").splitlines() if ln.strip()]
    return " / ".join(lines[-2:])[-limit:]


def reload_engine(table: Table, *, home: str | Path | None = None) -> EngineReport:
    """Pick the engine back up with the current table — only if it is ours to restart.

    A foreign listener is refused rather than killed: the port may be somebody
    else's proxy, and stealing it is not what "reload" was asked to mean.
    """
    snap = snapshot(table, home=home)
    if not snap.enabled:
        return EngineReport(OFF, "engine projection is switched off in config", snap.port,
                            notes=snap.notes)
    current = status(table, home=home)
    if current.state == FOREIGN:
        return replace(current, notes=(*current.notes, "reload refused: this engine was not started by Plobi"))
    if current.state == OURS:
        stopped = stop_ours(table, home=home)
        if stopped.state != STOPPED:
            return replace(current, notes=(*current.notes, f"reload refused: {stopped.detail}"))
    return ensure(table, home=home)


def stop_ours(table: Table, *, home: str | Path | None = None) -> EngineReport:
    """Stop only an engine Plobi started."""
    snap = snapshot(table, home=home)
    rec = snap.record
    if not rec or rec.get("unreadable"):
        return EngineReport(STOPPED, "no engine record, so nothing of ours to stop", snap.port,
                            str(snap.workdir or ""), snap.source)
    alive, why = _pid_is_ours(rec)
    if not alive:
        _clear_record(home)
        return EngineReport(STOPPED, f"recorded engine is gone ({why})", snap.port,
                            str(snap.workdir or ""), snap.source)
    psutil = _psutil()
    try:
        psutil.Process(int(rec["pid"])).terminate()
        psutil.Process(int(rec["pid"])).wait(10)
    except Exception as exc:
        return EngineReport(FAILED, f"could not stop pid {rec['pid']}: {type(exc).__name__}",
                            snap.port, str(snap.workdir or ""), snap.source, pid=rec.get("pid"))
    _clear_record(home)
    return EngineReport(STOPPED, f"stopped the engine we started ({why})", snap.port,
                        str(snap.workdir or ""), snap.source, pid=rec.get("pid"))


def restrict_file_acl(path: Path) -> tuple[bool, str]:
    """Put an owner-only DACL on a file **we** wrote, and say what happened.

    The compiled ``config.yaml`` carries the node credentials the engine has to be
    able to read, so leaving it to inherit whatever the directory's ACL happens to
    be is the exposure.  This never raises and never blocks a start: a failed
    tightening is reported as a note, because "we meant to lock it" is not the same
    as "it is locked" (裁定 94 — the surface has to carry the difference).
    """
    if sys.platform != "win32":
        return False, f"这一格只实现 Windows（这台是 {sys.platform}）"
    user = os.environ.get("USERNAME") or os.environ.get("USER")
    if not user:
        # Guessing a principal is how you hand the file to the wrong account.
        return False, "拿不到当前账户名（USERNAME / USER 都没设）——不敢凭空改 ACL"
    try:
        r = subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
            capture_output=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"icacls 没跑起来：{type(exc).__name__}: {exc}"
    out = (r.stdout + r.stderr).decode("utf-8", "replace").strip()
    if r.returncode != 0:
        return False, f"icacls rc={r.returncode}：{out.splitlines()[-1] if out else ''}"
    return True, f"{user}:F（继承已断，{out.splitlines()[-1] if out else ''}）"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def current_status(*, home: str | Path | None = None) -> EngineReport:
    """Read-only entry point for callers that only want to report."""
    return status(load_table(), home=home)
