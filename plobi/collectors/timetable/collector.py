"""TimetableCollector —— 课表这一条的**读**与**落池**（WP-TIMETABLE-COLLECT，裁定 193）。

两件事这一笔真的改了：

1. **落点是信息池**，不再直落日程库。``import_ics`` 以前走的是共享那条日程落库接缝
   （``apply="confirmed"``，把课表行直接坐进 ``events``）——那是最后一条还在直落日程的信源。
   裁定 193 的原话是
   「**全部都要直接进池。不能直接落别的库**」，所以这一格没有过渡期：走
   :func:`plobi.collectors.base.pool_items_of` + ``plobi.pool.land()``，与微信、学在浙大同一条路。
2. **`apply` 那一档跟着日程落点一起删了**（池子不分待确认／坐实）。连带一个**必须照实说的后果**：
   旧形状里「课表改时间 ⇒ 长一张待确认卡并快照 ``prev_value`` 好回滚」这一条**在这一条信源上没有了**——
   池里那一行被 UPDATE，日程那侧要不要跟着改、怎么改，是读池的那个 Agent 的判断（裁定 186）。
   这不是顺手简化，是新边界必然的形状，别在 review 时把它当漏掉的功能补回来。

**仍然没有 HTTP 采集器**，而且这句要一直留到接口实测掉为止（P6：不许描述不存在的机制）：
本目录里没有任何 HTTP 客户端，``pull()`` 读的是用户放进来的本地 ``.ics``。
Celechron 那条路（本科教务 ``zdbk``/``jwglxt``，`Docs/reference/celechron-survey.md` §1）
给的只有"哪个文件哪一行"，**端点参数、分页、状态位三条只能拿真账号打一次才知道**（§4.5）——
那一步要用用户的凭据，**要他当场同意**才跑。所以：

- 探测之前，``pull()`` 的唯一真源还是那份文件；探测之后加的是**另一条 source**，
  不是把这条悄悄换成 HTTP（``test_collect_timetable_status.py`` 那颗「出现 HTTP 客户端即红」的
  扫描钉就是为这件事留的：它红了必须连口径一起改，不许拔钉子）。
- 一次拉回 0 行**不许当成「今天没课」**：``health()`` 说得出文件在不在，``pull()`` 打不开就抛
  :class:`TimetableUnavailable`（带 ``state`` + 那一句），与 zju 那一族同一形状——
  「0 行看着正常」是采集面最贵的那类故障。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from ..base import Occurrence, pool_items_of
from .parser import IcsParseError, parse_ics

SOURCE_ID = "timetable"

STATE_NO_PATH = "no_path_configured"
STATE_FILE_MISSING = "file_missing"
STATE_UNREADABLE = "file_unreadable"
STATE_PARSE_FAILED = "ics_parse_failed"

_NOTES = {
    STATE_NO_PATH: "首次配置里 `ics_path` 那一格没填：这一条现在唯一的真源就是那个文件，没有它就没有课表。",
    STATE_FILE_MISSING: "配置指向的那个 .ics 不在盘上——不是「没课」，是读不到。",
    STATE_UNREADABLE: ".ics 读不动（权限或被占用）：这一轮什么都没拿到。",
    STATE_PARSE_FAILED: ".ics 打开了但解析不出事件：文件坏了或不是日历，别当成空课表。",
}


def note_for(state: str) -> str:
    if state not in _NOTES:
        return f"没记到的课表状态 {state!r}：不去猜它属于哪一种。"
    return _NOTES[state]


class TimetableUnavailable(RuntimeError):
    """读不到课表 —— **带状态那一句**，绝不返回空清单冒充「这一轮确实没课」。"""

    def __init__(self, state: str, detail: str = "") -> None:
        super().__init__(detail or note_for(state))
        self.state = state
        self.detail = detail or note_for(state)


class TimetableCollector:
    """:class:`plobi.collectors.base.Collector` over a local ``.ics`` file."""

    source_id = SOURCE_ID

    def __init__(self, path: str | Path):
        self.path = str(path)

    def health(self) -> dict:
        """``{ok, detail, state}`` —— 文件在不在、读得动读不动，三种话不同。"""
        file_path = Path(self.path)
        if not file_path.is_absolute():
            return {"ok": False, "state": STATE_NO_PATH, "detail": f"path is not absolute: {self.path}"}
        if not file_path.is_file():
            return {"ok": False, "state": STATE_FILE_MISSING, "detail": f"file not found: {self.path}"}
        try:
            with file_path.open("rb") as handle:
                handle.read(1)
        except OSError as exc:
            return {"ok": False, "state": STATE_UNREADABLE, "detail": f"file not readable: {exc}"}
        return {"ok": True, "state": "ready", "detail": self.path}

    def pull(self, *, since: Any = None) -> list[Occurrence]:
        """课表每一节 Concrete 一条 ``Occurrence``。纯读：不碰日程，也不碰池。

        ``since`` 收了不用（能力位 ``incremental_since`` 因此记 ``False``）：一份文件要么整份读，
        没有增量游标可用——**这句话说的是我们这侧的契约**，不是"学校没有增量接口"。
        """
        file_path = Path(self.path)
        if not file_path.is_absolute():
            raise TimetableUnavailable(STATE_NO_PATH, f"路径不是绝对路径：{self.path}")
        if not file_path.is_file():
            raise TimetableUnavailable(STATE_FILE_MISSING, f"配置指向的文件不在盘上：{self.path}")
        try:
            return parse_ics(self.path)
        except IcsParseError as exc:
            raise TimetableUnavailable(STATE_PARSE_FAILED, str(exc)) from exc
        except OSError as exc:
            raise TimetableUnavailable(STATE_UNREADABLE, f"{type(exc).__name__}: {exc}") from exc


def land_occurrences(occurrences: list[Occurrence], *, db_path: Optional[Path] = None) -> dict:
    """把课表条目落进**信息池**——这一条信源唯一的写出口。

    时间那一格交给池的写入口归一（裁定 189：归一只在写入口做一次），这一层不自拼格式；
    ``external_id`` 用的是 ``parser`` 那条 ``uid[:开始时刻]``（导出文件给的稳定 id），
    不是标题拼的，也不是自增。``kind`` 保持 ``class``，节次／教师／地点在 ``context`` 里，
    **不为课表另开专用列**（判据：只有全信源共用的形状才配当列）。

    ``db_path`` 是给测试钉落点用的（读侧函数其实会写库那一族的坑：不传就会写真的共享池）。
    """
    from plobi.pool import land as pool_land

    result = pool_land(pool_items_of(occurrences, source=SOURCE_ID), db_path=db_path)
    return {**result, "source": SOURCE_ID, "landing": "pool", "round": len(occurrences)}


def import_ics(path: str | Path, *, db_path: Optional[Path] = None) -> dict:
    """解析 + 落**池**。返回 ``{created, updated, unchanged, source, landing, round}``。

    ``apply`` 参数**没了**（见模块头第 2 条）。这一轮里少掉的那些行**不删任何东西**：池按
    ``(source, external_id)`` 幂等，旧条目留在池里；要不要按缺席标失效由能力位
    ``authoritative_replace`` 说了算，而那位现在声明的是 ``False``——因为「文件里少一条」
    分不清是**停课**还是**导出少导了一节**，没实测就不许拿缺席当取消（裁定 191 的口径）。
    """
    return land_occurrences(parse_ics(path), db_path=db_path)
