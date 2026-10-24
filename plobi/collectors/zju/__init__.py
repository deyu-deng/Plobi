"""「学在浙大」collector —— 未提交作业 → 日程（判据 5，WP-SOCIAL-SOURCES-PLUGGABLE）。

公开面：:class:`ZjuCollector` / :func:`to_occurrences` / :func:`to_local_naive` /
:class:`PullReport`，加上状态那一族（一格一句，见 :mod:`.status`）。

这一档**没有 HTTP 接线**，是刻意的：注册表那条判据写的是「加一个平台 = 登记一行 +
实现 Collector，不改 ``collect_api.py``/``web_server.py`` 的手写接线」，所以面上什么时候
能点到它，归触发那一刀（``WP-SCHEDULE-REAL-PLAN``）。

:data:`SPEC` 是这一条信源向注册表自报的形状（`specs/source-layer.md` §3.1）——**现在
它不再由 ``registry.py`` 那一行手写元组登记**，加一条信源＝加一个这样的包。
"""

from plobi.collectors.registry import SourceSpec, register

from .collector import PullReport, ZjuCollector, to_local_naive, to_occurrences
from .config import ZjuConfig
from .status import (
    SOURCE_STATES,
    ZjuUnavailable,
    note_for,
)


def _build() -> ZjuCollector:
    """教务那条的执行方入口。**延迟 import**：``collector`` 那头要读注册表派生的词表，
    而注册表的发现正是在导入本包——顶层就 import 它会成一个环。

    `transport` 是**必填位**（判据 7：默认回退到真网络调用就是那颗雷的形状），所以这里显式给
    生产那一只 `UrllibTransport` —— 这一格写漏一次，每天那一轮就稳定报 `build_failed`
    （10-15 13:07 真跑就是这么抓出来的，不是测试抓的）。
    """
    from .collector import ZjuCollector as _ZjuCollector
    from .auth import UrllibTransport

    return _ZjuCollector(transport=UrllibTransport())


SPEC = SourceSpec(
    "zju",
    capabilities={
        # 实测：一发 `GET /api/todos` 就回 48 条（含排到 2027-01-05 的），
        # 且 `limit=5` 与 `limit=200` 都回 48 ⇒ 不按 limit 截断，历史一次拿全。
        "history_backfill": True,
        # **没实测到增量端点**："不按 limit 截断"不等于"认 since"。
        # 这一格记 False 讲的是**我们这侧的契约**：`pull(since=…)` 收了不用，
        # 每轮全量 + 按 `uid` 幂等。站方有没有增量端点仍是未测，别把这句读成"学校没有"。
        "incremental_since": False,
        # 实测：11 个字段全是文本/布尔/数组，无图片、无附件、无语音。
        "supports_media": False,
        # 账密已给（根 .env）；`getKaptchaStatus` 是**站方服务端开关**，不是这信源的性质，
        # 撞上 true 必须报「要人工验证码」并停手，不许自动识别。
        "requires_manual_setup": True,
        # **实测掉了**（裁定 190／191：完成态的真源就是这一家，不是用户在桌面上勾那一下）。
        # 依据是 `plobi-source-collect` 自己那五轮的正文（这台在
        # `D:/Data/AppData/plobi/cron/output/552aaf2ee773/`，10-20 现读）：
        # 10-16 **46** → 10-17 **44** → 10-18 **44** → 10-19 **43** → 10-20 **45** 条，
        # 而库里这一家有 52 行 ⇒ **今天这一轮的清单里至少少了 7 行**；条数不在动的那几轮是原样
        # 复读（`unchanged`），在动的那几轮有增有减 ⇒ 不是分页截断（`limit=5` 与 `limit=200`
        # 回同样条数，`history_backfill` 那位记的就是这次实测）。
        # 11 个字段里**没有**「已提交／未提交」状态位 ⇒ 平台侧唯一给得出的完成信号就是
        # 「从清单里消失」。那就按它：本轮未出现 ⇒ 标失效，由公共层执行
        # （:func:`plobi.collectors.ingest.authoritative_close`），**只改状态不删行**（裁定 42.2 第 5 项）。
        # 两句仍要照实留着：①「消失」＝已提交 还是 ＝已截止，平台没区分——两者都不该再被排，
        # 所以这个区别不影响排程那侧的结论，但别把它读成"学校告诉我们他交了"；
        # ②**这一轮一条都不回 ⇒ 不关闭**（清单空更像采集坏了，不是作业清零），这一条是防"一次抖动判死一批"。
        "authoritative_replace": True,
    },
    # 无客户端可装 ⇒ 连接器形状是「一个地址 + 账密」，归「网站」不归「消息」。
    category="website",
    site="https://courses.zju.edu.cn",  # 图标从这一格抓一次（`collectors/icons.py`）；不是配置项，改它要重新装
    #: 这一条**叫什么**由它自己说（用户 10-24：「网站就显示网站名就行了，不要叫什么"教务"」）。
    #: 「学在浙大」是这个平台自己的名字（`Docs/DECISIONS.md` 裁定 190 那一行用的就是这四个字）。
    #: 为什么这里要写一份而不能全靠抓：`courses.zju.edu.cn` 的根路径对无 cookie 的裸 GET 现回
    #: **400**（10-24 两条 UA 各试一次都是），抓不到 `<title>` ——那是站方给不给出首页的事，
    #: 不是这条信源没有名字。第三方的网站类信源不填这一格，就吃站点自报的名（`icons.py`）。
    display_name="学在浙大",

    # **这一条没有首次配置格**（10-23 删掉「教务系统地址」「账号名」两格）。
    # 用户对着那一屏问的是「这个地址的填空真的有用吗？我填了地址又会发生什么呢？还有账户名填了会有什么用呢？」
    # ——答案是没有：采集器**一个字都没读**这两格（SSO 入口是 `auth.` 里的常量，账号密码只走
    # `auth.read_credentials()` 那一只读路）。留着它俩等于给用户一个"我配了点什么"的假动作，
    # 而声明层写进 `sources.yaml` 的键从此没有任何消费方。删掉不是丢功能，是把假动作收走。
    # 能力位注记原话「账密已给（根 .env）」仍然成立；要不要支持"换一所学校"，等真有第二种学校时再开格。
    setup_fields=(),
    # 采集器与入库都真跑过（交账在任务书里），缺的是**执行方那一层**，10-15 补在
    # `plobi/collectors/sweep.py`。用户 2026-10-15 13:0x 拍的形状是「每天自动采，
    # 新条目进待确认」⇒ 这一格从此是一条被点过的读数，不是我替他填的默认值（判据 1）。
    collecting=True,
    builder=_build,
)

register(SPEC, replace=True)

__all__ = [
    "SPEC",
    "SOURCE_STATES",
    "PullReport",
    "ZjuCollector",
    "ZjuConfig",
    "note_for",
    "to_local_naive",
    "to_occurrences",
]
