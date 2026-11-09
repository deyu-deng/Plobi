"""Keep the engine up while the machine depends on it (WP-EGRESS-ROUTING).

Measured 10-29: the engine went down twice in one day and **nothing brought it back**.
The desktop backend only supervises while it is alive; when it is closed, or when the
process itself is wedged, the machine keeps a system proxy pointing at a dead port —
which is indistinguishable from "no internet".

What this does, once per tick:

* the table says the machine is taken over → the machine depends on the engine;
* the engine is ours and healthy → do nothing (never restart a healthy process: one
  outage must not become a loop, 裁定 102);
* the engine is stopped or failed → :func:`agent.egress.engine.ensure`;
* someone else holds the port, or the projection is switched off → exit, quietly.
  A watchdog that fights a foreign proxy is worse than no watchdog.

It is a **detached child**, like the engine itself: the thing that starts it (the CLI,
the desktop backend) may exit long before the machine stops depending on us.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

RECORD_BASENAME = "watchdog.json"
LOG_BASENAME = "watchdog.log"
#: How often to look. 20s is short enough that a user noticing "no internet" is rare,
#: long enough that an ensure() (which may spawn + probe) is not a busy loop.
INTERVAL_SECONDS = 20.0
#: A marker in the child's own command line, so ownership is provable.
CMDLINE_MARKER = "egress.watchdog"
#: Set in the child's own environment: it is how a re-arm request knows "I am the supervisor",
#: so `engine.ensure()` (which the watchdog itself calls every tick) cannot fork copies of us.
MARKER = "PLOBI_EGRESS_WATCHDOG"
#: How long a "starting" placeholder stays believable. The worker registers its own pid
#: on its first tick; until then the record carries pid 0 and this window keeps a second
#: `start()` from forking a duplicate.
STARTING_WINDOW_SECONDS = 45.0


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _home(home: str | Path | None = None) -> Path:
    from agent.egress.engine import egress_dir

    return egress_dir(home)


def _record_path(home: str | Path | None = None) -> Path:
    return _home(home) / RECORD_BASENAME


def _log_path(home: str | Path | None = None) -> Path:
    return _home(home) / LOG_BASENAME


def _read_record(home: str | Path | None = None) -> Optional[dict[str, Any]]:
    try:
        raw = json.loads(_record_path(home).read_text(encoding="utf-8"))
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
    """Is the recorded pid still **our** watchdog? (Windows recycles pids.)"""
    pid = rec.get("pid")
    # A "starting" placeholder: `start()` has spawned the worker but the worker has not
    # registered its pid yet. Believing it (for a bounded window) is what stops a second
    # `start()` — the watchdog's own tick calls `enable()` → `_watchdog().start()` — from
    # forking a duplicate supervisor.
    if pid == 0 and rec.get("last_verdict") == "starting":
        age = time.time() - float(rec.get("created") or 0.0)
        if age < STARTING_WINDOW_SECONDS:
            return True, "看门狗正在启动（pid 还没登记）"
        return False, "启动窗口过了，仍然没有登记上 pid"
    if not isinstance(pid, int):
        return False, "记录里没有可用的 pid"
    try:
        import psutil
    except ImportError:
        return False, "psutil 不可用，没法确认这个 pid 是不是我们的"
    try:
        proc = psutil.Process(pid)
        cmdline = " ".join(proc.cmdline())
        created = proc.create_time()
    except Exception as exc:  # noqa: BLE001 - psutil.Error and OS errors both mean "gone"
        return False, "pid %s 已经不在了（%s）" % (pid, type(exc).__name__)
    if "watchdog" not in cmdline:
        return False, "pid %s 现在是别的东西" % pid
    if abs(created - float(rec.get("created", 0.0))) > 1.0:
        return False, "pid %s 的创建时间与我们记的不一样" % pid
    return True, "pid %d 是我们的看门狗" % pid


def _own_created() -> float:
    """When *this* process actually started — the record's `created` anchor.

    Measured from the OS rather than from "whenever we got round to writing the record",
    so the pid-reuse check in `_pid_is_ours` compares like with like.
    """
    try:
        import psutil

        return float(psutil.Process(os.getpid()).create_time())
    except Exception:  # noqa: BLE001 - a coarse timestamp still beats nothing
        return time.time()


#: Import time, i.e. this worker's start time — the yardstick for "is that quit marker about us?"
_PROCESS_STARTED_AT = time.time()


def _quit_marker_is_about_us(system_proxy: Any, home: str | Path | None) -> bool:
    """Did the desktop mark *this* session as a deliberate quit? (WP-QUIT-CLEAN, 11-05)

    The marker is timestamped by the desktop's clock and compared against ours with 1s of slack: a
    leftover file from a previous session must never retire a watchdog that is doing its job.
    """
    at = system_proxy.quit_marker_since(home)

    return at is not None and at >= _PROCESS_STARTED_AT - 1.0


def _stop_a_switched_off_engine(engine: Any, table: Any, home: str | Path | None) -> None:
    """Stop our own leftover engine when the table says the engine is switched off.

    ``tick`` is also called with narrow stand-in tables in tests, so the shape is
    checked rather than assumed; a table that cannot describe an engine has no
    process to stop.  Never raises: a watchdog that dies of a bookkeeping error is
    worse than one that misses a tick.
    """
    if getattr(table, "engine_enabled", False):
        return
    if not hasattr(table, "engine_mixed_port"):
        return
    try:
        engine.stop_ours(table, home=home)
    except Exception:  # noqa: BLE001 - cleanup is best-effort, the loop must survive
        log.warning("watchdog: 停不掉那个已被关掉的引擎", exc_info=True)


def _hand_the_registry_back(system_proxy: Any, table: Any, home: str | Path | None) -> None:
    """停掉引擎之后，注册表不能继续指着它。

    停在「停了进程」这一步就是 10-29 那个现场的另一半：进程没了，注册表还在，全机照旧
    对着一个空端口说话。只交还**我们的**那一份，别人的设置一概不碰。Never raises。
    """
    try:
        if system_proxy.status(table, home=home).get("ours"):
            system_proxy.disable(home=home)
    except Exception:  # noqa: BLE001 - a watchdog that dies of a registry error is worse
        log.warning("watchdog: 交还系统代理失败", exc_info=True)


def tick(table: Any = None, *, home: str | Path | None = None) -> str:
    """One decision. Returns one of ``idle``/``healthy``/``restarted``/``gave-up``.

    Kept as a plain function (no loop) so the judgement is testable without processes.
    """
    from agent.egress import engine, system_proxy

    table = table if table is not None else _load_table()

    # A deliberate quit outranks the table (WP-QUIT-CLEAN, 11-05). `before-quit` has already handed
    # the registry back, and without this the `taken-over` rule below would put the takeover right
    # back on the next tick — after the engine it depends on is gone. ``idle`` is what ``run``
    # treats as "nothing left to supervise": clear the record and exit.
    if _quit_marker_is_about_us(system_proxy, home):
        log.info("watchdog: 用户主动退出了 Plobi —— 不再接管，本进程退出")
        return "idle"

    if not getattr(table, "system_proxy_enabled", False):
        # Nothing on this machine points at the engine ⇒ nothing to keep alive.
        # 「不用引擎」和「不接管系统代理」是两个开关：engine 被关掉而系统代理也关着的
        # 机器上，少这一句就没有任何一条路径去停残留进程。
        _stop_a_switched_off_engine(engine, table, home)
        _hand_the_registry_back(system_proxy, table, home)
        return "idle"
    state = engine.status(table, home=home).state
    if state == engine.FOREIGN:
        # Somebody else's proxy owns the port; a watchdog must not fight it.
        return "gave-up"
    if state == engine.OFF:
        # The table says off — but a live engine of ours does not read the table, and
        # `status` stops looking at it the moment the switch flips.  A hand-edited
        # config.yaml (or a save that landed before this cleanup existed) can leave our
        # process holding the port with nothing supervising it.  Clean it, then idle.
        _stop_a_switched_off_engine(engine, table, home)
        _hand_the_registry_back(system_proxy, table, home)
        return "idle"
    verdict = "healthy"
    if state != engine.OURS:
        engine.ensure(table, home=home)
        verdict = "restarted"

    # The **takeover** is part of "this machine depends on us".  Measured 10-31: the
    # ikuuu client, on exit, restored its own registry values (ProxyEnable=0 + its
    # override list) — our takeover disappeared silently and the machine went direct
    # for ~15 hours (google dead, CN fine) while the table still said "take over".
    # The table is the source of truth (裁定 94), so re-assert it.
    info = system_proxy.status(table, home=home)
    if info.get("declared") and not info.get("ours"):
        if system_proxy.enable(table, home=home).ok and verdict == "healthy":
            verdict = "retaken"
    return verdict


def _load_table():
    from agent.egress.rules import load_table

    return load_table(force_reload=True)


def _setup_file_log(log_path: Path) -> None:
    """Log to our own file — do not rely on inherited stdout.

    The worker is spawned through a ``cmd /c start`` shim (see :func:`start`), and
    the shim does **not** hand the worker's stdout down to the log file the parent
    opened (measured 11-04: the watchdog kept running happily and the log stopped
    dead at the last pre-shim line).  A supervisor whose own log is blind is a
    supervisor nobody can debug, so the file handle is opened here instead.
    """
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(fh)
        log.setLevel(logging.INFO)
        log.propagate = False
    except OSError:
        pass  # stdout still carries something


def run(*, interval: float = INTERVAL_SECONDS, home: str | Path | None = None,
        once: bool = False) -> int:
    """The loop the detached child runs."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    _setup_file_log(_log_path(home))
    while True:
        try:
            from plobi_cli.config import reload_env

            # The compiled config reads credentials from the process environment, and a
            # fresh node added to the table since we started would otherwise be missing.
            reload_env()
            verdict = tick(home=home)
            log.info("watchdog: %s", verdict)
            if verdict == "gave-up":
                log.warning("watchdog: 端口上是别人的代理，退出（不跟别人抢）")
                return 0
            _write_record({
                "pid": os.getpid(),
                "created": _own_created(),
                "last_tick": time.time(),
                "last_verdict": verdict,
            }, home=home)
            if once:
                return 0
            if verdict == "idle":
                # The table no longer takes the machine over ⇒ nothing for us to do.
                _clear_record(home=home)
                return 0
        except Exception as exc:  # noqa: BLE001 - a watchdog must outlive one bad tick
            log.warning("watchdog: tick failed: %s: %s", type(exc).__name__, exc)
            if once:
                return 1
        time.sleep(max(1.0, float(interval)))


