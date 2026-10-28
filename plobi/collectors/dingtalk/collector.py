r"""钉钉那条的采集器：**读本机客户端已经落盘的那份库**，只取被点名的那几扇会话。

落点仍然只有信息池（裁定 193）——这一档只 `pull()`，`sweep.py` 里"只有 `pull()`"那一支负责落池，
我不新起第二条落库路。

为什么默认是"采"而界面上仍要有一块名单（用户 10-24 第二轮：「**把微信和钉钉的会话采集都做成
做到UI里面去。这样太麻烦了**」）：上一刀的形状是"点名为采"，他要先从临时文件里挑 cid 再填进
一个文本框——那一格在 178 个会话前就是他要拆掉的东西。现在他点开抽屉看到名单，默认全在采，
不想采哪扇就关哪扇，与微信那一条口径一致（裁定 50／100 一：**只有 `excluded` 拒绝**）。
形状上却仍比微信更硬一点：微信那份名册有历史（`first_seen_at`、`review_done`），钉钉这份
只有"排除名单"，因为**没定过 = 采**这件事不需要一行去记它。

时间与撤回：`occurred_at` = **这条消息自己的时间**（`createdAt`，毫秒优先、退秒），
`recallStatus` 为真的行不取——撤回之后还留在池里就是"库里有一条平台已经说不存在的话"。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any, Iterable, Optional

from ..base import Occurrence
from . import store

SOURCE_ID = "dingtalk"
#: 聊天流进池的 `kind`。
KIND = "message"
#: 日历那一枚库（`calendar_v2.db`）进池的 `kind`。用户 10-26 拍「进池，kind 我定」——
#: 这个词是**给下一把刀读的契约**（排程那侧今天还不读池，见 `WP-PLAN-READS-POOL`），
#: 所以写在这里而不是散在界面里。
KIND_EVENT = "event"


class DingTalkCollector:
    source_id = SOURCE_ID

    def __init__(self, *, local_store: Any, excluded: Iterable[str] = (),
                 limit: int = 500, since_ms: int = 0, titles: Optional[dict[str, str]] = None,
                 calendar_opener: Any = None, media: Optional[dict[str, list[dict]]] = None,
                 media_index: Any = None, stage_media: Any = None) -> None:
        self.store = local_store
        # 只有 `excluded` 拒绝（裁定 50／100 一：默认采，用户逐扇关）。采集器**只问这一格**——
        # 不去读整张名册再自己判一遍，那是第二处判"采不采"。
        self.excluded = {str(cid).strip() for cid in excluded if str(cid).strip()}
        self.limit = limit
        self.since_ms = since_ms
        self._titles = dict(titles or {})
        self._calendar_opener = calendar_opener or store.open_calendar_store
        #: 日历那一腿这一轮到底怎么样（读到了几条 / 为什么读不到）——由 `health()` 说，不闷着。
        self.calendar_note = ""
        #: 媒体那一格：`None` = 这一轮自己去问索引；传一份字典 = 注入位（测试不碰盘）。
        self._media = media
        self._media_index = media_index or store.media_index
        self._stage_media = stage_media or store.stage_media
        #: 「本机索引里有几条、真取回几条、因为什么没取」——由 `health()` 说出去。
        self.media_note = ""

    @classmethod
    def build(cls, *, excluded: Optional[Iterable[str]] = None,
              opener: Any = None) -> "DingTalkCollector":
        """生产接线：本机库 + 那份准入名册里被排除的那些。

        `opener` / `excluded` 都是注入位（测试不碰盘也不碰日志），默认分别走
        :func:`store.open_local_store` 与 :class:`plobi.collectors.talkers.Ledger`。
        日历那一枚库不在这里开——它是 `pull()` 里独立的一腿，开不开不该决定这一整条源活不活。
        """
        if excluded is None:
            from ..talkers import Ledger

            excluded = Ledger().excluded(SOURCE_ID)
        opened = (opener or store.open_local_store)()
        return cls(local_store=opened, excluded=excluded)

    def health(self) -> dict:
        """库开不开、本机几扇、排除了几扇、日历与媒体那两腿怎么样——四格一起说。

        一扇都没定过**不等于采不到**：默认就是采（与微信同一条口径），所以这里 `ok=True`。
        真要说的那句限制是"本机库里只有客户端已经拉下来的那部分"，而那件事由 `pull()`
        回来的条数与最新时间说，不由这颗绿灯盖掉。
        """
        detail = (f"本机 {self.store.rows} 个会话，其中排除 {len(self.excluded)} 个"
                  f"；日历：{self.calendar_note or '这一轮还没读过'}"
                  f"；图片：{self.media_note or '这一轮还没问过索引'}")
        return {"ok": True, "state": "ready", "detail": detail}


    def pull(self) -> list[Occurrence]:
        """两腿一起读：聊天（被排除的那几扇不读）+ 日历那一枚库。**纯读**，一个字都不写库。"""
        occurrences = self._chat_occurrences()
        occurrences.extend(self._calendar_occurrences())
        return occurrences

    def _chat_occurrences(self) -> list[Occurrence]:
        wanted = [row["cid"] for row in store.list_conversations(self.store, limit=self.limit)
                  if row["cid"] not in self.excluded]
        if not wanted:
            return []
        rows = store.recent_messages(self.store, wanted, limit=self.limit, since_ms=self.since_ms)
        titles = self._conversation_titles()
        media = self._media_for(rows)

        occurrences: list[Occurrence] = []
        for row in rows:
            items = media.get(str(row["mid"]), [])
            occurrences.append(Occurrence(
                source=self.source_id,
                # cid 进键：同一 mid 在转发/引用链里不保证跨会话唯一，而 (会话, mid) 唯一。
                external_id=f"{row['cid']}:{row['mid']}",
                title=_one_line(row["text"]) or f"（{store.content_label(row['content_type'])}）",
                start_at=_local_iso(row["created_at"]),
                kind=KIND,
                evidence={
                    "text": row["text"],
                    "talker": row["cid"],
                    "talker_name": titles.get(row["cid"], ""),
                    "sender_id": row["sender_id"],
                    "created_at": row["created_at"],
                    "content_type": row["content_type"],
                    "content_kind": store.KNOWN_TYPES.get(row["content_type"], "other"),
                    "at_ids": row["at_ids"],
                    # 出处：这一行是从哪一枚本机库、哪一张分片表读来的。排障时不用再猜一次。
                    "origin": f"local-store:{self.store.profile}:{row['shard']}",
                    # 媒体那一格：`[]` 是一个读数（这没有可取的缓存件），不是错误。
                    "media": items,
                },
            ))
        return occurrences

    def _media_for(self, rows: list[dict]) -> dict[str, list[dict]]:
        """聊天里的图片：**先问索引，再把盘上真有的那几件收进 Plobi 的家**。

        索引那一纸只覆盖客户端真的下过并缓存过的件（这台 10-28 现读：3 条，全在盘上）；
        文件类消息本机没有这样一纸索引，`ExtensionFiles` 今天空 ⇒ 没点下载就没有字节。
        所以这一格说的永远是"这台机器上现在有什么"，不是"钉钉云端有什么"——那两句话不许混。
        """
        if self._media is None:
            try:
                self._media = self._media_index(self.store, mids=[str(r["mid"]) for r in rows])
            except Exception as exc:  # noqa: BLE001 - 索引读不到不许把整条源弄红
                self._media = {}
                self.media_note = f"问不到（{type(exc).__name__}）"
                return {}

        if not self._media:
            self.media_note = "本机索引里没有这一轮的件"
            return {}

        out: dict[str, list[dict]] = {}
        saved = skipped = dropped = 0
        for mid, items in self._media.items():
            staged, gone, over = self._stage_media(items)
            out[str(mid)] = staged
            saved += len(staged)
            skipped += gone
            dropped += over

        self.media_note = (f"索引 {len(self._media)} 条，取回 {saved} 件"
                           + (f"，本机已无 {skipped} 件" if skipped else "")
                           + (f"，超单件上限放弃 {dropped} 件" if dropped else ""))
        return out

    def _calendar_occurrences(self) -> list[Occurrence]:
        """`calendar_v2.db` 那几行进池（用户 10-26 拍：「进池，kind 我定」⇒ `KIND_EVENT`）。

        这一腿**读不到不许把整条源弄红**：聊天那 233 条与日历那 6 行不是同一件事，
        拿一次开不了 `calendar_v2.db` 去关掉屏幕上那颗开关就是一句假话。
        但也不许吞成"本机没有日程"——为什么读不到由 `health()` 那张嘴说出去。
        """
        try:
            opened = self._calendar_opener()
        except store.LocalStoreUnavailable as exc:
            self.calendar_note = f"读不到（{exc.code}）"
            return []
        try:
            events = store.calendar_events(opened, limit=self.limit, since_ms=self.since_ms)
        except sqlite3.Error as exc:
            self.calendar_note = f"开不了（{type(exc).__name__}）"
            return []
        finally:
            opened.close()
        self.calendar_note = f"{len(events)} 条（合过 WAL 的那一版）"

        return [
            Occurrence(
                source=self.source_id,
                external_id=f"event:{event['event_id']}",
                title=event["subject"],
                start_at=_local_iso(event["start_time"]),
                end_at=_local_iso(event["end_time"]) or None,
                kind=KIND_EVENT,
                location=event["location"] or None,
                evidence={
                    "calendar_id": event["calendar_id"],
                    "folder_id": event["folder_id"],
                    "unique_id": event["unique_id"],
                    "all_day": event["all_day"],
                    "biz_type": event["biz_type"],
                    "calendar_source": event["source"],
                    "start_time": event["start_time"],
                    "end_time": event["end_time"],
                    "origin": f"local-store:{opened.profile}:{store.CALENDAR_DB}",
                },
            )
            for event in events
        ]

    def _conversation_titles(self) -> dict[str, str]:
        if self._titles:
            return self._titles
        try:
            self._titles = {row["cid"]: row["title"] for row in store.list_conversations(self.store)}
        except Exception:  # noqa: BLE001 - 标题只是让行读得懂，读不到不该把这一轮弄红
            self._titles = {}
        return self._titles


def _one_line(text: str) -> str:
    first = (text or "").strip().splitlines()
    return first[0][:120] if first else ""


def _local_iso(raw: Any) -> str:
    """`createdAt` → 本机朴素 ISO。单位是实测出来的：**毫秒**（>1e11），旧样本里也有按秒的。"""
    try:
        number = int(raw)
    except (TypeError, ValueError):
        return ""
    if not number:
        return ""
    seconds = number / 1000 if number > 100_000_000_000 else number
    return datetime.fromtimestamp(seconds).strftime("%Y-%m-%d %H:%M:%S")
