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

from datetime import datetime
from typing import Any, Iterable, Optional

from ..base import Occurrence
from . import store

SOURCE_ID = "dingtalk"
#: 聊天流进池的 `kind`。日程/审批那一族在**另一枚库**（`calendar_v2.db` / `ding.db`，
#: 同一把钥匙解得开），这一版没接——所以这里不预留一个假的 kind 名。
KIND = "message"


class DingTalkCollector:
    source_id = SOURCE_ID

    def __init__(self, *, local_store: Any, excluded: Iterable[str] = (),
                 limit: int = 500, since_ms: int = 0, titles: Optional[dict[str, str]] = None) -> None:
        self.store = local_store
        # 只有 `excluded` 拒绝（裁定 50／100 一：默认采，用户逐扇关）。采集器**只问这一格**——
        # 不去读整张名册再自己判一遍，那是第二处判"采不采"。
        self.excluded = {str(cid).strip() for cid in excluded if str(cid).strip()}
        self.limit = limit
        self.since_ms = since_ms
        self._titles = dict(titles or {})

    @classmethod
    def build(cls, *, excluded: Optional[Iterable[str]] = None,
              opener: Any = None) -> "DingTalkCollector":
        """生产接线：本机库 + 那份准入名册里被排除的那些。

        `opener` / `excluded` 都是注入位（测试不碰盘也不碰日志），默认分别走
        :func:`store.open_local_store` 与 :class:`plobi.collectors.talkers.Ledger`。
        """
        if excluded is None:
            from ..talkers import Ledger

            excluded = Ledger().excluded(SOURCE_ID)
        opened = (opener or store.open_local_store)()
        return cls(local_store=opened, excluded=excluded)

    def health(self) -> dict:
        """库开不开、本机几扇、排除了几扇——三格一起说。

        一扇都没定过**不等于采不到**：默认就是采（与微信同一条口径），所以这里 `ok=True`。
        真要说的那句限制是"本机库里只有客户端已经拉下来的那部分"，而那件事由 `pull()`
        回来的条数与最新时间说，不由这颗绿灯盖掉。
        """
        return {"ok": True, "state": "ready",
                "detail": f"本机 {self.store.conversations} 个会话，其中排除 {len(self.excluded)} 个"}

    def pull(self) -> list[Occurrence]:
        """读**没被排除的那些**会话。**纯读**：一个字都不写库、一个字节都不动钉钉那侧。"""
        wanted = [row["cid"] for row in store.list_conversations(self.store, limit=self.limit)
                  if row["cid"] not in self.excluded]
        if not wanted:
            return []
        rows = store.recent_messages(self.store, wanted, limit=self.limit, since_ms=self.since_ms)
        titles = self._conversation_titles()

        return [
            Occurrence(
                source=self.source_id,
                # cid 进键：同一 mid 在转发/引用链里不保证跨会话唯一，而 (会话, mid) 唯一。
                external_id=f"{row['cid']}:{row['mid']}",
                title=_one_line(row["text"]) or f"（{store.KNOWN_TYPES.get(row['content_type'], '非文本')}）",
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
                },
            )
            for row in rows
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