def start(*, home: str | Path | None = None, interval: float = INTERVAL_SECONDS) -> tuple[bool, str]:
    """Start the detached child (idempotent: a live one is reused)."""
    rec = _read_record(home)
    if rec:
        ours, why = _pid_is_ours(rec)
        if ours:
            return True, why
        _clear_record(home=home)

    cmd = [sys.executable, "-m", "agent.egress.watchdog", "--interval", str(interval)]
    child_env = {**os.environ, MARKER: "1"}
    flags = 0
    kwargs: dict[str, Any] = {}
    spawn_cmd = cmd
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        # `DETACHED_PROCESS` alone is **not** enough on Windows (measured 11-04): it
        # detaches the console, not the parent/child link, so the worker is still a
        # descendant of the backend — and the desktop's `taskkill /T /F` on quit reaps
        # the whole live tree.  The watchdog therefore died together with the backend
        # (its log gap 11:32→11:39 on this machine), leaving the engine unsupervised:
        # once mihomo died, nothing restarted it and the machine sat on a dead proxy
        # with every app reading "no internet".
        #
        # Spawn through a `cmd /c start` shim that exits immediately: the worker's
        # parent is then a *dead* PID, which the tree walk cannot reach.  The worker
        # registers its own pid in the record, so the shim's pid is never mistaken
        # for it.
        spawn_cmd = ["cmd.exe", "/c", "start", "", "/b", *cmd]
    else:
        kwargs["start_new_session"] = True
    log_path = _log_path(home)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(log_path, "ab")  # noqa: SIM115 - the child keeps the handle
    try:
        subprocess.Popen(  # noqa: S603 - our own interpreter and our own module
            spawn_cmd, stdout=handle, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            cwd=str(repo_root()), creationflags=flags, env=child_env, **kwargs,
        )
    except OSError as exc:
        return False, "起看门狗失败（%s: %s）" % (type(exc).__name__, exc)
    finally:
        handle.close()
    # pid 0 + "starting": the worker writes its own pid on its first tick. Recording
    # the shim's pid here would be a lie — and a lie `_pid_is_ours` would correctly
    # call "not ours", which is how a duplicate supervisor used to get forked.
    _write_record({"pid": 0, "created": time.time(), "last_tick": 0.0,
                   "last_verdict": "starting"}, home=home)
    return True, "看门狗起来了"


