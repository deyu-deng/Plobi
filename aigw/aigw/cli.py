"""aigw command-line interface.

Subcommands:
  aigw start [--config path] [--host H] [--port P]
  aigw discover [--scan] [--app cursor|antigravity|workbuddy] [--dry-run] [--yes]
  aigw status  [--config path]

Design notes / safety:
  - `discover` launches an intercepting proxy + (optionally) the target desktop app.
    That is a sensitive operation, so by DEFAULT it only prints the commands it would
    run. Pass --yes to actually execute, or --dry-run to force the print-only view.
  - `discover --scan` is read-only: it reads the desktop apps' local credential stores
    the same way the gateway does, and reports what would be found. It changes nothing.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .config import load
from .tokens import desktop_stores as ds
from .auth import antigravity_oauth as oa

TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"

# how to launch each target app behind mitmproxy (Electron needs NODE_EXTRA_CA_CERTS)
LAUNCH_SNIPPETS = {
    "cursor": (
        "HTTPS_PROXY=http://127.0.0.1:8080 HTTP_PROXY=http://127.0.0.1:8080 "
        "NODE_EXTRA_CA_CERTS=~/.mitmproxy/mitmproxy-ca-cert.pem "
        "/Applications/Cursor.app/Contents/MacOS/Cursor"
    ),
    "antigravity": (
        "HTTPS_PROXY=http://127.0.0.1:8080 HTTP_PROXY=http://127.0.0.1:8080 "
        "NODE_EXTRA_CA_CERTS=~/.mitmproxy/mitmproxy-ca-cert.pem "
        "ANTIGRAVITY_PROXY=1 <your-antigravity-launch-command>"
    ),
    "workbuddy": (
        "HTTPS_PROXY=http://127.0.0.1:8080 HTTP_PROXY=http://127.0.0.1:8080 "
        "NODE_EXTRA_CA_CERTS=~/.mitmproxy/mitmproxy-ca-cert.pem "
        "<your-workbuddy-launch-command>"
    ),
}


def _print_scan() -> None:
    print("== aigw discover --scan (read-only, safe) ==")
    c = ds.read_cursor()
    a = ds.read_antigravity()
    print(f"  cursor state.vscdb     : {'FOUND' if ds.cursor_state_db() else 'not installed'}")
    if c:
        print(f"    -> access token      : present ({'refresh token: yes' if c.get('refresh_token') else 'no refresh'})")
        print(f"    -> email             : {c.get('email') or '(unknown)'}")
    else:
        print("    -> no logged-in cursor session detected")
    print(f"  antigravity db.sqlite  : {'FOUND' if ds.antigravity_db() else 'not installed'}")
    if a:
        print("    -> refresh token      : present")
    else:
        print("    -> no antigravity session detected")
    print("  antigravity token sources (in priority order):")
    print("    1. config accounts[].refresh_token  (composite: rt|projectId|mpid)")
    print("    2. env ANTIGRAVITY_REFRESH_TOKEN     (run: aigw auth antigravity)")
    print("    3. env ANTIGRAVITY_ACCESS_TOKEN      (quick live test)")
    print("    4. keychain gemini/antigravity       (go-keyring blob; needs fresh token)")
    if not a:
        print("    -> no session detected; get one with: aigw auth antigravity")
    print("  workbuddy              : no local store reader (configure base_url + token in config.yaml)")
    print("\nTo capture the real wire protocol, run: aigw discover --app cursor --yes")


def _discover(args) -> int:
    if args.scan:
        _print_scan()
        return 0

    # offline report regeneration (no mitmproxy needed)
    if args.report:
        cmd = [sys.executable, str(TOOLS_DIR / "mitm_discover.py"),
               "--report", args.report]
        if args.app:
            cmd += ["--app", args.app]
        return subprocess.run(cmd).returncode

    addon = TOOLS_DIR / "mitm_discover.py"
    mitmdump = shutil.which("mitmdump") or "mitmdump"
    cmd = [mitmdump, "-s", str(addon),
           "--set", f"app={args.app or 'all'}",
           "--set", "out=captures"]
    if args.hosts:
        cmd += ["--set", f"hosts={args.hosts}"]

    app = args.app
    snippet = LAUNCH_SNIPPETS.get(app, "") if app else ""
    print("== aigw discover ==")
    print(f"  proxy command : {' '.join(cmd)}")
    if snippet:
        print(f"  then launch app:\n    {snippet}")
    else:
        print("  (pass --app cursor|antigravity|workbuddy to see the launch snippet)")
    if app == "workbuddy" and not args.hosts:
        print("  note: Workbuddy's host is unknown — add "
              "--hosts=host1,host2 (or --set hosts=... when launching).")

    if args.dry_run or not args.yes:
        print("\n[dry-run] no process started. Re-run with --yes to actually "
              "launch mitmdump.\nAfter capture, regenerate/inspect the report with:\n"
              f"  aigw discover --report captures/{app or '_all'}"
              + (f" --app {app}" if app else ""))
        return 0

    print("\n[launching] mitmdump ...")
    try:
        subprocess.run(cmd)
    except KeyboardInterrupt:
        print("\n[stopped]")
    return 0


def _start(args) -> int:
    if args.config:
        os.environ["AIGW_CONFIG"] = str(Path(args.config).resolve())
    try:
        import uvicorn  # type: ignore
    except ImportError:
        print("uvicorn not installed. Run: pip install -r requirements.txt", file=sys.stderr)
        return 1
    cfg = load()
    host = args.host or cfg["server"].get("host", "127.0.0.1")
    port = args.port or cfg["server"].get("port", 8000)
    # configure logging before uvicorn takes over the root logger
    from .main import setup_logging
    setup_logging(cfg.get("logging"))
    logging.getLogger("aigw").info("starting on http://%s:%s", host, port)
    print(f"[aigw] starting on http://{host}:{port}")
    uvicorn.run("aigw.main:app", host=host, port=port,
                log_level="info", loop="asyncio")
    return 0


def _status(args) -> int:
    if args.config:
        os.environ["AIGW_CONFIG"] = str(Path(args.config).resolve())
    try:
        cfg = load()
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 1
    host = cfg["server"].get("host", "127.0.0.1")
    port = cfg["server"].get("port", 8000)
    api_key = cfg["server"].get("api_key") or os.environ.get("AIGW_KEY", "")
    base = f"http://{host}:{port}"
    import urllib.request

    def get(path: str):
        req = urllib.request.Request(base + path,
                                     headers={"Authorization": f"Bearer {api_key}"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode())

    try:
        hz = get("/healthz")
    except Exception as e:
        print(f"[status] cannot reach gateway at {base}: {e}", file=sys.stderr)
        print("         is it running?  aigw start", file=sys.stderr)
        return 1

    print(f"== aigw status @ {base} ==")
    for prov, accounts in hz.items():
        if prov == "tokens":
            continue
        print(f"[{prov}]")
        for acc in accounts:
            print(f"  - {acc['id']:<18} state={acc['state']:<9} fails={acc['fail']} "
                  f"quota={acc.get('quota')}")
    tokens = hz.get("tokens")
    if tokens:
        print("[token health]")
        now = time.time()
        for aid, info in tokens.items():
            exp = info.get("expires_in_sec")
            exp_s = f"{exp}s" if isinstance(exp, int) else "n/a"
            lu = info.get("last_used")
            lu_s = time.strftime("%H:%M:%S", time.localtime(lu)) if lu else "never"
            print(f"  - {aid:<18} health={info.get('health'):<9} "
                  f"expires_in={exp_s:<8} last_used={lu_s}")
    return 0


def _auth(args) -> int:
    if args.app != "antigravity":
        print(f"auth for '{args.app}' is not implemented yet", file=sys.stderr)
        return 1
    try:
        oa.run_bootstrap()
    except KeyboardInterrupt:
        print("\n[aborted]", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="aigw", description="unified desktop-quota gateway")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("start", help="run the gateway (uvicorn)")
    sp.add_argument("--config", default=None)
    sp.add_argument("--host", default=None)
    sp.add_argument("--port", type=int, default=None)
    sp.set_defaults(func=_start)

    dp = sub.add_parser("discover", help="protocol-discovery helper (mitmproxy)")
    dp.add_argument("--scan", action="store_true", help="read-only: report detected local sessions")
    dp.add_argument("--app", choices=list(LAUNCH_SNIPPETS), default=None,
                    help="target app to filter capture (cursor|antigravity|workbuddy)")
    dp.add_argument("--hosts", default=None,
                    help="extra upstream hosts to capture (comma-separated); "
                         "needed for workbuddy whose host is unknown")
    dp.add_argument("--report", default=None,
                    help="regenerate the discovery report from a captured dir "
                         "(offline, no mitmproxy)")
    dp.add_argument("--dry-run", action="store_true", help="print commands, do not execute")
    dp.add_argument("--yes", action="store_true", help="actually launch mitmdump")
    dp.set_defaults(func=_discover)

    stp = sub.add_parser("status", help="query a running gateway")
    stp.add_argument("--config", default=None)
    stp.set_defaults(func=_status)

    ap = sub.add_parser("auth", help="OAuth bootstrap — obtain a provider refresh token")
    ap.add_argument("app", nargs="?", default="antigravity",
                    choices=["antigravity"], help="provider to bootstrap")
    ap.set_defaults(func=_auth)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
