"""Collector configuration.

Two collection modes (config key ``mode`` in ``$PLOBI_HOME/plobi/chatlog.json``):

* ``"whitelist"`` — only the explicitly-named ``talkers`` are ever read.
  Fail-closed: an empty whitelist collects nothing. This is the strict,
  privacy-first default.
* ``"blacklist"`` — read every conversation except those in ``blacklist``.
  An empty blacklist collects *everything* (first-run full ingestion).

Config lives in ``$PLOBI_HOME/plobi/chatlog.json``; env vars override for
tests and unusual deployments. No drive letters are hardcoded.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_BASE_URL = "http://127.0.0.1:5030"
# Webhook delivers in ~13s; this sweep only exists to catch what it dropped.
# 10 minutes keeps the worst case inside the 15-minute notification SLA
# (docs/adr/0005-incremental-rule-plus-model.md, revision note).
DEFAULT_POLL_MINUTES = 10


def config_path() -> Path:
    override = os.environ.get("PLOBI_CHATLOG_CONFIG", "").strip()
    if override:
        return Path(override)
    try:
        from plobi_constants import get_plobi_home

        root = get_plobi_home() / "plobi"
    except Exception:
        root = Path.home() / ".plobi" / "plobi"
    return root / "chatlog.json"


@dataclass
class CollectorConfig:
    base_url: str = DEFAULT_BASE_URL
    # Collection mode: "whitelist" (named-only, fail-closed) or
    # "blacklist" (collect all except `blacklist`; empty = collect all).
    mode: str = "whitelist"
    # Whitelist set — used when mode == "whitelist". Empty means collect nothing.
    talkers: list[str] = field(default_factory=list)
    # Blacklist set — used when mode == "blacklist". Empty means collect all.
    blacklist: list[str] = field(default_factory=list)
    # Per-talker grade for collection priority. Human-declared only — the
    # collector never auto-grades. Known talkers absent from this map default
    # to "info". Excluded talkers are gated upstream by the scope (mode /
    # blacklist); tier does not reopen a closed door. Valid grades: info, task.
    tiers: dict[str, str] = field(default_factory=dict)
    poll_minutes: int = DEFAULT_POLL_MINUTES
    enabled: bool = False
    # Snapshot of the whitelist as it existed before a blacklist-mode migration.
    # Read-only from the collector's perspective — the original 399-name set
    # is preserved here for audit + future name→ID reconciliation work, but
    # has zero effect on ``allows()``.
    archived_whitelist: list[str] = field(default_factory=list)
    archived_at: str = ""  # ISO-8601 timestamp; "" if never migrated.

    def allows(self, talker: str) -> bool:
        if not talker:
            return False
        if self.mode == "blacklist":
            return talker not in set(self.blacklist)
        # whitelist (default): named-only, fail-closed
        return talker in set(self.talkers)

    def tier_of(self, talker: str) -> str:
        """Per-talker grade for collection priority.

        Returns the declared grade, or ``"info"`` for anything not explicitly
        listed. Excluded talkers are gated by the scope layer, not here — tier
        only influences *how* an in-scope message is confirmed, never *whether*
        it is collected.
        """
        return self.tiers.get(talker, "info")

    @classmethod
    def load(cls, path: Path | str | None = None) -> "CollectorConfig":
        target = Path(path) if path else config_path()
        data: dict = {}
        if target.exists():
            try:
                loaded = json.loads(target.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    data = loaded
            except (OSError, json.JSONDecodeError):
                data = {}

        talkers = data.get("talkers")
        env_talkers = os.environ.get("PLOBI_CHATLOG_TALKERS", "").strip()
        if env_talkers:
            talkers = [t.strip() for t in env_talkers.split(",") if t.strip()]

        mode = str(data.get("mode") or "whitelist").strip().lower()
        env_mode = os.environ.get("PLOBI_CHATLOG_MODE", "").strip().lower()
        if env_mode in ("whitelist", "blacklist"):
            mode = env_mode
        if mode not in ("whitelist", "blacklist"):
            mode = "whitelist"

        blacklist = data.get("blacklist")
        env_blacklist = os.environ.get("PLOBI_CHATLOG_BLACKLIST", "").strip()
        if env_blacklist:
            blacklist = [t.strip() for t in env_blacklist.split(",") if t.strip()]

        # Per-talker grades. Keep only valid grades ("info"/"task"); anything
        # else is treated as if undeclared (defaults to "info"), so a typo can
        # never silently invent a priority. No auto-grading happens here.
        raw_tiers = data.get("tiers") or {}
        tiers: dict[str, str] = {}
        if isinstance(raw_tiers, dict):
            for name, grade in raw_tiers.items():
                norm = str(grade).strip().lower()
                if norm in ("info", "task"):
                    tiers[str(name)] = norm

        archived = data.get("archived_whitelist") or []
        if not isinstance(archived, list):
            archived = []
        archived_at = str(data.get("archived_at") or "").strip()

        return cls(
            base_url=str(
                os.environ.get("PLOBI_CHATLOG_URL")
                or data.get("base_url")
                or DEFAULT_BASE_URL
            ).rstrip("/"),
            mode=mode,
            talkers=[str(t) for t in talkers] if isinstance(talkers, list) else [],
            blacklist=[str(b) for b in blacklist] if isinstance(blacklist, list) else [],
            tiers=tiers,
            poll_minutes=int(data.get("poll_minutes") or DEFAULT_POLL_MINUTES),
            enabled=bool(data.get("enabled", False)),
            archived_whitelist=[str(a) for a in archived if a],
            archived_at=archived_at,
        )

    def save(self, path: Path | str | None = None) -> Path:
        target = Path(path) if path else config_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, object] = {
            "base_url": self.base_url,
            "mode": self.mode,
            "talkers": self.talkers,
            "blacklist": self.blacklist,
            "poll_minutes": self.poll_minutes,
            "enabled": self.enabled,
        }
        if self.tiers:
            # Only written when non-empty; an absent key means "all defaults to
            # info", so old configs (no tiers key) load and save untouched.
            payload["tiers"] = self.tiers
        if self.archived_whitelist:
            payload["archived_whitelist"] = self.archived_whitelist
        if self.archived_at:
            payload["archived_at"] = self.archived_at
        data = json.dumps(payload, ensure_ascii=False, indent=2)
        # Atomic write: write to temp file then rename, so a crash mid-write
        # never leaves a truncated/corrupted config file.
        tmp = target.with_suffix(".tmp")
        try:
            tmp.write_text(data, encoding="utf-8")
            tmp.replace(target)
        except BaseException:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise
        return target
