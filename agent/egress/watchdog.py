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
    except Exception as exc:  # noqa: BLE001 - psutil.Error and OS errors both mean "gone"
        return False, "pid %s 已经不在了（%s）" % (pid, type(exc).__name__)
    if "watchdog" not in cmdline:
        return False, "pid %s 现在是别的东西" % pid
    if abs(created - float(rec.get("created", 0.0))) > 1.0:
        return False, "pid %s 的创建时间与我们记的不一样" % pid
    return True, "pid %d 是我们的看门狗" % pid


def tick(table: Any = None, *, home: str | Path | None = None) -> str:
    """One decision. Returns one of ``idle``/``healthy``/``restarted``/``gave-up``.

    Kept as a plain function (no loop) so the judgement is testable without processes.
    """
    from agent.egress import engine, system_proxy

    table = table if table is not None else _load_table()
    if not getattr(table, "system_proxy_enabled", False):
        # Nothing on this machine points at the engine ⇒ nothing to keep alive.
        return "idle"
    state = engine.status(table, home=home).state
    if state == engine.FOREIGN:
        # Somebody else's proxy owns the port; a watchdog must not fight it.
        return "gave-up"
    if state == engine.OFF:
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


def run(*, interval: float = INTERVAL_SECONDS, home: str | Path | None = None,
        once: bool = False) -> int:
    """The loop the detached child runs."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    record = _read_record(home) or {}
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
                "created": float(record.get("created") or time.time()),
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
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    log_path = _log_path(home)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(log_path, "ab")  # noqa: SIM115 - the child keeps the handle
    try:
        proc = subprocess.Popen(  # noqa: S603 - our own interpreter and our own module
            cmd, stdout=handle, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            cwd=str(repo_root()), creationflags=flags, env=child_env, **kwargs,
        )
    except OSError as exc:
        return False, "起看门狗失败（%s: %s）" % (type(exc).__name__, exc)
    finally:
        handle.close()
    _write_record({"pid": proc.pid, "created": time.time(), "last_tick": 0.0,
                   "last_verdict": "starting"}, home=home)
    return True, "看门狗起来了（pid %d）" % proc.pid


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
    try:
        import psutil

        family = _terminate_tree(psutil.Process(rec["pid"]), psutil)
    except Exception as exc:  # noqa: BLE001 - already gone counts as stopped
        _clear_record(home=home)
        return True, "看门狗本来就不在了（%s）" % type(exc).__name__
    _clear_record(home=home)
    return True, "已停掉看门狗（pid %d，共 %d 个进程）" % (rec["pid"], len(family))

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
