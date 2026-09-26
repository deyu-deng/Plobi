"""Migrate the chatlog config from whitelist mode to blacklist mode (option B).

Reads the current collector config (same path resolution as
``plobi.collectors.chatlog.config.config_path``), takes a timestamped backup,
then atomically rewrites the file with:

* ``mode`` flipped to ``"blacklist"``
* ``talkers`` cleared (they no longer drive the gate)
* ``blacklist`` left as-is (operator-edited; we don't touch it)
* ``archived_whitelist`` populated with the previous whitelist
* ``archived_at`` stamped
* ``enabled`` left as-is

This script is idempotent — running it twice produces the same state because
the source-of-truth whitelist has been moved to ``archived_whitelist`` and
``talkers`` is now empty. The blacklist-mode review gate
(``POST /api/collect/review-complete``) is **not** called here on purpose:
the user reviews each enumerated talker on the desktop board and decides
``known`` vs ``excluded`` from there. See ADR-0010.

Usage::

    python scripts/plobi/whitelist_migrate.py                # migrate in place
    python scripts/plobi/whitelist_migrate.py --dry-run      # show what would change
    python scripts/plobi/whitelist_migrate.py --restore      # revert to archived whitelist

Exit code is non-zero if chatlog.json is missing, malformed, or already in
blacklist mode without an archive (so a second invocation never silently
wipes data).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plobi.collectors.chatlog.config import (  # noqa: E402  (after sys.path tweak)
    CollectorConfig,
    config_path,
)


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _backup_path(target: Path) -> Path:
    return target.with_suffix(target.suffix + f".bak.{_stamp()}")


def plan(config: CollectorConfig) -> dict[str, object]:
    """Return the diff between current state and the post-migration state."""
    return {
        "from_mode": config.mode,
        "to_mode": "blacklist",
        "talkers_will_be_archived": len(config.talkers),
        "talkers_after": [],
        "blacklist_kept": len(config.blacklist),
        "enabled_kept": config.enabled,
        "archived_already": len(config.archived_whitelist),
        "archived_at_current": config.archived_at,
    }


def _restore_from_archive(config: CollectorConfig, *, target: Path) -> dict[str, object]:
    """Reverse a previous migration: copy ``archived_whitelist`` back to ``talkers``."""
    if not config.archived_whitelist:
        return {"changed": False, "reason": "no archived_whitelist to restore"}
    backup = _backup_path(target)
    shutil.copy2(target, backup)
    config.talkers = list(config.archived_whitelist)
    config.archived_whitelist = []
    config.archived_at = ""
    config.mode = "whitelist"
    config.save(target)
    return {"changed": True, "backup": str(backup), "restored_talkers": len(config.talkers)}


def _apply_migration(config: CollectorConfig, *, target: Path) -> dict[str, object]:
    """Switch mode + archive the old whitelist. Idempotent."""
    if config.mode == "blacklist" and config.archived_whitelist and not config.talkers:
        # Already migrated; do nothing, but report the archive so the operator
        # can audit the state without re-running.
        return {
            "changed": False,
            "reason": "already migrated",
            "archived": len(config.archived_whitelist),
            "archived_at": config.archived_at,
        }
    if config.mode == "blacklist" and not config.archived_whitelist:
        # Refuse to silently destroy data — the caller probably mis-invoked.
        return {
            "changed": False,
            "reason": (
                "mode is already blacklist but archived_whitelist is empty — "
                "the original whitelist is gone. Refusing to overwrite."
            ),
        }
    backup = _backup_path(target)
    shutil.copy2(target, backup)
    archived = list(config.talkers)
    archived_at = datetime.now(timezone.utc).isoformat()
    config.talkers = []
    config.mode = "blacklist"
    config.archived_whitelist = archived
    config.archived_at = archived_at
    config.save(target)
    return {
        "changed": True,
        "backup": str(backup),
        "archived": len(archived),
        "archived_at": archived_at,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="print the plan and exit")
    parser.add_argument(
        "--restore",
        action="store_true",
        help="copy archived_whitelist back to talkers (whitelist mode), leave blacklist untouched",
    )
    args = parser.parse_args(argv)

    target = config_path()
    if not target.exists():
        print(f"error: no config at {target}", file=sys.stderr)
        return 2

    config = CollectorConfig.load(target)
    print(f"config_path: {target}")
    print(f"current state: mode={config.mode} talkers={len(config.talkers)} "
          f"blacklist={len(config.blacklist)} enabled={config.enabled}")
    if config.archived_whitelist:
        print(f"already archived: {len(config.archived_whitelist)} names "
              f"(archived_at={config.archived_at})")

    if args.dry_run:
        print("\n--- plan ---")
        print(json.dumps(plan(config), ensure_ascii=False, indent=2))
        return 0

    if args.restore:
        result = _restore_from_archive(config, target=target)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("changed") or result.get("reason") == "no archived_whitelist to restore" else 1

    result = _apply_migration(config, target=target)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result.get("changed") and "reason" in result and result.get("reason") != "already migrated":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())