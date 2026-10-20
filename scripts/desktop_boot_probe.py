#!/usr/bin/env python3
"""桌面后端启动成本探针（WP-DESKTOP-BOOT-COST）。

桌面起的是哪份码，和那份码起得多慢，是两件事：本脚本一次只回答后者，并把「取的是哪份码」
和读数印在同一行里，免得下次有人拿装好的旧那份当仓里 HEAD 的账（2026-10-20 实测那份落后 72 笔）。

落点默认是**临时家**（自己 `PLOBI_HOME`，测完不碰用户那份真数据）。要看真家的数就显式
`--live`——它会写 state.db 与 logs，那是跑起来的后端本来就要写的东西，不是本脚本加的。

「可对话」= WS 握手拿到 `commands.catalog` 的回执，**不打模型、不花钱**。

用法：
    python Code/scripts/desktop_boot_probe.py                       # 仓里 HEAD 那份 + 临时家
    python Code/scripts/desktop_boot_probe.py --which installed     # 桌面实际用的那份
    python Code/scripts/desktop_boot_probe.py --live                # 真家（会写盘，读数才和用户等的对得上）
    python Code/scripts/desktop_boot_probe.py --json                # 给闸门/流水吃
退出码：ready 超过 --max-ready-seconds（默认 5）即 1，否则 0。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]          # Code/
sys.path.insert(0, str(REPO))

from plobi_constants import get_plobi_home          # noqa: E402
from plobi_cli.plobi_bin import resolve_plobi_argv  # noqa: E402

READY_PREFIX = "PLOBI_BACKEND_READY port="
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


@dataclass
class Boot:
    which: str
    argv: list[str]
    git_rev: str
    home: str
    ready_s: float
    http_s: float | None
    talk_s: float | None
    uptime_min: int = 0
    error: str = ""


def _uptime_minutes() -> int:
    """开机多久——这台实测 ready 从 18.84s（开机 ~30 分钟）掉到 3.25s（开机 5.5 小时），
    不把这一格印在读数旁边，任何一个数都会被当成回归或当成没问题。"""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "[int](((Get-Date) - (Get-CimInstance Win32_OperatingSystem).LastBootUpTime).TotalMinutes)"],
            capture_output=True, text=True, timeout=30)
        return int((out.stdout or "0").strip() or 0)
    except Exception:
        return 0


def _git_rev(cwd: Path) -> str:
    """这份码自己是哪一笔——读数必须和这一格一起交，否则不知道量的是谁。"""
    try:
        out = subprocess.run(["git", "-C", str(cwd), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=15)
        return out.stdout.strip() or "?"
    except Exception:
        return "?"


def _source_argv() -> list[str]:
    """仓里那份 venv 的入口（按 Windows / POSIX 两种布局探，别照抄文档里那一条）。"""
    for rel in (("Scripts", "plobi.exe"), ("Scripts", "plobi"), ("bin", "plobi")):
        cand = REPO / ".venv" / rel[0] / rel[1]
        if cand.exists():
            return [str(cand)]
    raise SystemExit(f"仓里 venv 找不到 plobi 入口：{REPO / '.venv'}")


def _installed_argv() -> list[str]:
    """桌面实际解析到的那份——用被测代码自己的解析器，不手拼路径。"""
    return resolve_plobi_argv()


def _tail() -> list[str]:
    """spawn 的那串参数走桌面自己那支纯函数（backend-command.ts），保证量的就是桌面那一发。"""
    tsx = REPO / "node_modules" / ".bin" / ("tsx.cmd" if os.name == "nt" else "tsx")
    if not tsx.exists():
        return ["serve", "--host", "127.0.0.1", "--port", "0"]
    snippet = ("import {serveBackendArgs} from "
               "'file:///" + str(REPO / 'apps' / 'desktop' / 'electron' / 'backend-command.ts').replace("\\", "/") +
               ".ts'; process.stdout.write(JSON.stringify(serveBackendArgs()));")
    out = subprocess.run([str(tsx), "-e", snippet], capture_output=True, text=True,
                         cwd=str(REPO / "apps" / "desktop"), timeout=120)
    line = (out.stdout or "").strip().splitlines()[-1] if (out.stdout or "").strip() else ""
    return json.loads(line) if line.startswith("[") else ["serve", "--host", "127.0.0.1", "--port", "0"]


def measure(which: str, live: bool, deadline_s: float) -> Boot:
    head = _source_argv() if which == "source" else _installed_argv()
    code_home = Path(REPO) if which == "source" else Path(get_plobi_home()) / "plobi-agent"
    argv = list(head) + _tail()
    home = str(get_plobi_home()) if live else tempfile.mkdtemp(prefix="plobi-boot-probe-")
    token = os.urandom(16).hex()

    env = dict(os.environ)
    env.update({"PLOBI_HOME": home, "PLOBI_DASHBOARD_SESSION_TOKEN": token,
                "PYTHONPATH": str(REPO) + os.pathsep + env.get("PYTHONPATH", "")})

    t0 = time.perf_counter()
    child = subprocess.Popen(argv, cwd=str(REPO), env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, bufsize=1, creationflags=NO_WINDOW)
    box: dict = {"port": None, "first": ""}

    def pump() -> None:
        for line in child.stdout:
            if READY_PREFIX in line:
                try:
                    box["port"] = int(line.split(READY_PREFIX)[1].split()[0])
                except (ValueError, IndexError):
                    pass
                return
            if not box["first"] and line.strip():
                box["first"] = line.strip()[:200]

    th = threading.Thread(target=pump, daemon=True)
    th.start()
    stop = t0 + deadline_s
    while box["port"] is None and time.perf_counter() < stop and child.poll() is None:
        time.sleep(0.05)

    boot = Boot(which=which, argv=argv, git_rev=_git_rev(code_home), home=home,
                ready_s=round(time.perf_counter() - t0, 2), http_s=None, talk_s=None)

    if box["port"] is None:
        boot.error = f"没等到 ready（rc={child.poll()}），首行输出：{box['first']!r}"
        child.kill()
        if not live:
            shutil.rmtree(home, ignore_errors=True)
        return boot

    port = int(box["port"])
    while time.perf_counter() < stop:
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/agents",
                                         headers={"Authorization": f"Bearer {token}"})
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status == 200:
                    boot.http_s = round(time.perf_counter() - t0, 2)
                    break
        except Exception:
            time.sleep(0.05)

    try:
        import websocket  # type: ignore
        ws = websocket.create_connection(f"ws://127.0.0.1:{port}/api/ws?token={token}", timeout=20)
        ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "commands.catalog", "params": {}}))
        while time.perf_counter() < stop:
            if json.loads(ws.recv()).get("id") == 1:
                boot.talk_s = round(time.perf_counter() - t0, 2)
                break
        ws.close()
    except Exception as exc:
        boot.error = f"可对话探针没成：{type(exc).__name__}"

    child.terminate()
    try:
        child.wait(timeout=20)
    except subprocess.TimeoutExpired:
        child.kill()
    if not live:
        shutil.rmtree(home, ignore_errors=True)
    return boot


def exit_code_for(boot: Boot, max_ready_seconds: float) -> int:
    """闸门那一步单独成函数：读数有没有带语境、阈值怎么判，不该只有跑真后端才测得到。"""
    if boot.error:
        return 1
    missing = [k for k in ("which", "git_rev", "home", "uptime_min") if not getattr(boot, k)]
    if missing:
        return 1
    return 0 if boot.ready_s <= max_ready_seconds else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="桌面后端启动成本探针（零模型额度）")
    ap.add_argument("--which", choices=("source", "installed"), default="source",
                    help="source＝仓里 HEAD 那份 venv；installed＝桌面实际用的那份装好的码")
    ap.add_argument("--live", action="store_true",
                    help="用真 PLOBI_HOME（会写 state.db 与 logs）；默认用临时家")
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--max-ready-seconds", type=float, default=5.0)
    ap.add_argument("--deadline-seconds", type=float, default=240.0)
    args = ap.parse_args()

    boot = measure(args.which, args.live, args.deadline_seconds)
    boot.uptime_min = _uptime_minutes()
    if args.as_json:
        print(json.dumps(asdict(boot), ensure_ascii=False))
    else:
        print(f"[{boot.which}] 码={boot.git_rev} 家={'真' if args.live else '临时'} 开机{boot.uptime_min}min "
              f"ready={boot.ready_s:6.2f}s  http={boot.http_s if boot.http_s is not None else float('nan'):6.2f}s  "
              f"可对话={boot.talk_s if boot.talk_s is not None else float('nan'):6.2f}s"
              + (f"  ⚠ {boot.error}" if boot.error else ""))
    return exit_code_for(boot, args.max_ready_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
