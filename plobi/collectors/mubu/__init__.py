r"""幕布那条的**声明**（`specs/source-layer.md` §3.1：加一个平台＝加一个包）。

分类是 `software`（10-27 把这一格收成一条轴之后新增的那一格）——它**是**一个网站，
但用户拍的是「两条都要：本机为快、接口为全」，而这台机器上真的装着它的桌面客户端，
我们借的是那个客户端在本机留下的登录态。按"从哪来"这一条轴，它落在「软件」。
「文档」那个形状不再占分类那一格，它是池里的 `kind="doc"`（屏幕上那一排 chip 由它派生）。

**没有 `setup_fields`**：这一条不要用户填 token，也不要填手机号密码。
2026-10-27 用户原话是「我找不到 token 呀。我没这么厉害」，而实测本机客户端那份数据里
就有今天那枚 JWT——借它比让用户去浏览器开发者工具里抠一行字符串诚实得多。
登录态没了就说"客户端尚未在本机登录"（那一句四语目录里本来就有），不做静默重登：
存一份别人的账号密码是比抠 token 更重的一件事，那一格等他真要的时候再加。
"""

from __future__ import annotations

from plobi.collectors.registry import SourceSpec, register

from . import localdb


def _build():
    """执行方入口。**延迟 import**：`collector` 那一头 import 了 `..base`，
    而发现这一层正在导入本包——顶层就 import 它会成环（`zju/_build`、钉钉同一处理）。"""
    from .collector import MubuCollector

    return MubuCollector.build()


SPEC = SourceSpec(
    "mubu",
    capabilities={
        # 全量拉一次是真的：名单那一趟 `get_all_documents_page` 回的是根上全部条目，
        # 目录再逐层走（`collector._roster`），不是"只取最近十篇"。
        "history_backfill": True,
        # `pull(since=…)` 认 `updateTime`（毫秒），所以这一格是真的 True。
        "incremental_since": True,
        # 正文里的图片/附件：`definition` 的节点里带 `src`，但**附件怎么落**这一版没实测
        # （它自己的图在 `document-image.mubu.com`，取法没核）⇒ 留 None，不拿 False 冒充测过。
        "supports_media": None,
        # 不要用户动手：装好客户端并登录即可，屏幕上没有要填的格子。
        "requires_manual_setup": False,
        # 一篇文档这一轮没出现 ≠ 它被删了（一轮只取回上限那么多篇正文）。
        # 按"本轮未出现"去关闭会把没取到的那批一起判死。
        "authoritative_replace": False,
    },
    cadence="every 1d",
    category="software",
    site="https://mubu.com",
    setup_fields=(),
    # 默认不点：这一条读的是他的个人笔记。装上客户端、屏幕上点一下开关才采。
    collecting=False,
    builder=_build,
    # 这一条在本机成不成立由探针回答（它只报"有没有一个登录好的客户端可借"，不报"采得到"）。
    probe=localdb.probe,
)

register(SPEC, replace=True)

__all__ = ["SPEC"]
