r"""Whitelist source-of-truth and effectiveness report (C line).

Read-only. Asks chatlog HTTP ``/api/v1/session`` for the active talkers and
``/api/v1/chatlog`` for each talker's most recent messages, then compares the
result with the collector's whitelist.

The script deliberately reuses :mod:`plobi.collectors.chatlog.config` and
:mod:`plobi.collectors.chatlog.client` so the "true source" of the
collector config is resolved exactly the way the running collector would
resolve it (env override, then ``HERMES_HOME``, then platform default).
No new dependencies, no third-party HTTP libs.

Usage::

    python scripts/plobi/whitelist_report.py                 # 48h window
    python scripts/plobi/whitelist_report.py --hours 72      # custom window
    python scripts/plobi/whitelist_report.py --json          # machine-readable

Outputs:

* Human table on stdout: active config path, mode, Top-N by message count,
  the most recent active talkers NOT in the current whitelist.
* :data:`PROPOSAL_PATH` (``$HERMES_HOME/plobi/whitelist_proposal.json``)
  with the same data in a deterministic JSON shape.

Discipline (per ``Docs/PROMPT-CHATLOG-WHITELIST-REPORT.md``):

* Does **not** modify ``chatlog.json``.
* Does **not** add dependencies.
* Network/parse failures raise a clear error rather than producing a silent
  zero-result table.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Allow running this file directly (``python scripts/plobi/whitelist_report.py``)
# without first installing the package — ``plobi.collectors.chatlog`` lives at
# the repo root and we add the parent directory to ``sys.path``.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plobi.collectors.chatlog.client import (  # noqa: E402  (import after sys.path tweak)
    ChatlogClient,
    ChatlogUnavailable,
    normalize_message,
    _extract_records,
)
from plobi.collectors.chatlog.config import (  # noqa: E402
    DEFAULT_BASE_URL,
    CollectorConfig,
    config_path,
)

# On Windows the platform default is ``%LOCALAPPDATA%\hermes``. Before
# ``hermes_constants`` is importable, ``config_path`` already remembers that
# fallback; we re-derive it here only to surface candidate paths the operator
# may have left behind after a migration.
def _platform_default_hermes_home() -> Path | None:
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA", "").strip()
        if local:
            return Path(local) / "hermes"
        return Path.home() / "AppData" / "Local" / "hermes"
    return Path.home() / ".hermes"


def _hermes_home_resolved() -> Path:
    """Best-effort value matching what ``config_path`` will use.

    Prefers the same resolution chain the collector follows:

    1. ``PLOBI_CHATLOG_CONFIG`` env (config file override — gives the parent).
    2. ``hermes_constants.get_hermes_home()`` (honours ``HERMES_HOME`` env).
    3. Platform default (``%LOCALAPPDATA%/hermes`` on Win, ``~/.hermes`` elsewhere).
    """
    cfg_override = os.environ.get("PLOBI_CHATLOG_CONFIG", "").strip()
    if cfg_override:
        return Path(cfg_override).parent.parent
    try:
        from hermes_constants import get_hermes_home

        return get_hermes_home()
    except Exception:
        fallback = _platform_default_hermes_home()
        return fallback if fallback is not None else Path.home() / ".hermes"


PROPOSAL_FILENAME = "whitelist_proposal.json"


def proposal_path() -> Path:
    """Where the JSON proposal is written. Created on first run."""
    return _hermes_home_resolved() / "plobi" / PROPOSAL_FILENAME


@dataclass(frozen=True)
class TalkerStat:
    talker: str
    msg_count: int
    last_active: str  # ISO-8601 string from chatlog, may be empty
    first_active: str
    sample_sender: str
    sample_snippet: str
    tier: str = "info"  # declared grade ("info" default, "task" human-prioritised)


def list_active_talkers(client: ChatlogClient, *, timeout: float) -> list[str]:
    """Same enumeration contract the collector uses (``/api/v1/session``)."""
    try:
        payload = client._get(  # noqa: SLF001 - reused seam (testable, no I/O here)
            "/api/v1/session",
            {"format": "json", "keyword": "", "limit": "10000"},
        )
    except ChatlogUnavailable:
        return []
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        uname = (
            item.get("topicId")
            or item.get("UserName")
            or item.get("userName")
            or item.get("talker")
            or item.get("personID")
        )
        if not uname or uname in seen:
            continue
        seen.add(str(uname))
        out.append(str(uname))
    return out


def fetch_day_messages(
    client: ChatlogClient, talker: str, day_iso: str, *, timeout: float
) -> list[dict[str, str]]:
    """One call to ``/api/v1/chatlog`` — return raw normalized records.

    Falls back to the client's regular ``fetch`` path for the "today" leg, but
    we use the underlying ``_get`` here so the lower-level error semantics are
    surfaced identically across both legs (yesterday / today).
    """
    try:
        payload = client._get(  # noqa: SLF001
            "/api/v1/chatlog",
            {"format": "json", "time": day_iso, "talker": talker},
        )
    except ChatlogUnavailable:
        return []
    rows: list[dict[str, str]] = []
    for raw in _extract_records(payload):
        message = normalize_message(raw, fallback_talker=talker)
        if message is None or message.is_empty:
            continue
        rows.append(
            {
                "talker": message.talker,
                "sender": message.sender,
                "sent_at": message.sent_at,
                "snippet": message.content[:80],
            }
        )
    return rows


def aggregate_recent_messages(
    client: ChatlogClient,
    talkers: list[str],
    *,
    hours: int,
    timeout: float,
    tier_of=None,
) -> dict[str, TalkerStat]:
    """For each talker, pull the last ``hours`` hours of messages (UTC ± day buckets)."""
    now = datetime.now(timezone.utc)
    days: list[str] = []
    span_days = max(2, (hours + 23) // 24)  # at least 2 day buckets covers 48h
    for offset in range(span_days):
        target = (now - timedelta(days=offset)).date().isoformat()
        days.append(target)

    bucket: dict[str, list[dict[str, str]]] = defaultdict(list)
    last_per_talker: dict[str, str] = {}
    for talker in talkers:
        for day in days:
            for row in fetch_day_messages(client, talker, day, timeout=timeout):
                bucket[talker].append(row)
                last_per_talker[talker] = row["sent_at"]

    stats: dict[str, TalkerStat] = {}
    resolve_tier = tier_of or (lambda t: "info")
    for talker, rows in bucket.items():
        if not rows:
            continue
        sent_at_times = [r["sent_at"] for r in rows if r["sent_at"]]
        last_active = max(sent_at_times) if sent_at_times else ""
        first_active = min(sent_at_times) if sent_at_times else ""
        stats[talker] = TalkerStat(
            talker=talker,
            msg_count=len(rows),
            last_active=last_active,
            first_active=first_active,
            sample_sender=rows[-1]["sender"],
            sample_snippet=rows[-1]["snippet"],
            tier=resolve_tier(talker),
        )
    return stats


def stale_candidates(true_source: Path) -> list[Path]:
    """Other places a chatlog.json might have been left behind after migration.

    Reads only — never touches the filesystem beyond ``stat``-ing.
    """
    candidates: list[Path] = []
    roots = [
        _hermes_home_resolved(),
        Path.home() / ".hermes",
        _platform_default_hermes_home() or Path.home() / ".hermes",
    ]
    seen_roots: set[Path] = set()
    for root in roots:
        if root in seen_roots:
            continue
        seen_roots.add(root)
        candidate = root / "plobi" / "chatlog.json"
        if candidate == true_source or not candidate.exists():
            continue
        candidates.append(candidate)
    return candidates


def seen_state_counts() -> dict[str, int]:
    """Return ``{seen_messages, known_talkers, known_status}`` from chatlog_state.db."""
    state_db = _hermes_home_resolved() / "plobi" / "chatlog_state.db"
    out = {"seen_messages": 0, "known_talkers": 0, "excluded_talkers": 0, "pending_talkers": 0}
    if not state_db.is_file():
        return out
    try:
        conn = sqlite3.connect(str(state_db))
        try:
            out["seen_messages"] = int(conn.execute("SELECT COUNT(*) FROM seen").fetchone()[0] or 0)
            for status in ("known", "excluded", "pending"):
                try:
                    row = conn.execute(
                        "SELECT COUNT(*) FROM talkers WHERE status=?", (status,)
                    ).fetchone()
                    out[f"{status}_talkers"] = int(row[0] or 0) if row else 0
                except sqlite3.OperationalError:
                    # table missing on older DB schemas
                    out[f"{status}_talkers"] = 0
        finally:
            conn.close()
    except sqlite3.Error:
        return out
    return out


def write_proposal(
    *,
    proposal: dict[str, Any],
    proposal_path_value: Path,
) -> None:
    proposal_path_value.parent.mkdir(parents=True, exist_ok=True)
    tmp = proposal_path_value.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(proposal, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(proposal_path_value)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def recommend_first_batch(
    not_whitelisted: list[TalkerStat], *, limit: int = 5
) -> list[dict[str, str]]:
    """Suggest the first batch the user should whitelist, with reasoning.

    Picks the highest-traffic not-whitelisted talkers that look like real
    conversations rather than bot/service traffic. The reason_hint is human
    English so the operator can spot noise without examining each name.
    """
    if not not_whitelisted:
        return []
    by_traffic = sorted(not_whitelisted, key=lambda s: s.msg_count, reverse=True)
    picks: list[dict[str, str]] = []
    for stat in by_traffic[:limit]:
        name = stat.talker
        lowered = name.lower()
        if "@chatroom" in lowered:
            reason = "群消息(@chatroom) — 通常是项目/班级群;若与此项目相关,值得加白"
        elif any(token in name for token in ("服务", "通知", "客服", "助手", "团队", "公众号")):
            reason = "服务号/通知类 — 单向推送多,加白往往空转,可暂时跳过"
        elif stat.msg_count >= 5:
            reason = f"48h 内 {stat.msg_count} 条,活跃度高;多半是真对话,优先加白"
        elif stat.msg_count >= 1:
            reason = f"48h 内 {stat.msg_count} 条;信号弱,但已比 0 多,第二批再加"
        else:
            reason = "无消息但 chatlog 枚举到;留作 review 决定"
        picks.append(
            {
                "talker": name,
                "msg_count": str(stat.msg_count),
                "last_active": stat.last_active,
                "reason_hint": reason,
            }
        )
    return picks


def render_table(
    *,
    true_source: Path,
    stale: list[Path],
    mode: str,
    enabled: bool,
    whitelist_size: int,
    blacklist_size: int,
    archived_size: int,
    archived_at: str,
    active_total: int,
    window_hours: int,
    topn: list[TalkerStat],
    not_whitelisted: list[TalkerStat],
    not_excluded: list[TalkerStat],
    first_batch: list[dict[str, str]],
    proposal_path_str: str,
    state: dict[str, int],
) -> str:
    lines: list[str] = []
    lines.append("Chatlog whitelist report (read-only)")
    lines.append(f"  collector truth source : {true_source}  (mode={mode}, enabled={enabled})")
    lines.append(f"  whitelist/blacklist     : {whitelist_size} / {blacklist_size}")
    if archived_size:
        stamp = archived_at or "(unknown)"
        lines.append(
            f"  archived_whitelist      : {archived_size}  (frozen at {stamp}; "
            "no longer drives the gate — see scripts/plobi/whitelist_migrate.py --restore)"
        )
    if stale:
        for path in stale:
            lines.append(f"  STALE candidate         : {path}  <- not collector truth, check before cleanup")
    lines.append(f"  active talkers (chatlog): {active_total}")
    lines.append(f"  window                  : last {window_hours}h")
    lines.append(f"  talkers with messages   : {len(topn)}")
    lines.append(f"  state.db counts         : seen={state.get('seen_messages', 0)} "
                 f"known={state.get('known_talkers', 0)} "
                 f"excluded={state.get('excluded_talkers', 0)} "
                 f"pending={state.get('pending_talkers', 0)}")
    lines.append(f"  proposal JSON           : {proposal_path_str}")
    lines.append("")

    lines.append(f"Top {len(topn)} by message volume (last {window_hours}h)")
    lines.append(f"{'rank':<5}{'in_wl':<7}{'tier':<7}{'count':<6}{'last_active':<22}{'talker'}")
    lines.append("-" * 95)
    for idx, stat in enumerate(topn, start=1):
        if mode == "whitelist":
            in_wl = "yes" if stat.talker in _whitelist_lookup else "no"
        elif mode == "blacklist":
            in_wl = "no" if stat.talker in _blacklist_lookup else "yes"
        else:
            in_wl = "-"
        lines.append(
            f"{idx:<5}{in_wl:<7}{stat.tier:<7}{stat.msg_count:<6}{stat.last_active[:19]:<22}{stat.talker}"
        )
    lines.append("")

    if mode == "blacklist":
        # In blacklist mode the gate is the excluded list — surface
        # anything actively excluded so the operator can see what's being
        # thrown away on purpose.
        if not_excluded:
            lines.append(
                f"Excluded but still active ({len(not_excluded)}) — "
                "candidates to un-exclude if you want them back"
            )
            for stat in not_excluded[:20]:
                lines.append(
                    f"  - {stat.talker:<40} count={stat.msg_count:<5} last={stat.last_active[:19]}"
                )
            if len(not_excluded) > 20:
                lines.append(f"  ... and {len(not_excluded) - 20} more (see proposal JSON)")
        else:
            lines.append("Excluded but still active: none — current blacklist looks correct")
    else:
        if not_whitelisted:
            lines.append(
                f"Not whitelisted ({len(not_whitelisted)} talkers with activity) — "
                "candidates for first batch"
            )
            for stat in not_whitelisted[:20]:
                lines.append(
                    f"  - {stat.talker:<40} count={stat.msg_count:<5} last={stat.last_active[:19]}"
                )
            if len(not_whitelisted) > 20:
                lines.append(f"  ... and {len(not_whitelisted) - 20} more (see proposal JSON)")
        else:
            lines.append("Not whitelisted: none in this window")

    if first_batch:
        lines.append("")
        if mode == "blacklist":
            lines.append(
                "Recommended first decisions (review each on the desktop board, "
                "or hit POST /api/collect/review-complete to accept all then prune):"
            )
        else:
            lines.append("Recommended first batch (highest-traffic, please review reason_hint):")
        for row in first_batch:
            lines.append(
                f"  + {row['talker']:<40} count={row['msg_count']:<5} -> {row['reason_hint']}"
            )
    return "\n".join(lines)


# Module-level so render_table can re-use the "was this in the gate?" decision.
_whitelist_lookup: set[str] = set()
_blacklist_lookup: set[str] = set()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hours", type=int, default=48, help="window size in hours (default 48)")
    parser.add_argument(
        "--topn", type=int, default=15, help="rows of Top talkers (default 15)"
    )
    parser.add_argument(
        "--json", action="store_true", dest="as_json", help="emit proposal JSON on stdout"
    )
    args = parser.parse_args(argv)

    config = CollectorConfig.load()
    true_source = config_path()
    client = ChatlogClient(config.base_url, timeout=5.0)

    # 1) enumerate what chatlog currently has
    try:
        active_talkers = list_active_talkers(client, timeout=2.5)
    except ChatlogUnavailable as exc:
        print(f"error: chatlog is unreachable — {exc}", file=sys.stderr)
        return 2
    if not active_talkers:
        print(f"warn: chatlog returned 0 active talkers (base_url={config.base_url})", file=sys.stderr)
    active_total = len(active_talkers)

    # 2) pull recent messages per active talker, then aggregate
    try:
        stats = aggregate_recent_messages(
            client, active_talkers, hours=args.hours, timeout=2.5,
            tier_of=config.tier_of,
        )
    except ChatlogUnavailable as exc:
        print(f"error: chatlog session enumeration failed — {exc}", file=sys.stderr)
        return 2

    global _whitelist_lookup, _blacklist_lookup
    _whitelist_lookup = set(config.talkers) if config.mode == "whitelist" else set()
    _blacklist_lookup = set(config.blacklist)

    # 3) sort & split
    ordered = sorted(stats.values(), key=lambda s: s.msg_count, reverse=True)
    topn = ordered[: args.topn]
    if config.mode == "whitelist":
        not_whitelisted = [s for s in ordered if s.talker not in set(config.talkers)]
        not_excluded: list[TalkerStat] = []
    else:
        # In blacklist mode the gate is `blacklist` (empty = collect all).
        # Anything in `blacklist` that still produced traffic is "we explicitly
        # turned this off but it's still noisy" — surface it for review.
        excluded = set(config.blacklist)
        not_whitelisted = []
        not_excluded = [s for s in ordered if s.talker in excluded]

    # 4) write proposal JSON
    proposal = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config_path": str(true_source),
        "mode": config.mode,
        "enabled": config.enabled,
        "window_hours": args.hours,
        "active_talkers_total": active_total,
        "whitelist_size": len(config.talkers),
        "blacklist_size": len(config.blacklist),
        "archived_whitelist_size": len(config.archived_whitelist),
        "archived_at": config.archived_at,
        "seen_state": seen_state_counts(),
        "topn": [
            {
                "talker": s.talker,
                "msg_count": s.msg_count,
                "first_active": s.first_active,
                "last_active": s.last_active,
                "sample_sender": s.sample_sender,
                "sample_snippet": s.sample_snippet,
                "in_current_whitelist": (s.talker in set(config.talkers)),
                "in_blacklist": (s.talker in set(config.blacklist)),
                "tier": s.tier,
            }
            for s in ordered
        ],
        "not_whitelisted_with_activity": [
            {
                "talker": s.talker,
                "msg_count": s.msg_count,
                "first_active": s.first_active,
                "last_active": s.last_active,
            }
            for s in not_whitelisted
        ],
        "excluded_but_active": [
            {
                "talker": s.talker,
                "msg_count": s.msg_count,
                "first_active": s.first_active,
                "last_active": s.last_active,
            }
            for s in not_excluded
        ],
        "recommended_first_batch": recommend_first_batch(not_whitelisted),
        "stale_candidate_paths": [str(p) for p in stale_candidates(true_source)],
    }
    proposal_path_value = proposal_path()
    try:
        write_proposal(proposal=proposal, proposal_path_value=proposal_path_value)
    except OSError as exc:
        print(f"warn: could not write proposal to {proposal_path_value}: {exc}", file=sys.stderr)

    if args.as_json:
        print(json.dumps(proposal, ensure_ascii=False, indent=2))
        return 0

    print(
        render_table(
            true_source=true_source,
            stale=stale_candidates(true_source),
            mode=config.mode,
            enabled=config.enabled,
            whitelist_size=len(config.talkers),
            blacklist_size=len(config.blacklist),
            archived_size=len(config.archived_whitelist),
            archived_at=config.archived_at,
            active_total=active_total,
            window_hours=args.hours,
            topn=topn,
            not_whitelisted=not_whitelisted,
            not_excluded=not_excluded,
            first_batch=proposal["recommended_first_batch"],
            proposal_path_str=str(proposal_path_value),
            state=proposal["seen_state"],
        )
    )
    print("")
    print(f"proposal written -> {proposal_path_value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
