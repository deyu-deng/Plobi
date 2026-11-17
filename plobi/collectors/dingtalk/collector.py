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

11-17 补的四格（用户「采集的信息好像还没有榨干」那一条）：

1. **会话那两列**（`conversation`／`visibility`）——微信那一族在**自己的**落点填，
   这一族走公共落点 `sweep.py` ⇒ 同一个池两套写法，池里那 1500 条钉钉消息两列全空，
   按会话筛不动、分不了群。现在由采集器在 `Occurrence` 上带下来（`store.list_conversations`
   的 `type` 那一格本来就是现成的：1＝私聊／2＝群）。
2. **谁说的**——`tbuser_profile_v2`（2146 行 nick）与 `tbuser_group_nick`（群里的备注名）
   这一族以前**零引用** ⇒ 发件人只剩一个数字 uid。现在：**群备注 ＞ 全局昵称 ＞ 空**。
3. **是不是我本人发的**——本机 uid（`store.uid` ＝ 解开钥匙的那个 uid）对照，**三态**
   （True／False／None＝"没验出来"）。
4. **真字节**——消息体里**原样**带着的 URL：免鉴权的那族真取回来，要凭据的那族
   **如实说缺凭据**（盘上没有凭据），**不许拿缩略图冒充原件**。
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from typing import Any, Iterable, Optional

from ..base import Occurrence
from . import store

