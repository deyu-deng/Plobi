"""ZjuCollector —— 学在浙大的作业清单 → :class:`Occurrence`（判据 5）。

只读源、只交数据：**落点全在公共层** —— 进池走 :func:`plobi.collectors.base.pool_items_of`
+ ``plobi.pool.land()``（裁定 186：采集器只写信息池，「要不要变成日程」的判断权在 Agent 那侧），
完成态走 :func:`plobi.collectors.ingest.authoritative_round`。本包不碰存储层（判据 3：
加一个平台 = 登记一行 + 实现 Collector，公共层一行不动）。

三件形状上的决定，都是被裁定钉的，不是我的偏好：

- **``transport`` 必须显式注入**。默认回退到"真网络调用"正是判据 7 那颗雷的形状（三次跑出
  三种结果，根因是构造里悄悄够到了真外部），所以这里连默认值都不给（裁定 104 二）。
- **采不到东西必须说得出为什么**：每一格失败都抛 :class:`ZjuUnavailable`（带 ``state`` + 那一句），
  **绝不 ``return []`` 冒充"这轮确实没有作业"**（判据 2 那颗"0 行看着正常"的雷）。
- **本轮没出现的作业标失效，不删行**（裁定 190／191，10-20 实测掉的那件）：清单本身完成态的真源，
  ``authoritative_replace`` 已按五轮读数填成 ``True``，**执行住在公共层** :func:`authoritative_round`
  里（只把 ``completed_at`` 写上，行一条不删；下一轮它又出现 ⇒ 撤回来）。这一包**没有**一行
  zju 专用的对账代码——那是裁定 191 判据 5 专门拦的形状。

单飞（同账号同一时刻只许一把在打）交给触发方：cron 的 ``.tick.lock`` 已经是它（``cron/scheduler.py``），
采集器这一层再加一把就是第二真源。这一层管的是**上限**：两次真打之间最短 ``min_minutes_between_pulls``
（带抖动），没到点就连学校都不碰 —— 因为对 zju 的轮询上限**未实测**（治理线只打了 2 成功 + 3 失败，
原话是不许当"可以每 5 分钟一轮"的依据）。
⚠️ 这一格与**用户那侧的采集频率**（:mod:`plobi.collectors.cadence`，裁定 191 二）不是一回事，
两把闸叠着生效：频率说"我想几点采"，这一格说"最多多快碰它一次"。谁都不许被拿掉来"简化"另一个。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Mapping, Optional

from plobi.agenda import get_service
from plobi.agenda.service import AgendaService

from ..base import Occurrence, pool_items_of
from ..ingest import authoritative_round
from . import auth
from .auth import Transport
from .config import ZjuConfig, read_last_pull_at, read_state, write_state
from .status import (
    STATE_DISABLED,
    STATE_NO_CREDENTIAL,
    STATE_READY,
    STATE_THROTTLED,
    ZjuUnavailable,
    note_for,
)

SOURCE_ID = "zju"
#: 实测 48 条全 ``type == "homework"``；别的取值没测过，出现了就计入"这轮没认出的类型"，
# 不静默入库也不静默丢掉。
HOMEWORK_TYPE = "homework"


def to_local_naive(value: str) -> str:
    """站方给的 ``2027-01-05T15:59:00Z``（UTC）→ 本地无时区 ISO，入库格式。

    这一转是**硬约束**，不是美化：``Occurrence.start_at`` 要的是本地 naive
    （``plobi/collectors/base.py:26-27``）。不转就带 ``+08:00`` 进 ``normalize_dt``，
    同一节课每次 pull 都会被 ``ingest.py:15-16`` 那个"改了没改"的比较判成"被改动"，
    凭空生成一堆待确认卡。
    """
    raw = (value or "").strip()
    if not raw:
        raise ValueError("空的时间串")
    text = raw[:-1] + "+00:00" if raw[-1] in ("Z", "z") else raw
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        # 没时区标记的那种回话**没实测过**：猜它是 UTC 还是本地，就是拿一个假时刻排进日程。
        raise ValueError(f"时间串没带时区，不猜它是 UTC 还是本地：{raw!r}")
    return moment.astimezone().replace(tzinfo=None).isoformat(timespec="seconds")


@dataclass
class PullReport:
    """一轮 pull 的实话：什么状态、看到多少行、落成几条、丢了几条以及为什么丢。"""

    state: str
    occurrences: list[Occurrence] = field(default_factory=list)
    rows_seen: int = 0
    dropped: list[dict] = field(default_factory=list)
    detail: str = ""

    @property
    def note(self) -> str:
        return note_for(self.state)

    @property
    def ok(self) -> bool:
        return self.state == STATE_READY


def to_occurrences(rows: list[Mapping[str, Any]]) -> tuple[list[Occurrence], list[dict]]:
    """清单 → 事件，外加"这一轮没认出的行"清单（绝不静默）。

    ``evidence["uid"]`` 用的是**站方自己的 ``id``**（实测 48 条各不同、且字段确实在场），
    去重就落在这位上；表里没有 external-id 这一列，所以 id 只有进 evidence 才活得下来。
    """
    items: list[Occurrence] = []
    dropped: list[dict] = []

    for row in rows:
        row_id = row.get("id")
        if not row_id:
            dropped.append({"reason": "missing_id", "row_keys": sorted(row)})
            continue
        if row.get("type") != HOMEWORK_TYPE:
            # 只认作业这一类；别的类型（考试/通知……）没在这把刀里验过形状，不猜。
            dropped.append({"id": str(row_id), "reason": f"type!={HOMEWORK_TYPE}",
                            "type": str(row.get("type"))})
            continue
        if row.get("is_student") is not True:
            # 实测 48/48 都是 true；不是学生视角的那条不该进"我"的日程。
            dropped.append({"id": str(row_id), "reason": "not_student"})
            continue

        title = str(row.get("title") or "").strip()
        end_time = str(row.get("end_time") or "").strip()
        if not title or not end_time:
            dropped.append({"id": str(row_id), "reason": "missing_title_or_end_time"})
            continue
        try:
            start_at = to_local_naive(end_time)
        except ValueError as exc:
            dropped.append({"id": str(row_id), "reason": "bad_end_time", "detail": str(exc)})
            continue

        items.append(
            Occurrence(
                source=SOURCE_ID,
                external_id=str(row_id),
                title=title,
                # ddl 的语义就是"截止那一刻"这个点位，所以 start_at = end_time、end_at=None。
                start_at=start_at,
                end_at=None,
                kind="ddl",
                location=None,
                evidence={
                    "uid": str(row_id),
                    "course": str(row.get("course_name") or ""),
                    "course_id": row.get("course_id"),
                    # 原样留着站方那个 UTC 串：换算可能因为这台时区漂，原值才是可对账的证据。
                    "due": end_time,
                    # 实测 48 条里 20 条 is_locked=true —— 那是**被前置锁住**，不是错误码。
                    # 排程要不要给锁住的作业留时间位是产品决定，这里只把数据带到。
                    "is_locked": bool(row.get("is_locked")),
                    "prerequisite_count": len(row.get("prerequisites") or []),
                },
            )
        )
    return items, dropped


class ZjuCollector:
    """:class:`plobi.collectors.base.Collector` over ``courses.zju.edu.cn/api/todos``."""

    source_id = SOURCE_ID

    def __init__(
        self,
        *,
        transport: Transport,
        config: Optional[ZjuConfig] = None,
        credentials: Optional[tuple[str, str]] = None,
        credential_reader: Callable[[], Optional[tuple[str, str]]] = auth.read_credentials,
        now: Callable[[], float] = time.time,
    ):
        self.transport = transport
        self.config = config if config is not None else ZjuConfig.load()
        self._credentials = credentials
        self._credential_reader = credential_reader
        self._now = now

    # -- 前置 ---------------------------------------------------------------

    def _resolved_credentials(self) -> Optional[tuple[str, str]]:
        if self._credentials is not None:
            return self._credentials
        return self._credential_reader()

    def _record_attempt(self, report: PullReport) -> None:
        """把"这一轮什么时候打的、结果是什么、看到几行"落进状态文件。

        这一份记录是 ``health()`` 不去打网络也能说得出真话的唯一凭据。
        """
        write_state(
            {
                "last_pull_at_epoch": self._now(),
                "last_state": report.state,
                "last_detail": report.detail,
                "rows_seen": report.rows_seen,
                "occurrences": len(report.occurrences),
                "dropped": len(report.dropped),
            }
        )

    # -- Collector 契约 -----------------------------------------------------

    def health(self, *, probe: bool = False) -> dict:
        """``{ok, detail, state, …}`` —— 默认**不**打学校，读上一轮的记录。

        ``probe=True`` 才真去走一遍认证链。为什么默认不探：对 zju 的轮询上限没实测过，
        而面上每一次刷新都打一遍 CAS 是可观测的负载（判据 7 那句"不静默"反过来也成立 ——
        我们不替站方制造他没承诺的流量）。拿不到记录就明说"没采过"，不许回一句"正常"。
        """
        if not self.config.enabled:
            return {"ok": False, "state": STATE_DISABLED, "detail": note_for(STATE_DISABLED)}
        if self._resolved_credentials() is None:
            return {"ok": False, "state": STATE_NO_CREDENTIAL, "detail": note_for(STATE_NO_CREDENTIAL)}

        if probe:
            report = self.pull_report(ignore_throttle=True)
            return {
                "ok": report.ok,
                "state": report.state,
                "detail": report.note if not report.ok else f"进去了，看到 {report.rows_seen} 行",
                "rows_seen": report.rows_seen,
            }

        stored = read_state()
        if not stored:
            return {
                "ok": False,
                "state": STATE_THROTTLED,
                "detail": "这一路还没真打过一次（没有上一轮的记录，也不拿猜测当读数）。",
                "last_pull_at_epoch": None,
            }
        return {
            "ok": stored.get("last_state") == STATE_READY,
            "state": str(stored.get("last_state") or ""),
            "detail": str(stored.get("last_detail") or ""),
            "note": note_for(str(stored.get("last_state") or "")),
            "last_pull_at_epoch": read_last_pull_at(),
            "rows_seen": stored.get("rows_seen"),
        }

    def pull(self, *, since: Any = None) -> list[Occurrence]:
        """:class:`Collector` 契约那一格。采不到就抛，**不返空清单冒充正常**。

        ``since`` 收了不用：这台**没实测到增量端点**（``limit=5`` 与 ``limit=200`` 都回 48 条
        只说明它不按 ``limit`` 截断，不说明它认 ``since``）⇒ 每轮全量 + 本地按 ``uid`` 幂等，
        由 ``ingest`` 去做"有没有变"。
        """
        report = self.pull_report()
        if not report.ok:
            raise ZjuUnavailable(report.state, report.detail)
        return report.occurrences

    def pull_report(self, *, ignore_throttle: bool = False) -> PullReport:
        if not self.config.enabled:
            return PullReport(state=STATE_DISABLED, detail=note_for(STATE_DISABLED))

        credentials = self._resolved_credentials()
        if credentials is None:
            return PullReport(state=STATE_NO_CREDENTIAL, detail=note_for(STATE_NO_CREDENTIAL))

        last_pull_at = read_last_pull_at()
        if not ignore_throttle and not self.config.pull_allowed_now(last_pull_at, self._now()):
            return PullReport(
                state=STATE_THROTTLED,
                detail=f"最短间隔 {self.config.min_minutes_between_pulls} 分钟（带抖动）还没到",
            )

        username, password = credentials
        # 先只打一发业务端点：会话还活着就一轮 GET 完事（礼貌面）；掉了才去走 CAS 那五步。
        state, detail, rows = auth.fetch_todos(self.transport)
        if state != STATE_READY:
            state, detail, rows = auth.authenticate(self.transport, username, password)

        report = PullReport(state=state, detail=detail, rows_seen=len(rows))
        if state == STATE_READY:
            report.occurrences, report.dropped = to_occurrences(rows)
        self._record_attempt(report)
        return report

    def collect(self, *, service: Optional[AgendaService] = None) -> dict:
        """pull + land：**落点是信息池**（裁定 186），完成态由公共层按声明执行（裁定 190／191）。

        ``service`` 现在只被**完成态那一半**用到——:func:`authoritative_round` 要把"本轮没出现的
        这一类"标失效，那一格写在事件上。原来的 ``apply``（pending / confirmed）**跟着日程落点
        一起删了**：池子不分那两档，留着那个参数就是留着「顺手落日程」那条路（``sweep.py`` 里
        同一格 10-19 已删，这边补上另一半，别留一个"看着还在"的档位）。
        """
        from plobi.pool import land as pool_land

        report = self.pull_report()
        if not report.ok:
            raise ZjuUnavailable(report.state, report.detail)

        landed = pool_land(pool_items_of(report.occurrences, source=SOURCE_ID))
        closed = authoritative_round(service or get_service(), report.occurrences)
        return {
            **landed,
            "state": report.state,
            "source": SOURCE_ID,
            "rows_seen": report.rows_seen,
            "dropped": report.dropped,
            #: 逐条 id 一起给：判据 2「已提交的别再被排」与判据 3「不删行」都要能指着名字核，
            #: 而不是只看一个数字。这一格也是"消失到底等不等于已提交"那件仍未测的事的唯一现场。
            "closed_ids": closed["closed"],
            "reopened_ids": closed["reopened"],
            "closed": closed["closed_count"],
            "reopened": closed["reopened_count"],
            "authoritative_skipped": closed["skipped"],
        }
