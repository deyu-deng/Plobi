"""钉钉那条的**声明**（`specs/source-layer.md` §3.1）。

这一档以前**只有声明没有实现体**（本机那个客户端只是网关侧的收发通道）。2026-10-24 改了：
用户说「钉钉你两个方式都要支持。用户按需选择。只不过我肯定是用"我登录一次客户端"的方式」
并且当场登录了客户端 ⇒ 丙路（读那份本机库）第一次有了可验的对象，实现体在
:mod:`.store`（找目录 / 推钥匙 / 解出只读副本）与 :mod:`.collector`（只取被点名的会话）。

登记归口与假数据占位是两件事这条仍然成立：能力位里没实测的那一格留 `None`，
不拿 `False` 冒充测过（判据 5）。

这一包与它的 `source_id` 同名，但**发现按包走**：注册表认的是"包顶层有没有 `SPEC`"，
不是目录长得像不像（见 `registry.py` 发现器那一段）。
"""

from plobi.collectors.registry import SourceSpec, register

from . import localdb


def _conversations() -> list[dict]:
    """名单从**本机库**来（`tbconversation`），准入状态从通用 `Ledger` 来。

    这一只**可以抛**（没登录 / 读不出钥匙 / 盘读不到）——HTTP 边界那一层会把它收成
    `state=unavailable` 加那句为什么，界面上因此还能说人话（不在这里吞成空表：
    "没有会话"与"读不到"是两句话）。
    """
    from . import store
    from ..talkers import Ledger

    opened = store.open_local_store()
    try:
        ledger = Ledger()
        return [{"id": row["cid"], "name": row["title"] or row["cid"], "kind": row["type"],
                 "status": ledger.status_of("dingtalk", row["cid"])}
                for row in store.list_conversations(opened, limit=500)]
    finally:
        opened.close()


def _admit(talker: str, status: str) -> None:
    from ..talkers import Ledger

    Ledger().set_status("dingtalk", talker, status)


def _build():
    """执行方入口。**延迟 import**：`collector` 那一头 import 了 `..base`，而 base 的
    文档串指着注册表派生的词表——顶层就 import 它在这一族里已经成过环（`zju/_build` 同一处理）。"""
    from .collector import DingTalkCollector

    return DingTalkCollector.build()


SPEC = SourceSpec(
    "dingtalk",
    capabilities={
        # 实测（2026-10-24 这台，登录后）：本机库里 178 个会话、155 条消息——
        # 客户端只落它自己已经拉下来的那部分，**这侧没有回捞历史的路径**，所以是 False。
        # 这一格讲的是我们这侧的契约，不是"钉钉云端没有历史"。
        "history_backfill": False,
        # `pull()` 认 `since_ms`（按 `createdAt` 过滤），所以这一格是真的 True。
        "incremental_since": True,
        # 图片/文件那一族：库里 `contentType` 有 2(图)/501(文件)/3100(小程序)，
        # 但**正文之外的附件怎么取**这一版没实测（`attachments` 那一串 JSON 里的路径
        # 指向客户端自己的缓存，跨版本换过位置）⇒ 留 None，不拿 False 冒充测过。
        "supports_media": None,
        # 要用户自己动一次手：登录桌面客户端 + 点出要采的那几扇会话。
        "requires_manual_setup": True,
        # 一轮里没出现不等于"这条消息被取消了"（本机库只是缓存，客户端什么时候清它
        # 不是我们能预告的），按"本轮未出现"去关闭会判死一批。
        "authoritative_replace": False,
    },
    # 对话流，与微信同族 ⇒ 「消息」。
    category="message",
    site="https://www.dingtalk.com",  # 图标从这一格抓一次（`collectors/icons.py`）；不是配置项
    # 名字仍然由界面语言目录给（四语各有写法）：这一条不是"一个网站"，那一格的兜底
    # 只对「网站」那一类开（`sources_api._enrich` 那一段写着为什么）。
    setup_fields=(),
    # 「哪些会话采」上屏用的两只手（用户 10-24：「把微信和钉钉的会话采集都做成做到UI里面去。
    # 这样太麻烦了」）：名单来自本机库的 `tbconversation`，决定写进通用 `Ledger`。
    # 上一刀那格 cid 文本框因此撤掉——有了面板还留着它，就是**两处入口写同一个事实**。
    conversation_scoped=True,
    conversations=_conversations,
    admission=_admit,
    # 默认不点：这一路读的是他的 IM。点上之后**默认全采、逐扇可关**（与微信同一条口径，
    # 裁定 100 一）——这一条与上一刀"点名为采"相反，改的理由写在任务书顶部那段口径差里。
    # 10-24 之前这颗开关对调度**不起作用**（`collecting_sources()` 只看代码默认值），
    # 所以"默认关"那时只是个装饰；现在它真的算数，这一格才第一次有了"默认关"的含义。
    collecting=False,
    builder=_build,
    # 这一条在本机成不成立、今天哪条路走得通，由探针回答（它只报"库在不在位"，
    # 不报"采得到"）。理由与那颗假故障写在 :mod:`.localdb` 的模块头。
    probe=localdb.probe,
)

register(SPEC, replace=True)

__all__ = ["SPEC"]
