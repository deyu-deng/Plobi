"""Collector configuration.

Two collection modes (config key ``mode`` in ``$PLOBI_HOME/plobi/chatlog.json``):

* ``"blacklist"`` — **现行默认**（ADR-0010 修订 2026-08-30 + 裁定 50 + 裁定 100 一）。
  读所有会话，只跳过 ``blacklist`` 里点名的；空黑名单＝**全量**。首次见到的会话默认采。
* ``"whitelist"`` — 只读 ``talkers`` 里点名的，空名单一条不采。这是被推翻的旧默认档，
  留作用户显式选择的更严一档，**不再是缺省**。

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
#: 日采每轮往回带多少天。**原来没人传，就等于只看当天**——于是这台断一次侧车，
#: 中间那几天的消息就永远没人回头捞（Plan 线 10-20 09:5x 要的就是这一格补上）。
#: 它是"往回看多久"，不是"事件发生在多久之内"，两者不许混（裁 189）。
DEFAULT_TIME_WINDOW_DAYS = 30

# 采集口径的两个模式。**现行口径是黑名单**：ADR-0010 修订 2026-08-30 把「只采白名单」
# 改成「黑名单 + 新会话默认采」，2026-09-30 裁定 50 又把新会话那半改成 fail-open
# （首见即采），`Docs/DECISIONS.md` 待决 #3 已答成「默认全采 + 逐个关」。
# 代码默认值一直停在被推翻的那一半上——这就是 10-10 判据 7 要修的。
MODE_WHITELIST = "whitelist"
MODE_BLACKLIST = "blacklist"
CURRENT_MODE = MODE_BLACKLIST
VALID_MODES = (MODE_WHITELIST, MODE_BLACKLIST)


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
    # Collection mode: "blacklist"（现行默认：采全部，除 `blacklist` 里的；空表＝全采）
    # 或 "whitelist"（只采点名，fail-closed——要用户显式写下才走这一档）。
    mode: str = CURRENT_MODE
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
    # 每轮采集往回扫的时间窗（天）。默认 30：够盖住一次周末 + 一次侧车宕机。
    time_window_days: int = DEFAULT_TIME_WINDOW_DAYS
    # 主开关默认**开**：用户 10-09 拍「chatlog 启用」、10-10 又拍「全量执行」。
    # 开着不等于在采——采不到的四种原因由 sidecar 各说一句，不静默、不报"正常"。
    enabled: bool = True
    # Snapshot of the whitelist as it existed before a blacklist-mode migration.
    # Read-only from the collector's perspective — the original 399-name set
    # is preserved here for audit + future name→ID reconciliation work, but
    # has zero effect on ``allows()``.
    archived_whitelist: list[str] = field(default_factory=list)
    archived_at: str = ""  # ISO-8601 timestamp; "" if never migrated.
    # 配置里写了但不被认识的 ``mode`` 原值；空串 = 没被纠正过。它存在的唯一理由就是
    # 「不许静默」：面上要能说出"你写的 X 不认识，我按现行口径 blacklist 在跑"。
    mode_as_written: str = ""

    def allows(self, talker: str) -> bool:
        if not talker:
            return False
        if self.mode == "blacklist":
            return talker not in set(self.blacklist)
        # whitelist：显式选了才走，named-only fail-closed（不再是默认档）
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

        mode_raw = str(data.get("mode") or "").strip().lower()
        env_mode = os.environ.get("PLOBI_CHATLOG_MODE", "").strip().lower()
        mode = env_mode or mode_raw or CURRENT_MODE
        # 拼错一个字母**不许静默退回被推翻的旧口径**——那正是"用户以为在采、其实一条
        # 都没采"的成因。不认识就按现行口径跑，同时把原值留着，让面上说得出"你写的 X 不认识"。
        mode_as_written = "" if mode in VALID_MODES else mode
        if mode not in VALID_MODES:
            mode = CURRENT_MODE
        if not mode_as_written:
            # 上一次纠正过的值也要留得住：save/load 往返还是一次，面上那句话就没了。
            persisted = str(data.get("mode_as_written") or "").strip().lower()
            if persisted and persisted not in VALID_MODES:
                mode_as_written = persisted

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
            mode_as_written=mode_as_written,
            talkers=[str(t) for t in talkers] if isinstance(talkers, list) else [],
            blacklist=[str(b) for b in blacklist] if isinstance(blacklist, list) else [],
            tiers=tiers,
            poll_minutes=int(data.get("poll_minutes") or DEFAULT_POLL_MINUTES),
            time_window_days=max(1, int(data.get("time_window_days")
                                  or DEFAULT_TIME_WINDOW_DAYS)),
            enabled=bool(data.get("enabled", True)),
            archived_whitelist=[str(a) for a in archived if a],
            archived_at=archived_at,
        )

    def save(self, path: Path | str | None = None) -> Path:
        target = Path(path) if path else config_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, object] = {
            "base_url": self.base_url,
            "mode": self.mode,
            "mode_as_written": self.mode_as_written,
            "talkers": self.talkers,
            "blacklist": self.blacklist,
            "poll_minutes": self.poll_minutes,
            "time_window_days": self.time_window_days,
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
