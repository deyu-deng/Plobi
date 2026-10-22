"""钉钉那条的**声明**（`specs/source-layer.md` §3.1）。

它只有声明、没有实现体：本机那个客户端是网关侧的**收发通道**，采集层对它零实现。
登记可以，编造读数不行（判据 5）——所以 ``collecting=False``，而 ``supports_media``
保持 ``None``（本仓只做文本收发，没测过媒体），不是拿 ``False`` 冒充测过。

这一档**只有一个包没有 ``__init__.py`` 也会被认出来**：注册表的发现认的是"包顶层有没有
``SPEC``"，不是"目录长得像不像"。所以本包的模块名与它的 ``source_id`` 不同——发现按
包走，而 ``SPEC`` 里那个 id 才是信源在词表里的名字。
"""

from plobi.collectors.registry import SourceSpec, register

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

    # 名字已经在词表里、但没有任何东西在采它——登记归口与假数据占位是两件事。
    collecting=False,
)

register(SPEC, replace=True)

__all__ = ["SPEC"]