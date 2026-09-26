#!/usr/bin/env python3
"""Host-side Pico serial simulator for dry development.

Run: python host_sim.py
Point PLOBI_PICO_SERIAL at a virtty pair, or use PLOBI_HID_MOCK=1 instead.
"""

from __future__ import annotations

import json
import sys


def main() -> int:
    print("pico2w-sim ready (stdin JSON lines)", flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}), flush=True)
            continue
        op = msg.get("op")
        if op == "ping":
            print(json.dumps({"ok": True, "mode": "sim"}), flush=True)
        elif op == "action":
            print(
                json.dumps(
                    {
                        "ok": True,
                        "mode": "sim",
                        "action": msg.get("type"),
                        "payload": msg.get("payload"),
                    }
                ),
                flush=True,
            )
        else:
            print(json.dumps({"ok": False, "error": f"unknown op {op}"}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
