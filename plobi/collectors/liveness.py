"""每条信源最后一次**真的落库**在什么时候 —— 用来回答"采集还在不在跑"。

存在的理由是一条我们反复量到的坑：**代码合了 ≠ 会在采**。配置齐、端点在、产物在，
都可能一行新数据都没进过。所以这里不去读"配了什么"，只读 `events` 表里每个 `source`
最后一次进新条目的时刻，并把"距今多少天"翻成一句状态话。

三条口径：
- **只报读数，不猜原因**。这里没有"为什么停了"的答案——只有"最后一次进新行是几点"。
  猜"学期中没变更"和猜"登录态过期"一样是编故事。
- 判"活不活"只认 `created_at`，**不认 `updated_at`**。这台 2026-10-15 亲眼量到一次反例：
  `timetable` 的 `MAX(updated_at)` 是当天 08:43:30，看着像活着，实际那是**一条日程被确认**时
  写的本地改动（该信源 240 行的 `created_at` 全停在 10-09，只有那一行今天动过）。
  确认一条日程、改一次标题都会刷 `updated_at` ⇒ 拿它判活性会把"有人在动板"说成"有人在采"。
- 探针的天花板也写在这：一次**没有新增**的拉取不在 `events` 里留痕，所以这里只能说
  "最后一次进新条目是几点"，不能说"最后一次跑采集是几点"。想要后者，得采集器自己记一份
  （`zju` 那份 `zju_state.json` 就是它自己记的，不在本探针的通用面里按名字分支）。
- 那一格 `collecting` 只报注册表点没点开关，**不报"有没有人排它"**：这台现在的情况是
  `collecting_sources()` 全仓零调用方（10-15 现核，见 `Docs/reference/agent-register.md` 的 WP-COLLECT-WIN 一格），
  那是"没有执行方"这条现核事实，不是一个能从这里读出来的数——把它写进探针就等于编一个读数。
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

#: 超过这个天数没进过新条目就算"这条没在采"。不是产品参数，是一条读数阈值：
#: 课表类信源按学期日更，三天足够跨过任何一次正常静默。
STALE_AFTER_DAYS = 3.0

_NOTES = {
    STATE_LIVE: (
        "阈值内进过新条目。这一句只说有没有新东西进来，不说写得对不对，"
        "也不说它下一次还会不会进。"
    ),
    STATE_STALE: (
        "阈值内没有新条目进来。它**不等于**「采集器坏了一次」——一次没新增的拉取不在这里留痕；"
        "也**不等于**「板没人动」：本地改动刷的是 `updated_at`，那一列不参与判活。"
        "想知道为什么，去读同一条信源自己的状态与那句失败话。"
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
    #: 板有人动过、但没有新东西进来 —— 只在两个时刻都读得到时才说，不猜。
    touched_after_last_write: bool
    #: 注册表那个 `collecting` 开关点没点。`None` = 这一条不在注册表里（如 `manual`），
    #: 不是"没点"——把读不到说成假，就是下一颗假绿。
    collecting: Optional[bool]
    note: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def collecting_flags() -> dict[str, bool]:
    """注册表里每条信源的采集开关 —— 唯一真源是 `plobi.collectors.registry`，这里不抄名单。"""
    try:
        from plobi.collectors import registry

        return {sid: bool(registry.spec(sid).collecting) for sid in registry.source_ids()}
    except Exception as exc:                        # noqa: BLE001 - 读不到就是这一格没读数
        logger.debug("liveness probe: registry unreadable: %s", exc)
        return {}


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
    switches: Optional[dict[str, bool]] = None,
) -> dict[str, Any]:
    """每条信源的活性。`expected_sources` 里没出现过的信源补一行 `never`，不静默省略。

    `switches` 不传就去问注册表（唯一真源）；传进来是给测试钉读数用的，跳过那次问。
    """
    reference = now or datetime.now()
    flags = collecting_flags() if switches is None else dict(switches)
    path = Path(db_path) if db_path else _default_db()

    if path is None or not path.is_file():
        return {
            "checked_at": reference.isoformat(timespec="seconds"),
            "stale_after_days": stale_after_days,
            "sources": [SourceLiveness(source, STATE_NO_STORE, 0, "", "", None, False,
                                       flags.get(source), note_for(STATE_NO_STORE)).as_dict()
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
            "sources": [SourceLiveness(source, STATE_NO_STORE, 0, "", "", None, False,
                                       flags.get(source), note_for(STATE_NO_STORE)).as_dict()
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
            age = _age_days(created, reference)          # 判活只认 created_at，见模块头的 10-15 反例
            state = STATE_STALE if age is None or age > stale_after_days else STATE_LIVE
        touched = bool(created and updated and updated > created)
        out.append(SourceLiveness(source, state, count, _iso(created), _iso(updated), age,
                                  touched, flags.get(source), note_for(state)))

    live = [s for s in out if s.state == STATE_LIVE]
    # 只把"读得到开关且确实是关"的算进这一格：`None`（这条不在注册表里，如 `manual`）不算"没点"。
    off = sorted(s.source for s in out if s.state != STATE_LIVE and s.collecting is False)
    if not out:
        verdict = "库里一条信源都没有：这条链没有在采"
    elif not live:
        verdict = "每一条信源都超时没进新条目 —— 没有东西进来（本地改动不算）"
        if off:
            verdict += f"；其中 {len(off)} 条连采集开关都没点：{'、'.join(off)}（就算有执行方也轮不到它）"
    else:
        verdict = f"{len(live)} 条还在进新条目：" + "、".join(s.source for s in live)
        if off:
            verdict += f"；另有 {len(off)} 条开关没点：{'、'.join(off)}"
    return {
        "checked_at": reference.isoformat(timespec="seconds"),
        "stale_after_days": stale_after_days,
        "sources": [s.as_dict() for s in out],
        "verdict": verdict,
    }


def _default_db() -> Optional[Path]:
    try:
        # 裁定 42 / WP-PROFILE-SHARED-LAYER：日程库解析走 store 的共享 root 那一份
        from plobi.agenda.store import agenda_db_path

        return agenda_db_path()
    except Exception:                                 # noqa: BLE001 - 常量读不到就是探针不可用
        return None