def _terminate_tree(proc: Any, psutil: Any) -> list:
    """Kill the recorded process **and its children** (a venv python is a launcher)."""
    try:
        family = list(proc.children(recursive=True))
    except Exception:  # noqa: BLE001
        family = []
    family.append(proc)
    for p in family:
        try:
            p.terminate()
        except Exception:  # noqa: BLE001
            pass
    try:
        psutil.wait_procs(family, timeout=3)
    except Exception:  # noqa: BLE001
        pass
    for p in family:
        try:
            if p.is_running():
                p.kill()
        except Exception:  # noqa: BLE001
            pass
    return family


def stop(*, home: str | Path | None = None) -> tuple[bool, str]:
    """Stop the watchdog we started — and only that one."""
    rec = _read_record(home)
    if not rec:
        return False, "没有看门狗的记录，没东西可停"
    ours, why = _pid_is_ours(rec)
    if not ours:
        _clear_record(home=home)
        return False, "记录的看门狗已经不是我们的了（%s）——没碰它" % why
    pid = rec.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        # Still in the "starting" window: the worker has not registered a pid yet, so
        # there is nothing to kill (and pid 0 is the System Idle Process — never touch it).
        _clear_record(home=home)
        return True, "看门狗还在启动，没有 pid 可停；记录已清"
    try:
        import psutil

        family = _terminate_tree(psutil.Process(pid), psutil)
    except Exception as exc:  # noqa: BLE001 - already gone counts as stopped
        _clear_record(home=home)
        return True, "看门狗本来就不在了（%s）" % type(exc).__name__
    _clear_record(home=home)
    return True, "已停掉看门狗（pid %d，共 %d 个进程）" % (pid, len(family))

