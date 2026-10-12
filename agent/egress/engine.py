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
import os
import socket
import subprocess
import time
import urllib.request
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Optional

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


def probe_engine(port: int, *, url: str = PROBE_URL, timeout: float = 8.0) -> tuple[bool, str]:
    """Ask the engine a question with a known answer.

    Returns ``(ok, detail)``.  Never raises: a supervision path that dies because
    a probe timed out would take the product down with it.
    """
    if port <= 0:
        return False, "no listen port declared (network.egress.engine.mixed_port)"
    proxy = f"http://127.0.0.1:{port}"
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy})
    )
    try:
        with opener.open(url, timeout=timeout) as resp:
            status = resp.status
    except Exception as exc:
        return False, f"via {proxy}: {type(exc).__name__} {str(exc)[:80]}"
    if status == PROBE_EXPECTED:
        return True, f"{status} via {proxy}"
    return False, f"{status} via {proxy} (expected {PROBE_EXPECTED})"


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
    if snap.source != "pool":
        snap.config_path.write_bytes(data)
        verdict, why = verify_with_binary(snap.binary, str(snap.config_path))
        if verdict != "VERIFIED":
            return EngineReport(FAILED, f"the engine refused the config we compiled ({verdict})",
                                snap.port, str(snap.workdir), snap.source, None, "",
                                (*snap.notes, *render_notes, why[:300]))

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
    notes = [*snap.notes, *render_notes]
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


def stop_ours(table: Table, *, home: str | Path | None = None) -> EngineReport:
    """Stop only an engine Plobi started."""
    snap = snapshot(table, home=home)
    rec = snap.record
    if not rec or rec.get("unreadable"):
        return EngineReport(STOPPED, "no engine record, so nothing of ours to stop", snap.port)
    alive, why = _pid_is_ours(rec)
    if not alive:
        _clear_record(home)
        return EngineReport(STOPPED, f"recorded engine is gone ({why})", snap.port)
    psutil = _psutil()
    try:
        psutil.Process(int(rec["pid"])).terminate()
        psutil.Process(int(rec["pid"])).wait(10)
    except Exception as exc:
        return EngineReport(FAILED, f"could not stop pid {rec['pid']}: {type(exc).__name__}",
                            snap.port, pid=rec.get("pid"))
    _clear_record(home)
    return EngineReport(STOPPED, f"stopped the engine we started ({why})", snap.port,
                        pid=rec.get("pid"))


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def current_status(*, home: str | Path | None = None) -> EngineReport:
    """Read-only entry point for callers that only want to report."""
    return status(load_table(), home=home)
