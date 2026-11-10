"""Which magpie providers are waiting to be claimed (read-only, no network write).

The card list is **our** table (``providers.yaml``): magpie adding a provider
must not put it on the user's screen before somebody claims it. This prints
what is waiting — for the release-watch bot's PR body, and for a human who
wants to add one line.

    python scripts/plobi/check_magpie_providers.py            # human-readable
    python scripts/plobi/check_magpie_providers.py --json     # for a bot

Their roster is read exactly the way the gateway reads it (their own table on
disk first, their CLI as the fallback) — the module is loaded from its file so
this stays runnable without the gateway's dependencies.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TABLE = REPO / "quota_hub" / "quota_hub" / "providers" / "apps" / "magpie" / "providers.yaml"
BINARY_NAMES = ("magpie-cli.exe", "magpie-cli")
THEIR_LIST = REPO / "quota_hub" / "quota_hub" / "providers" / "apps" / "magpie" / "their_list.py"


def their_list_module():
    """The gateway's own reader of magpie's roster, loaded from its file.

    Loaded by path (not by import) so this script needs nothing but the
    standard library — it is run by a release-watch bot on its own.
    """
    spec = importlib.util.spec_from_file_location("plobi_magpie_their_list", THEIR_LIST)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:
        return None
    return module


def config_roots() -> list[Path]:
    """Where magpie's data may be — the same places the gateway looks.

    ``XDG_CONFIG_HOME`` first (that is what the desktop pins when it starts
    magpie), then ``PLOBI_HOME``, then the user's own ``~/.config``.
    """
    roots: list[Path] = []
    xdg = (os.environ.get("XDG_CONFIG_HOME") or "").strip()
    if xdg:
        roots.append(Path(xdg))
    home = (os.environ.get("PLOBI_HOME") or "").strip()
    if home:
        roots.append(Path(home))
    roots.append(Path.home() / ".config")
    return roots


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


def find_binary() -> str | None:
    """magpie's CLI, if this machine has one we may run."""
    binary = str(os.environ.get("PLOBI_MAGPIE_BINARY") or "").strip()
    if binary and Path(binary).is_file():
        return binary
    for name in ("magpie-cli", "magpie"):
        found = shutil.which(name)
        if found:
            return found
    home = os.environ.get("PLOBI_HOME") or ""
    if home:
        for candidate in (
            Path(home) / "magpie" / "magpie-cli.exe",
            Path(home) / "magpie" / "magpie-cli",
        ):
            if candidate.is_file():
                return str(candidate)
    return None


def their_ids() -> set[str] | None:
    """The provider ids magpie reports today, or None when we cannot ask.

    Their own table on disk first (that is what the gateway reads), their CLI
    as the fallback — the same two sources, in the same order, so this script
    and the gateway never disagree about who is on the list.
    """
    module = their_list_module()
    if module is not None:
        for root in config_roots():
            table = module.read_provider_table(root)
            if table:
                ids = module.ids_from_table(table)
                if ids:
                    return ids

    binary = find_binary()
    if not binary:
        return None
    if module is not None:
        return module.cli_provider_ids(binary, env={**os.environ, "DO_NOT_TRACK": "1"})
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    ours = our_table()
    theirs = their_ids()
    if theirs is None:
        print("magpie 的名单读不到（它的表与 CLI 都问不到）—— 没有可比的东西。")
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
