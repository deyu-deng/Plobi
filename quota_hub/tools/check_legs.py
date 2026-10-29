"""After a restart, is every leg you signed into actually usable? One command answers that.

`check_picker.py` proves the *picker* contract (names, one row, default resolves). This
script answers the other half: for each leg the settings screen shows as signed in, does
the hub really offer a model — and, only when asked with `--turn`, does one real turn come
back? No prompt is sent without `--turn`: a turn spends your subscription quota, so the
default read-only pass spends nothing.

Exit code 0 = every signed-in leg offers at least one selectable model (and, with
`--turn`, every one of them answered).

Run (the hub is up on :8000 while the desktop runs):
    cd Code && .venv/Scripts/python.exe quota_hub/tools/check_legs.py
    cd Code && .venv/Scripts/python.exe quota_hub/tools/check_legs.py --turn
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from plobi.agents.registry import aigw_api_key, aigw_base_url  # noqa: E402
import httpx  # noqa: E402


def _get(client: httpx.Client, url: str, key: str):
    res = client.get(url, headers={"Authorization": f"Bearer {key}"}, timeout=20.0)
    res.raise_for_status()
    return res.json()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--turn", action="store_true", help="send one real turn per leg (spends quota)")
    args = ap.parse_args()

    base = aigw_base_url().rstrip("/")
    root = base[:-3] if base.endswith("/v1") else base
    key = aigw_api_key()
    print(f"hub: {base}")

    with httpx.Client() as client:
        try:
            cards = {row["id"]: row for row in _get(client, f"{root}/plobi/auth", key)["data"]}
            models = _get(client, f"{root}/v1/models", key)["data"]
        except Exception as exc:  # noqa: BLE001 — an unreachable hub is the finding
            print(f"FAIL  cannot read the hub at {root}: {type(exc).__name__}: {exc}")
            return 1

        selectable = [m for m in models if m.get("selectable")]
        by_leg: dict[str, list[str]] = {}
        for m in selectable:
            # The row says which leg serves it; the id prefix is the *model* namespace and
            # does not always equal the card id (`kiro` vs `kiro_cli`, `cline` vs `cline_cli`).
            by_leg.setdefault(str(m.get("provider") or m["id"].split("/")[0]), []).append(m["id"])

        print(f"\n{'leg':16s} {'signed_in':9s} {'live':5s} {'models':6s} reason")
        problems: list[str] = []
        for leg, card in sorted(cards.items()):
            rows = by_leg.get(leg, [])
            reason = card.get("signed_in_reason") or card.get("reason") or ""
            signed = bool(card.get("signed_in"))
            print(f"{leg:16s} {str(signed):9s} {str(card.get('live')):5s} {len(rows):6d} {reason}")
            if signed and not rows:
                problems.append(f"{leg}: signed in but the hub offers no model ({reason})")

        if not selectable:
            print("\nFAIL  the hub offers no usable model at all")
            return 1

        if args.turn:
            print("\n--turn: one short prompt per leg that has a model")
            ok = True
            for leg, ids in sorted(by_leg.items()):
                model = ids[0]
                started = time.monotonic()
                try:
                    res = client.post(
                        f"{root}/v1/chat/completions",
                        headers={"Authorization": f"Bearer {key}"},
                        json={
                            "model": model,
                            "messages": [{"role": "user", "content": "只回两个字：能用"}],
                            "stream": False,
                            "max_tokens": 32,
                        },
                        timeout=180.0,
                    )
                    took = time.monotonic() - started
                    if res.status_code != 200:
                        ok = False
                        detail = res.text[:160]
                        print(f"  FAIL {model:34s} {res.status_code} {detail}")
                        continue
                    body = res.json()
                    text = (body["choices"][0]["message"].get("content") or "").strip()
                    print(f"  ok   {model:34s} {took:5.1f}s  {text[:40]!r}")
                except Exception as exc:  # noqa: BLE001
                    ok = False
                    print(f"  FAIL {model:34s} {type(exc).__name__}: {exc}")
            if not ok:
                problems.append("--turn: at least one leg did not answer")

    if problems:
        print("\nPROBLEMS")
        for line in problems:
            print(f"  - {line}")
        return 1

    print(f"\nGREEN — {len(selectable)} usable model(s) across {len(by_leg)} leg(s), every signed-in leg has one")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
