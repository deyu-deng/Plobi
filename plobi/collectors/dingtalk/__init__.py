"""钉钉那条的**声明**（`specs/source-layer.md` §3.1）。

它只有声明、没有实现体：本机那个客户端是网关侧的**收发通道**，采集层对它零实现。
登记可以，编造读数不行（判据 5）——所以 ``collecting=False``，而 ``supports_media``
保持 ``None``（本仓只做文本收发，没测过媒体），不是拿 ``False`` 冒充测过。

这一档**只有一个包没有 ``__init__.py`` 也会被认出来**：注册表的发现认的是"包顶层有没有
``SPEC``"，不是"目录长得像不像"。所以本包的模块名与它的 ``source_id`` 不同——发现按
包走，而 ``SPEC`` 里那个 id 才是信源在词表里的名字。
"""

from plobi.collectors.registry import SourceSpec, register

from . import localdb

SPEC = SourceSpec(
    "dingtalk",
    capabilities={
        "history_backfill": False,      # Stream 长连接只收连上之后的消息
        "incremental_since": True,
        "supports_media": None,         # 本仓只做文本收发，没测过媒体
        "requires_manual_setup": True,  # 开放平台建应用 + 两个 key
        "authoritative_replace": False,
    },
    # 对话流（Stream 长连接），与微信同族 ⇒ 「消息」。
    category="message",
    site="https://www.dingtalk.com",  # 图标从这一格抓一次（`collectors/icons.py`）；不是配置项，改它要重新装
    # 这一条**在本机成不成立**，由探针回答（用户 10-24：「我们组织没有开放CLI。要用别的方式」）。
    #
    # 它要改的是屏上那一颗**假故障**：今天这条是 `enabled=true` + `builder=None` ⇒ `error/internal`
    # （「本机采集组件暂未就绪」＝说我们的产品坏了）。真话是"这台机器给不出它要的前提"——
    # 甲路（官方 CLI）要组织管理员开通、乙路（企业内部应用）要管理员建应用，而丙路（本地客户端库）
    # 在这台机器上**客户端从没登录过**。那是**未连接**，不是故障。全文在 :mod:`.localdb` 的模块头。
    #
    # 探针说 `ready=True` 而仍然没人构造得出执行方，那一颗红点**继续是红**（不许用探针把
    # 自己的缺口洗绿）：`sources_api.status_of` 里那一支的次序钉着这条。
    probe=localdb.probe,

    # 名字已经在词表里、但没有任何东西在采它——登记归口与假数据占位是两件事。
    collecting=False,
)

register(SPEC, replace=True)

__all__ = ["SPEC"]