"""每条信源最后一次**真的落库**在什么时候 —— 用来回答"采集还在不在跑"。

存在的理由是一条我们反复量到的坑：**代码合了 ≠ 会在采**。配置齐、端点在、产物在，
都可能一行新数据都没进过。所以这里不去读"配了什么"，只读 `events` 表里每个 `source`
最后一次写入的时刻，并把"距今多少天"翻成一句状态话。

三条口径：
- **只报读数，不猜原因**。这里没有"为什么停了"的答案——只有"最后一次动是几点"。
  猜"学期中没变更"和猜"登录态过期"一样是编故事。
- 判"活不活"用 `updated_at`（重拉会刷新它，即使没有新行），新数据用 `created_at`；
  两格都给出，不合并成一个数——合并过一次的下次就是把"没进新东西"说成"活着"。
- 认不出的状态**不编句子**：表里没有的 source 一律 `never`，库不在就是 `no_store`。
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

STATE_LIVE = "live"
STATE_STALE = "stale"
STATE_NEVER = "never"
STATE_NO_STORE = "no_store"

#: 超过这个天数没进过东西就算"这条没在跑"。不是产品参数，是一条读数阈值：
#: 课表类信源按学期日更，三天足够跨过任何一次正常静默。
STALE_AFTER_DAYS = 3.0

_NOTES = {
    STATE_LIVE: "最近还在写（这一句只说写没写，不说写得对不对）。",
    STATE_STALE: (
        "超时没进新东西：这一条没有在跑。它**不是**「配好了等着」——"
        "想知道为什么停，看同一条信源的 state 与那一句失败话，别在这里猜。"
    ),
    STATE_NEVER: "库里就没有这一条信源的任何一行：从来没有成功采过。",
    STATE_NO_STORE: "读不到日程库（`agenda.db` 不在或读不开）：这个探针对整条采集链什么都不能说。",
}


def note_for(state: str) -> str:
    if state not in _NOTES:
        return f"没记到的信源活性状态 {state!r}：不去猜它属于哪一种。"
    return _NOTES[state]


@dataclass(frozen=True)
class SourceLiveness:
    source: str
    state: str
    events: int
    last_created_at: str
    last_updated_at: str
    age_days: Optional[float]
    note: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _iso(value: Any) -> str:
    return str(value) if value else ""


def _age_days(iso_ts: str, now: datetime) -> Optional[float]:
    if not iso_ts:
        return None
    try:
        parsed = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return round((now - parsed).total_seconds() / 86400.0, 2)


def read_source_liveness(
    db_path: Optional[Path] = None,
    *,
    now: Optional[datetime] = None,
    stale_after_days: float = STALE_AFTER_DAYS,
    expected_sources: tuple[str, ...] = (),
) -> dict[str, Any]:
    """每条信源的活性。`expected_sources` 里没出现过的信源补一行 `never`，不静默省略。"""
    reference = now or datetime.now()
    path = Path(db_path) if db_path else _default_db()

    if path is None or not path.is_file():
        return {
            "checked_at": reference.isoformat(timespec="seconds"),
            "stale_after_days": stale_after_days,
            "sources": [SourceLiveness(source, STATE_NO_STORE, 0, "", "", None, note_for(STATE_NO_STORE)).as_dict()
                        for source in (expected_sources or ("(unknown)",))],
            "verdict": "读不到日程库，这条探针什么都不能说",
        }

    rows: dict[str, tuple[int, str, str]] = {}
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        for row in conn.execute(
            "SELECT source, COUNT(*) AS n, MAX(created_at) AS created, MAX(updated_at) AS updated"
            " FROM events GROUP BY source"
        ):
            rows[str(row["source"] or "")] = (int(row["n"] or 0), _iso(row["created"]), _iso(row["updated"]))
    except sqlite3.Error as exc:                      # 表不存在 / 库被独占：说读不到，不编 0 条
        logger.debug("liveness probe failed: %s", exc)
        return {
            "checked_at": reference.isoformat(timespec="seconds"),
            "stale_after_days": stale_after_days,
            "sources": [SourceLiveness(source, STATE_NO_STORE, 0, "", "", None, note_for(STATE_NO_STORE)).as_dict()
                        for source in (expected_sources or ("(unknown)",))],
            "verdict": f"读不动日程库：{exc}",
        }
    finally:
        conn.close()

    for source in expected_sources:
        rows.setdefault(source, (0, "", ""))

    out: list[SourceLiveness] = []
    for source, (count, created, updated) in sorted(rows.items()):
        if not count:
            state = STATE_NEVER
            age = None
        else:
            age = _age_days(updated or created, reference)
            state = STATE_STALE if age is None or age > stale_after_days else STATE_LIVE
        out.append(SourceLiveness(source, state, count, _iso(created), _iso(updated), age, note_for(state)))

    live = [s for s in out if s.state == STATE_LIVE]
    if not out:
        verdict = "库里一条信源都没有：这条链没有在采"
    elif not live:
        verdict = "每一条信源都超时没进新东西 —— 采集没有在跑"
    else:
        verdict = f"{len(live)} 条还在写：" + "、".join(s.source for s in live)
    return {
        "checked_at": reference.isoformat(timespec="seconds"),
        "stale_after_days": stale_after_days,
        "sources": [s.as_dict() for s in out],
        "verdict": verdict,
    }


def _default_db() -> Optional[Path]:
    try:
        from plobi_constants import get_plobi_home

        return get_plobi_home() / "plobi" / "agenda.db"
    except Exception:                                 # noqa: BLE001 - 常量读不到就是探针不可用
        return None
