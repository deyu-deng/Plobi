"""Which magpie providers are waiting to be claimed (read-only, no network write).

The card list is **our** table (``providers.yaml``): magpie adding a provider
must not put it on the user's screen before somebody claims it. This prints
what is waiting — for the release-watch bot's PR body, and for a human who
wants to add one line.

    python scripts/plobi/check_magpie_providers.py            # human-readable
    python scripts/plobi/check_magpie_providers.py --json     # for a bot
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TABLE = REPO / "quota_hub" / "quota_hub" / "providers" / "apps" / "magpie" / "providers.yaml"
BINARY_NAMES = ("magpie-cli.exe", "magpie-cli")


def our_table() -> dict[str, dict]:
    """The providers we claim, from our own table."""
    try:
        import yaml
    except ImportError:
        return {}
    try:
        raw = yaml.safe_load(TABLE.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    out: dict[str, dict] = {}
    for key, row in (raw.get("providers") or {}).items():
        if isinstance(row, dict):
            out[str(key)] = row
    return out


def their_ids() -> set[str] | None:
    """The provider ids magpie reports today, or None when we cannot ask."""
    binary = str(os.environ.get("PLOBI_MAGPIE_BINARY") or "").strip()
    if not binary or not Path(binary).is_file():
        for name in ("magpie-cli", "magpie"):
            found = shutil.which(name)
            if found:
                binary = found
                break
    if not binary or not Path(binary).is_file():
        home = os.environ.get("PLOBI_HOME") or ""
        if home:
            for candidate in (
                Path(home) / "magpie" / "magpie-cli.exe",
                Path(home) / "magpie" / "magpie-cli",
            ):
                if candidate.is_file():
                    binary = str(candidate)
                    break
    if not binary or not Path(binary).is_file():
        return None
    try:
        out = subprocess.run(
            [binary, "plugin", "list", "--json"],
            capture_output=True,
            timeout=20.0,
            env={**os.environ, "DO_NOT_TRACK": "1"},
        )
        payload = json.loads(out.stdout.decode("utf-8", "replace") or "{}")
    except Exception:
        return None
    ids = {
        str(entry.get("id")).strip()
        for entry in (payload.get("providers") or [])
        if isinstance(entry, dict) and entry.get("id")
    }
    return ids or None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    ours = our_table()
    theirs = their_ids()
    if theirs is None:
        print("magpie's CLI could not be run — nothing to compare against.")
        return 0

    upstream = {str(row.get("upstream") or key) for key, row in ours.items()}
    waiting = sorted(theirs - upstream)
    gone = sorted(upstream - theirs)

    if args.json:
        print(json.dumps({"unclaimed": waiting, "no_longer_there": gone}, ensure_ascii=False))
        return 0

    print(f"我们认领的 provider：{len(ours)}")
    if waiting:
        print("等我们认领（不出卡）：")
        for pid in waiting:
            print(f"  - {pid}")
    else:
        print("等我们认领（不出卡）：无")
    if gone:
        print("magpie 那边已经没有了（我们表里还留着，确认一下）：")
        for pid in gone:
            print(f"  - {pid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