logger = logging.getLogger(__name__)

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
                 media_index: Any = None, stage_media: Any = None,
                 media_fetcher: Any = None) -> None:
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
        #: 真字节那一格（消息体里**原样**带着的 URL）：注入位，测试不触网。
        self._media_fetcher = media_fetcher or store.fetch_media
        #: 「远端那一格取回几件、哪一族缺凭据」——由 `health()` 说出去。
        self.remote_note = ""
        #: 会话名单（cid → 标题 ＋ 群/私聊）：一轮只读一次。
        self._conversations_cache: Optional[dict[str, dict]] = None
        #: 昵称表（uid → 名字）：一轮只读一次。
        self._names_cache: Optional[dict[str, str]] = None
        #: 群里的备注名（(cid, uid) → 群备注）：一轮只读一次。
        self._group_nicks_cache: Optional[dict[tuple[str, str], str]] = None

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
                  f"；图片：{self.media_note or '这一轮还没问过索引'}"
                  f"；远端媒体：{self.remote_note or '这一轮还没问过'}")
        return {"ok": True, "state": "ready", "detail": detail}


    def pull(self) -> list[Occurrence]:
        """两腿一起读：聊天（被排除的那几扇不读）+ 日历那一枚库。**纯读**，一个字都不写库。"""
        occurrences = self._chat_occurrences()
        occurrences.extend(self._calendar_occurrences())
        return occurrences

    def _chat_occurrences(self) -> list[Occurrence]:
        conversations = self._conversations()
        wanted = [cid for cid in conversations if cid not in self.excluded]
        if not wanted:
            return []
        rows = store.recent_messages(self.store, wanted, limit=self.limit, since_ms=self.since_ms)
        titles = self._conversation_titles()
        names = self._sender_names()
        group_nicks = self._group_nicks()
        media = self._media_for(rows)
        remote_saved, remote_pending = self._remote_media(rows, media)

        occurrences: list[Occurrence] = []
        for row in rows:
            mid = str(row["mid"])
            cid = str(row["cid"] or "")
            sender_id = str(row["sender_id"] or "")
            items = list(media.get(mid, [])) + list(remote_saved.get(mid, []))
            pending = list(remote_pending.get(mid, []))
            occurrences.append(Occurrence(
                source=self.source_id,
                # cid 进键：同一 mid 在转发/引用链里不保证跨会话唯一，而 (会话, mid) 唯一。
                external_id=f"{cid}:{mid}",
                title=_one_line(row["text"]) or f"（{store.content_label(row['content_type'])}）",
                start_at=_local_iso(row["created_at"]),
                kind=KIND,
                # 会话那两列（裁定 187①）：`conversation`＝哪一扇窗口，`visibility`＝群还是私聊。
                # 微信那一族在**自己的**落点填，钉钉这一族由采集器在 Occurrence 上带下来——
                # 同一个池曾经两套写法，钉钉那 1500 行两列全空（11-17 真机现核）。
                conversation=cid,
                visibility=str(conversations.get(cid, {}).get("type") or ""),
                evidence={
                    "text": row["text"],
                    "talker": cid,
                    "talker_name": self._conversation_label(cid, titles.get(cid, ""), names),
                    # 「谁说的」：**人名**（群里的备注名优先）。空串＝没查到名字，
                    # 不是"不知道是谁"（那是 `is_self` 那一格的活）。
                    "sender_name": self._sender_name(cid, sender_id, group_nicks, names),
                    # 发件人的 uid：身份证据，与名字**分开两格**（机器号归它，不许冒充人名）。
                    "sender_id": sender_id,
                    # 是不是本人发出：三态（True／False／None＝"没验出来"）。
                    "is_self": self._is_self(sender_id),
                    "created_at": row["created_at"],
                    "content_type": row["content_type"],
                    "content_kind": store.KNOWN_TYPES.get(row["content_type"], "other"),
                    "at_ids": row["at_ids"],
                    # 出处：这一行是从哪一枚本机库、哪一张分片表读来的。排障时不用再猜一次。
                    "origin": f"local-store:{self.store.profile}:{row['shard']}",
                    # 媒体那一格：`[]` 是一个读数（这没有可取的缓存件），不是错误。
                    "media": items,
                    # 取不到的那些**说得出为什么**（缺凭据／没取到），不许并成"这条没媒体"。
                    **({"media_pending": pending} if pending else {}),
                },
            ))
        return occurrences

    def _conversations(self) -> dict[str, dict]:
        """cid → `{"title": …, "type": "group"｜"private"}`（会话名单那一层，一轮只读一次）。

        `type` 那一格**本来就是现成的**（`store.list_conversations` 一直返回它：
        这台现读 1＝私聊 89／2＝群 95），只是采集侧从来没问过——于是池里那 1500 条
        钉钉消息的 `conversation`／`visibility` 两列全空，按会话筛不动、分不了群。
        """
        if self._conversations_cache is not None:
            return self._conversations_cache
        try:
            rows = store.list_conversations(self.store, limit=self.limit)
        except Exception as exc:  # noqa: BLE001 - 名单读不到不该把这一轮弄红
            logger.debug("dingtalk: conversation list unreadable: %s", type(exc).__name__)
            rows = []
        self._conversations_cache = {
            str(row.get("cid") or ""): {"title": str(row.get("title") or ""),
                                       "type": str(row.get("type") or "")}
            for row in rows if str(row.get("cid") or "")
        }
        return self._conversations_cache

    def _sender_names(self) -> dict[str, str]:
        """uid → 他看得懂的名字（`tbuser_profile_v2`）。

        这一族以前**零引用**（全仓没有一处读它）⇒ 池里 1500 条钉钉消息的发件人只剩
        一个数字 uid。这台现核：2146 行 nick，**1525/1565 条消息的 senderId 命中**（97%）。
        读不到 ⇒ 空表（"这一侧没有名字可查"，不是故障）。
        """
        if self._names_cache is not None:
            return self._names_cache
        try:
            self._names_cache = store.sender_names(self.store)
        except Exception as exc:  # noqa: BLE001 - 查名字这条路失败不许把这一轮弄红
            logger.debug("dingtalk: sender-name lookup failed: %s", type(exc).__name__)
            self._names_cache = {}
        return self._names_cache

    def _group_nicks(self) -> dict[tuple[str, str], str]:
        """(cid, uid) → 群里的备注名（`tbuser_group_nick`）。

        群内叫法比全局昵称更贴近"他怎么称呼对方" ⇒ **群消息优先用这一格**。
        这台现核 7 行（覆盖极低），读不到就当没有。
        """
        if self._group_nicks_cache is not None:
            return self._group_nicks_cache
        try:
            self._group_nicks_cache = store.group_nick_names(self.store)
        except Exception as exc:  # noqa: BLE001 - 同上：查不到就当没有
            logger.debug("dingtalk: group-nick lookup failed: %s", type(exc).__name__)
            self._group_nicks_cache = {}
        return self._group_nicks_cache

    def _is_self(self, sender_id: str) -> Optional[bool]:
        """是不是本机账号发的——**三态**（True／False／None＝"没验出来"）。

        判据是**本机 uid 对照**：`store.uid` ＝ 解开这把钥匙的那个 uid（服务端派下来的
        密钥按账号下发 ⇒ 它只可能是本机账号）。这台现核：本机 uid `5786844176`
        （nick 邓德彧）在库里发了 84 条。

        没有 uid 或没有 sender_id ⇒ **None**：不知道就说不知道，不猜。
        """
        mine = str(getattr(self.store, "uid", "") or "")
        if not mine or not sender_id:
            return None
        return sender_id == mine

    def _sender_name(self, cid: str, sender_id: str,
                     group_nicks: dict[tuple[str, str], str],
                     names: dict[str, str]) -> str:
        """发件人的名字：**群里的备注名优先**，再退全局昵称，再退「我」，最后空着。

        空着是"没查到"；拿 uid 顶上就是"把机器号冒充成人名"——11-17 现核：池里那 1500 条
        钉钉消息的 `sender_name` **0 行**，发件人只剩一个数字。
        """
        if not sender_id:
            return ""
        label = str(group_nicks.get((cid, sender_id), "") or "")
        if not label:
            label = str(names.get(sender_id, "") or "")
        if not label and self._is_self(sender_id) is True:
            return "我"
        return label

    def _conversation_label(self, cid: str, title: str, names: dict[str, str]) -> str:
        """这一扇窗口的名字：群＝群名；私聊＝**对方的名字**。

        私聊那一格库里存的是**对方 uid**（这台现读：`title` 就是 `659392009` 那种号），
        而私聊 cid 的形状是 `对方:我` ⇒ 从 cid 取出另一头，再用昵称表换成人名。
        换不到就留着那个号——那是"这是号不是名"，不是编一个名字。
        """
        mine = str(getattr(self.store, "uid", "") or "")
        if mine and ":" in cid:
            head, _, tail = cid.partition(":")
            other = tail if head == mine else (head if tail == mine else "")
            if other:
                return str(names.get(other, "") or other)
        return title

    def _remote_media(self, rows: list[dict],
                      local: Optional[dict[str, list[dict]]] = None
                      ) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
        """消息体里**原样**带着的那些媒体 URL：免鉴权的那族真取回来，要凭据的那族如实记下。

        返回 `(取回清单, 取不到清单)`，两把都按 `mid` 分着。

        **本机缓存优先**：这一条消息本机已经有件，就不再为它触网——盘上有的东西
        不必再问一遍网络（`WP-DINGTALK-MEDIA` 那一格量的就是"本机有没有"）。

        **只取消息体里现成的 URL**，绝不自己拼（11-17 实测：拼的六种变体全 404，
        客户端算好的那些原样 GET 才是 200）。要凭据的那族**不去试**：如实说缺凭据，
        **不许拿缩略图冒充原件**。
        """
        saved: dict[str, list[dict]] = {}
        pending: dict[str, list[dict]] = {}
        budget = store.MAX_MEDIA_BYTES
        used = fetched = blocked = missing = 0
        for row in rows:
            mid = str(row.get("mid") or "")
            if local and local.get(mid):
                continue  # 本机已有件 ⇒ 不触网
            for item in row.get("media_urls") or []:
                if used >= store.MAX_MEDIA_PER_ROUND or budget <= 0:
                    break
                used += 1
                result = self._media_fetcher(item.get("url", ""))
                if result.get("saved_to"):
                    saved.setdefault(mid, []).append({**item, **result})
                    budget -= int(result.get("size_bytes") or 0)
                    fetched += 1
                    continue
                reason = {key: value for key, value in result.items() if key != "url"}
                pending.setdefault(mid, []).append({**item, **reason})
                if reason.get("status") == "needs_credential":
                    blocked += 1
                else:
                    missing += 1
        self.remote_note = (f"取回 {fetched} 件"
                            + (f"，缺凭据 {blocked} 件" if blocked else "")
                            + (f"，没取到 {missing} 件" if missing else ""))
        return saved, pending

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
        self._titles = {cid: info["title"] for cid, info in self._conversations().items()}
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
