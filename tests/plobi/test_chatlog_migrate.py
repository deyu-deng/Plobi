"""chatlog config schema: ``archived_whitelist`` roundtrip + migration script."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from plobi.collectors.chatlog.config import CollectorConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts" / "plobi"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import whitelist_migrate  # noqa: E402  (import after sys.path tweak)


@pytest.fixture
def whitelist_config(tmp_path: Path) -> Path:
    """Write a whitelist-mode config the migration can consume."""
    target = tmp_path / "chatlog.json"
    target.write_text(
        json.dumps(
            {
                "base_url": "http://127.0.0.1:5030",
                "mode": "whitelist",
                "talkers": ["mom", "work-group", "ops"],
                "blacklist": [],
                "poll_minutes": 10,
                "enabled": True,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return target


def test_load_preserves_archived_fields(whitelist_config: Path) -> None:
    cfg = CollectorConfig.load(whitelist_config)
    # No archive yet — defaults are empty
    assert cfg.archived_whitelist == []
    assert cfg.archived_at == ""


def test_save_roundtrips_archive(tmp_path: Path) -> None:
    cfg = CollectorConfig(
        mode="blacklist",
        talkers=[],
        blacklist=["@chatroom-noisy"],
        archived_whitelist=["mom", "work-group"],
        archived_at="2026-09-06T12:00:00+00:00",
    )
    target = tmp_path / "chatlog.json"
    cfg.save(target)
    raw = json.loads(target.read_text(encoding="utf-8"))
    assert raw["mode"] == "blacklist"
    assert raw["talkers"] == []
    assert raw["blacklist"] == ["@chatroom-noisy"]
    assert raw["archived_whitelist"] == ["mom", "work-group"]
    assert raw["archived_at"] == "2026-09-06T12:00:00+00:00"

    # Reload and verify symmetry.
    reloaded = CollectorConfig.load(target)
    assert reloaded.archived_whitelist == ["mom", "work-group"]
    assert reloaded.archived_at == "2026-09-06T12:00:00+00:00"


def test_save_omits_empty_archive(tmp_path: Path) -> None:
    cfg = CollectorConfig(mode="blacklist", talkers=[], blacklist=[])
    target = tmp_path / "chatlog.json"
    cfg.save(target)
    raw = json.loads(target.read_text(encoding="utf-8"))
    assert "archived_whitelist" not in raw
    assert "archived_at" not in raw


def test_migrate_archives_and_switches_mode(whitelist_config: Path) -> None:
    cfg = CollectorConfig.load(whitelist_config)
    result = whitelist_migrate._apply_migration(cfg, target=whitelist_config)
    assert result["changed"] is True
    assert result["archived"] == 3
    assert Path(result["backup"]).exists()

    raw = json.loads(whitelist_config.read_text(encoding="utf-8"))
    assert raw["mode"] == "blacklist"
    assert raw["talkers"] == []
    assert raw["blacklist"] == []
    assert raw["archived_whitelist"] == ["mom", "work-group", "ops"]
    assert raw["archived_at"]  # stamped

    # Re-running is a no-op (idempotent).
    cfg = CollectorConfig.load(whitelist_config)
    second = whitelist_migrate._apply_migration(cfg, target=whitelist_config)
    assert second["changed"] is False
    assert second["reason"] == "already migrated"


def test_migrate_refuses_when_already_blacklist_without_archive(tmp_path: Path) -> None:
    """Refuse to silently overwrite when the operator mis-invokes the script."""
    target = tmp_path / "chatlog.json"
    target.write_text(
        json.dumps(
            {
                "base_url": "http://127.0.0.1:5030",
                "mode": "blacklist",
                "talkers": [],
                "blacklist": ["noise"],
                "poll_minutes": 10,
                "enabled": True,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    cfg = CollectorConfig.load(target)
    result = whitelist_migrate._apply_migration(cfg, target=target)
    assert result["changed"] is False
    assert "Refusing" in result["reason"]


def test_restore_moves_archive_back(whitelist_config: Path) -> None:
    cfg = CollectorConfig.load(whitelist_config)
    whitelist_migrate._apply_migration(cfg, target=whitelist_config)

    migrated = CollectorConfig.load(whitelist_config)
    result = whitelist_migrate._restore_from_archive(migrated, target=whitelist_config)
    assert result["changed"] is True
    assert result["restored_talkers"] == 3

    raw = json.loads(whitelist_config.read_text(encoding="utf-8"))
    assert raw["mode"] == "whitelist"
    assert raw["talkers"] == ["mom", "work-group", "ops"]
    assert raw.get("archived_whitelist", []) == []
    assert raw.get("archived_at", "") == ""


def test_allows_ignores_archive(whitelist_config: Path) -> None:
    """``allows()`` must not honour archived_whitelist — the field is audit-only."""
    cfg = CollectorConfig.load(whitelist_config)
    whitelist_migrate._apply_migration(cfg, target=whitelist_config)

    reloaded = CollectorConfig.load(whitelist_config)
    # Archive contains 3 names but they're frozen — the gate is the empty
    # blacklist, so anything passes through.
    assert reloaded.mode == "blacklist"
    assert reloaded.allows("any-new-talker") is True
    # And an explicitly blacklisted one is still rejected.
    reloaded.blacklist = ["blocked-id"]
    assert reloaded.allows("blocked-id") is False
    assert reloaded.allows("any-other-id") is True