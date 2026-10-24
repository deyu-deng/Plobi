r"""钉钉那条的采集器：**读本机客户端已经落盘的那份库**，只取被点名的那几扇会话。

落点仍然只有信息池（裁定 193）——这一档只 `pull()`，`sweep.py` 里"只有 `pull()`"那一支负责落池，
我不新起第二条落库路。

为什么默认一条都不采：这一枚库里装的是**整个钉钉的全部会话**（公司群、同事私聊、审批卡片），
而 Plobi 要的只是他点出来的那几扇。这一条与微信那一路同一口径（隐私默认关），
形状上却更硬：微信那份名册有面上那份"每个群/每个人都采不采"的清单可以点，
钉钉这一版**先只有配置格里那一串 cid**——清单上屏是下一刀（所以 `conversation_scoped`
这一格这一版留 `False`：那块面板今天只认微信那一份 store，把钉钉指过去会画出一屏错的数据）。

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

    def __init__(self, *, local_store: Any, cids: Iterable[str] = (),
                 limit: int = 500, since_ms: int = 0, titles: Optional[dict[str, str]] = None) -> None:
        self.store = local_store
        self.cids = [str(cid).strip() for cid in cids if str(cid).strip()]
        self.limit = limit
        self.since_ms = since_ms
        self._titles = dict(titles or {})

    @classmethod
    def build(cls, *, config: Optional[dict] = None, opener: Any = None) -> "DingTalkCollector":
        """生产接线：配置层那一格 cid 名单 + 本机库。

        `opener` 是注入位（测试不碰盘也不碰日志），默认 :func:`store.open_local_store`。
        """
        from plobi.collectors import sources_config

        params = config if config is not None else sources_config.setup_params(SOURCE_ID)
        raw = str(params.get("conversations") or "")
        cids = [part.strip() for part in raw.replace("，", ",").split(",") if part.strip()]
        opened = (opener or store.open_local_store)()
        return cls(local_store=opened, cids=cids)

    def health(self) -> dict:
        """能开库、点了几扇、这台的会话总数——三格一起说，少一格就是一次误判。

        库能开**不等于采得到东西**：一条会话都没点名，那今天就是零条，
        这句话不许被"ok=True"盖掉（判据 5 那句"成功但空货不能当答案交出去"）。
        """
        if not self.cids:
            return {"ok": False, "state": "needs_setup",
                    "detail": f"本机库在位（{self.store.conversations} 个会话），但一条都没准入"}
        return {"ok": True, "state": "ready",
                "detail": f"准入 {len(self.cids)} 扇 / 本机共 {self.store.conversations} 个会话"}

    def pull(self) -> list[Occurrence]:
        """只读被点名的那几扇。**纯读**：一个字都不写库、一个字节都不动钉钉那侧。"""
        rows = store.recent_messages(self.store, self.cids, limit=self.limit, since_ms=self.since_ms)
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
