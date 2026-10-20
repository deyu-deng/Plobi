"""信息池（`specs/source-pool.md`）。

**采集器唯一允许写的地方。** 采集器往这儿写，Agent 读；**没有任何自动路径从这儿往
`agenda.db` 搬东西** —— 池→日程必须经过 Agent 的判断和它手里的日程工具。

这个包**刻意不认识 `agenda.db`**：不认识，就没法顺手落日程，那条路是断的而不是靠约定。
"""

from .store import (
    CANONICAL_FORMAT,
    DEFAULT_LIMIT,
    MAX_LIMIT,
    PoolError,
    PoolItem,
    connect,
    land,
    mark_acted,
    normalize_time,
    path,
    query,
    stats,
)

__all__ = [
    "CANONICAL_FORMAT",
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "PoolError",
    "PoolItem",
    "connect",
    "land",
    "mark_acted",
    "normalize_time",
    "path",
    "query",
    "stats",
]