"""「学在浙大」collector —— 未提交作业 → 日程（判据 5，WP-SOCIAL-SOURCES-PLUGGABLE）。

公开面：:class:`ZjuCollector` / :func:`to_occurrences` / :func:`to_local_naive` /
:class:`PullReport`，加上状态那一族（一格一句，见 :mod:`.status`）。

这一档**没有 HTTP 接线**，是刻意的：注册表那条判据写的是「加一个平台 = 登记一行 +
实现 Collector，不改 ``collect_api.py``/``web_server.py`` 的手写接线」，所以面上什么时候
能点到它，归触发那一刀（``WP-SCHEDULE-REAL-PLAN``）。
"""

from .collector import PullReport, ZjuCollector, to_local_naive, to_occurrences
from .config import ZjuConfig
from .status import (
    SOURCE_STATES,
    ZjuUnavailable,
    note_for,
)

__all__ = [
    "SOURCE_STATES",
    "PullReport",
    "ZjuCollector",
    "ZjuConfig",
    "note_for",
    "to_local_naive",
    "to_occurrences",
]
