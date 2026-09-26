#!/usr/bin/env python3
"""Repoint the Windows gateway Scheduled Task launchers to the Code checkout.

The install-tree path ``D:\\Software\\Plobi\\plobi-agent`` embeds certifi under
that venv. After uninstall / partial delete, dingtalk-stream fails every ~10s:

  Could not find a suitable TLS CA certificate bundle, invalid path:
  D:\\Software\\Plobi\\plobi-agent\\venv\\Lib\\site-packages\\certifi\\cacert.pem

This script rewrites ``$HERMES_HOME/gateway-service/*Gateway*.{vbs,cmd}`` (and
Startup-folder copies) to run ``hermes_cli.main gateway run`` from the Code
``.venv``, with ``HERMES_HOME`` set. It does not rename the Scheduled Task
(legacy name may still be ``Plobi_Gateway``).

Usage (from Code root)::

    set HERMES_HOME=D:\\Data\\AppData\\Plobi
    .venv\\Scripts\\python.exe scripts\\plobi\\repoint_windows_gateway.py
    .venv\\Scripts\\python.exe scripts\\plobi\\repoint_windows_gateway.py --restart
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_VENV_PYTHON = CODE_ROOT / ".venv" / "Scripts" / "python.exe"
STALE_MARKERS = (
    r"D:\Software\Plobi",
    r"D:/Software/Plobi",
    "plobi-agent\\venv",
    "plobi-agent/venv",
    "plobi_cli.main",
)


def _hermes_home() -> Path:
    raw = (os.environ.get("HERMES_HOME") or "").strip()
    if raw:
        return Path(raw)
    # Dev machine convention (see PROMPT-DINGTALK-ROUTING-FIX).
    candidate = Path(r"D:\Data\AppData\Plobi")
    if candidate.is_dir():
        return candidate
    from hermes_constants import get_hermes_home

    return Path(get_hermes_home())


def _launcher_paths(home: Path) -> list[Path]:
    paths: list[Path] = []
    service = home / "gateway-service"
    if service.is_dir():
        paths.extend(sorted(service.glob("*Gateway*.vbs")))
        paths.extend(sorted(service.glob("*Gateway*.cmd")))
    appdata = os.environ.get("APPDATA", "").strip()
    if appdata:
        startup = (
            Path(appdata)
            / "Microsoft"
            / "Windows"
            / "Start Menu"
            / "Programs"
            / "Startup"
        )
        if startup.is_dir():
            paths.extend(sorted(startup.glob("*Gateway*.vbs")))
            paths.extend(sorted(startup.glob("*Gateway*.cmd")))
    return paths


def _needs_repoint(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return any(marker in text for marker in STALE_MARKERS)


def _profile_arg_for_launcher(path: Path) -> str:
    """Infer ``--profile X`` from launcher stem like ``Hermes_Gateway_l2-agenda``."""
    stem = path.stem
    for prefix in ("Hermes_Gateway_", "Plobi_Gateway_"):
        if stem.startswith(prefix):
            name = stem[len(prefix) :].strip()
            if name and name.lower() != "default":
                return f"--profile {name}"
    return ""


def _build_contents(
    *,
    python_exe: Path,
    home: Path,
    as_vbs: bool,
    profile_arg: str = "",
) -> str:
    # Import after PYTHONPATH includes CODE_ROOT.
    from hermes_cli.gateway_windows import (
        _build_gateway_cmd_script,
        _build_gateway_vbs_script,
    )

    hermes_home = str(home)
    working_dir = hermes_home
    python_path = str(python_exe)
    if as_vbs:
        return _build_gateway_vbs_script(
            python_path, working_dir, hermes_home, profile_arg
        )
    return _build_gateway_cmd_script(
        python_path, working_dir, hermes_home, profile_arg
    )


def repoint(
    *,
    home: Path,
    python_exe: Path,
    force: bool = False,
) -> list[Path]:
    if not python_exe.is_file():
        raise SystemExit(f"python not found: {python_exe}")
    rewritten: list[Path] = []
    for path in _launcher_paths(home):
        if not force and not _needs_repoint(path):
            continue
        as_vbs = path.suffix.lower() == ".vbs"
        profile_arg = _profile_arg_for_launcher(path)
        content = _build_contents(
            python_exe=python_exe,
            home=home,
            as_vbs=as_vbs,
            profile_arg=profile_arg,
        )
        path.write_text(content, encoding="utf-8", newline="\r\n")
        rewritten.append(path)
    return rewritten


def _kill_stale_gateways() -> list[int]:
    """Best-effort: end processes whose command line mentions the install tree."""
    killed: list[int] = []
    try:
        ps = (
            "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe' OR Name='python.exe'\" "
            "| Where-Object { $_.CommandLine -match 'Software\\\\Plobi|plobi_cli\\.main' } "
            "| ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; $_.ProcessId }"
        )
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=30,
        )
        for line in (proc.stdout or "").splitlines():
            line = line.strip()
            if line.isdigit():
                killed.append(int(line))
    except Exception:
        pass
    state = _hermes_home() / "gateway_state.json"
    if state.is_file():
        try:
            import json

            data = json.loads(state.read_text(encoding="utf-8"))
            pid = int(data.get("pid") or 0)
            argv = " ".join(str(x) for x in (data.get("argv") or []))
            if pid and ("Software\\Plobi" in argv or "plobi_cli" in argv):
                subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid)],
                    capture_output=True,
                    timeout=15,
                )
                killed.append(pid)
        except Exception:
            pass
    return killed


def _start_primary_vbs(home: Path) -> Path | None:
    service = home / "gateway-service"
    candidates = sorted(service.glob("*Gateway*.vbs"))
    if not candidates:
        return None
    # Prefer Plobi_Gateway.vbs (legacy scheduled task name) when present.
    preferred = service / "Plobi_Gateway.vbs"
    vbs = preferred if preferred.is_file() else candidates[0]
    subprocess.Popen(
        ["wscript.exe", "//B", "//Nologo", str(vbs)],
        cwd=str(home),
        close_fds=True,
    )
    return vbs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--python",
        type=Path,
        default=DEFAULT_VENV_PYTHON,
        help=f"Code venv python (default: {DEFAULT_VENV_PYTHON})",
    )
    parser.add_argument(
        "--home",
        type=Path,
        default=None,
        help="HERMES_HOME (default: env or D:\\Data\\AppData\\Plobi)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Rewrite launchers even if they do not mention the install tree",
    )
    parser.add_argument(
        "--restart",
        action="store_true",
        help="Kill stale install-tree gateways and start the rewritten VBS",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Exit 1 if any launcher still points at the install tree",
    )
    args = parser.parse_args(argv)

    # Ensure hermes_cli imports resolve from Code.
    code = str(CODE_ROOT)
    existing = os.environ.get("PYTHONPATH", "")
    if code not in existing.split(os.pathsep):
        os.environ["PYTHONPATH"] = code + (os.pathsep + existing if existing else "")
    if str(CODE_ROOT) not in sys.path:
        sys.path.insert(0, str(CODE_ROOT))

    home = args.home or _hermes_home()
    os.environ["HERMES_HOME"] = str(home)

    if args.check_only:
        stale = [p for p in _launcher_paths(home) if _needs_repoint(p)]
        if stale:
            print("STALE:")
            for p in stale:
                print(f"  {p}")
            return 1
        print("ok: no install-tree gateway launchers")
        return 0

    rewritten = repoint(home=home, python_exe=args.python, force=args.force)
    if rewritten:
        print("rewrote:")
        for p in rewritten:
            print(f"  {p}")
    else:
        print("nothing to rewrite (launchers already on Code, or missing)")

    if args.restart:
        killed = _kill_stale_gateways()
        if killed:
            print(f"killed stale pids: {killed}")
            time.sleep(1.5)
        started = _start_primary_vbs(home)
        if started:
            print(f"started: {started}")
        else:
            print("WARNING: no *Gateway*.vbs under gateway-service to start", file=sys.stderr)
            return 2

    stale_after = [p for p in _launcher_paths(home) if _needs_repoint(p)]
    if stale_after:
        print("WARNING: still stale:", file=sys.stderr)
        for p in stale_after:
            print(f"  {p}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
