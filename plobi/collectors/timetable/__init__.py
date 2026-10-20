"""Timetable (.ics) collector — the second Collector implementation.

Public surface: :func:`parse_ics` / :func:`parse_ics_with_stats`,
:class:`TimetableCollector`, :func:`import_ics`, the board API router
(:mod:`.api`, mounted under ``/api/collect``), and :data:`SPEC` — 这一条信源
**自己**向注册表报自己的形状（`specs/source-layer.md` §3.1）。加一条信源＝加一个
这样的包，`plobi/collectors/registry.py` 里不再出现任何信源 id。
"""

from plobi.collectors.registry import CADENCE_FIELD, SetupField, SourceSpec, register

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
    # 首次配置只有一格，就是注册表注记写的那句"用户指定目录里放一个 .ics"。
    # 这一格由本包**自己**声明——加一条新信源不用改 `apps/desktop/**`
    # （spec §8 有一条验收就是这个）。
    setup_fields=(
        SetupField(
            key="ics_path",
            label="Path to the .ics file",
            kind="path",
            required=True,
            help="Absolute path on this machine.",
        ),
        # 每源一行频率（裁定 191 二）。真源在注册表，这里只是引它 —— 不在四个包里各写一句 label。
        CADENCE_FIELD,
    ),
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