def ensure_running(*, home: str | Path | None = None, interval: float = INTERVAL_SECONDS) -> tuple[bool, str]:
    """Arm the supervisor if nobody has it — **the answer to "who watches the watchdog"**.

    11-02 这台实测：看门狗 10:06 被外力杀掉（日志停在一句 `healthy`，没有异常、pid 已不存在），
    之后 3 小时没有任何路径会把它挂回去——引擎有监督者，监督者自己没有。`enable()` 只在
    「接管动作发生的那一刻」起它，而接管一旦已经生效，重启之外的任何一次 `engine.ensure()`
    都不再碰它。

    所以任何一次「把引擎弄好」的路径都顺手问这一句。两条限制：
    · **自己是看门狗时不自 spawn**（子进程带 `MARKER`，它每 tick 都调 `engine.ensure`）；
    · 表里没要求整机承载时不动（没东西要监督，起一个每 20 秒读一次注册表的进程是白付的税）。
    """
    if os.environ.get(MARKER):
        return True, "我们自己就是看门狗——不 spawn 第二个"
    return start(home=home, interval=interval)


def status(*, home: str | Path | None = None) -> dict[str, Any]:
    """What the watchdog looks like right now."""
    rec = _read_record(home) or {}
    alive, why = (_pid_is_ours(rec) if rec else (False, "没有记录"))
    last = float(rec.get("last_tick") or 0.0)
    return {
        "alive": alive,
        "detail": why,
        "pid": rec.get("pid"),
        "last_tick": last,
        "age_seconds": (time.time() - last) if last else None,
        "last_verdict": rec.get("last_verdict", ""),
    }


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="agent.egress.watchdog")
    parser.add_argument("--interval", type=float, default=INTERVAL_SECONDS)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    return run(interval=args.interval, once=args.once)


if __name__ == "__main__":  # the detached child runs exactly this
    sys.exit(main())
