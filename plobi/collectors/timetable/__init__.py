"""Timetable (.ics) collector — the second Collector implementation.

Public surface: :func:`parse_ics` / :func:`parse_ics_with_stats`,
:class:`TimetableCollector`, :func:`import_ics`, the board API router
(:mod:`.api`, mounted under ``/api/collect``), and :data:`SPEC` — 这一条信源
**自己**向注册表报自己的形状（`specs/source-layer.md` §3.1）。加一条信源＝加一个
这样的包，`plobi/collectors/registry.py` 里不再出现任何信源 id。
"""

from plobi.collectors.registry import SourceSpec, register

from .collector import TimetableCollector, import_ics
from .parser import IcsParseError, parse_ics, parse_ics_with_stats


def _build() -> object:
    """执行方入口。**延迟 import**：``collector`` 那头要读注册表派生的词表，
    而注册表的发现正是在导入本包——顶层就 import 它会成一个环。"""
    from .collector import TimetableCollector as _TimetableCollector

    return _TimetableCollector()


SPEC = SourceSpec(
    "timetable",
    capabilities={
        "history_backfill": True,       # 文件里有什么就是什么
        "incremental_since": False,     # collector 收了 since 但忽略
        "supports_media": False,
        "requires_manual_setup": True,  # 用户指定目录里放一个 .ics
        "authoritative_replace": False,
    },
    # 「用户往某目录放一个 .ics」⇒ 连接器形状是磁盘路径，归「本地」不归「网站」
    # （这一档同时还有一个 `timetable.ics_url`，那是同一份数据的另一种接法，
    #   登记成一条还是两条属 spec-layer §10 未决，不在下面混着记）。
    category="local",
    builder=_build,
)

register(SPEC, replace=True)

__all__ = [
    "SPEC",
    "IcsParseError",
    "TimetableCollector",
    "import_ics",
    "parse_ics",
    "parse_ics_with_stats",
]
