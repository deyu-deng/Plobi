"""Timetable (.ics) collector — the second Collector implementation.

Public surface: :func:`parse_ics` / :func:`parse_ics_with_stats`,
:class:`TimetableCollector`, :func:`import_ics`, :func:`land_occurrences`,
:class:`TimetableUnavailable`, the board API router (:mod:`.api`, mounted under
``/api/collect``), and :data:`SPEC` — 这一条信源**自己**向注册表报自己的形状
（`specs/source-layer.md` §3.1）。加一条信源＝加一个这样的包，
`plobi/collectors/registry.py` 里不再出现任何信源 id。

落点是**信息池**（裁定 193），不再直落日程库；读的是本地 .ics，接口实测之前不假装会拉。
"""

from plobi.collectors.registry import SetupField, SourceSpec, register

from .collector import (
    STATE_FILE_MISSING,
    STATE_NO_PATH,
    STATE_PARSE_FAILED,
    STATE_UNREADABLE,
    TimetableCollector,
    TimetableUnavailable,
    import_ics,
    land_occurrences,
    note_for,
)
from .parser import IcsParseError, parse_ics, parse_ics_with_stats


def _build() -> object:
    """执行方入口：把**用户那一层填的那一格路径**交给采集器。

    这一格 10-21 之前是坏的：``_TimetableCollector()`` 少一个必填参数 ⇒ 每天那一轮把这一条
    报成 `build_failed: TypeError: missing 1 required positional argument: 'path'`
    （现读 10-20 那几轮 cron 正文都写着这一句）。**修法是去读用户填的那格**，
    而不是给路径加一个默认值——默认值就是"猜一个路径然后 0 行进账"那颗雷。

    那一格没填 ⇒ **抛有名状态**（``no_path_configured``），不返回一个空采集器：
    ``sweep`` 会把这句话原样收进那一轮的读数，于是"课表为什么没进来"是一行读数，
    不是设置里摆着好看的一格。延迟 import 照旧（顶层 import 会成环）。
    """
    from plobi.collectors import sources_config

    from .collector import STATE_NO_PATH, TimetableCollector as _TimetableCollector
    from .collector import TimetableUnavailable

    path = str(sources_config.setup_params("timetable").get("ics_path") or "").strip()
    if not path:
        raise TimetableUnavailable(STATE_NO_PATH)
    return _TimetableCollector(path)


SPEC = SourceSpec(
    "timetable",
    capabilities={
        # 实测（10-09 那份 240 条的导入）：文件里有什么就是什么，RRULE 一次展开整个学期。
        "history_backfill": True,
        "incremental_since": False,     # 收了 `since` 但忽略：一份文件要么整份读，没有游标
        "supports_media": False,        # VALARM/说明行都不进条目，媒体零处理
        "requires_manual_setup": True,  # 用户指定目录里放一个 .ics（或把路径填进那一格）
        # **没实测**：这一轮文件里少一条，分不清是停课、改了排次，还是导出少导了一节 ⇒
        # 不许拿缺席当取消（裁定 191 的口径：没测到就 False 并留一句为什么，别猜）。
        # 接口探查之后这一格才有依据重填——那时要连 `test_collect_timetable_status.py`
        # 那颗"出现 HTTP 客户端即红"的扫描钉一起改口径。
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
    ),
    builder=_build,
    # **这一条不再进每天那一轮**（裁定 194 第 2 条：用户把一份 .ics 交给日程，那是用户的手，
    # 不是采集器）。10-22 现读的坏形状：`ics_path` 没填 ⇒ builder 抛 `no_path_configured` ⇒
    # `collect_runs.json` 里每轮一条 `timetable: build_failed`，设置页上是一条永远红的信源行，
    # 而那一格本来就不该是信源。撤的是**采集**这一档，端点与文件读取照旧能用（日程那一侧的
    # 「从外部导入」还靠它），包整体搬出 `plobi/collectors/` 与界面上那格的去处由
    # `WP-AGENDA-ICS-IMPORT` 做（任务书 `tasks/agenda-ics-import-task.md`）——本刀不负责重写它。
    collecting=False,
)

register(SPEC, replace=True)

__all__ = [
    "SPEC",
    "STATE_FILE_MISSING",
    "STATE_NO_PATH",
    "STATE_PARSE_FAILED",
    "STATE_UNREADABLE",
    "IcsParseError",
    "TimetableCollector",
    "TimetableUnavailable",
    "import_ics",
    "land_occurrences",
    "note_for",
    "parse_ics",
    "parse_ics_with_stats",
]
