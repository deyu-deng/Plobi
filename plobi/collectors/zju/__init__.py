"""「学在浙大」collector —— 未提交作业 → 日程（判据 5，WP-SOCIAL-SOURCES-PLUGGABLE）。

公开面：:class:`ZjuCollector` / :func:`to_occurrences` / :func:`to_local_naive` /
:class:`PullReport`，加上状态那一族（一格一句，见 :mod:`.status`）。

这一档**没有 HTTP 接线**，是刻意的：注册表那条判据写的是「加一个平台 = 登记一行 +
实现 Collector，不改 ``collect_api.py``/``web_server.py`` 的手写接线」，所以面上什么时候
能点到它，归触发那一刀（``WP-SCHEDULE-REAL-PLAN``）。

:data:`SPEC` 是这一条信源向注册表自报的形状（`specs/source-layer.md` §3.1）——**现在
它不再由 ``registry.py`` 那一行手写元组登记**，加一条信源＝加一个这样的包。
"""

from plobi.collectors.registry import SetupField, SourceSpec, register

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
        # 仍未实测也未拍：11 个字段里没有「已提交/未提交」状态位，「提交后会不会整条消失」
        # 没测；叠加 `ingest.py:18` 的「本轮缺的不删」⇒「完成即关闭」是产品决定。
        # 留 `None` 是有意的：**消费方一问就报错**，不许拿猜测当读数。
        "authoritative_replace": None,
    },
    # 无客户端可装 ⇒ 连接器形状是「一个地址 + 账密」，归「网站」不归「消息」。
    category="website",
    # 首次配置两格，都是**非密钥**：地址与账号名。密码与那两个 key 在根 `.env`
    # 里（能力位注记原话「账密已给（根 .env）」），**声明层压根不收密钥**——
    # 所以这里只能声明这两格，这不是省略。
    setup_fields=(
        SetupField(
            key="base_url",
            label="School system address",
            kind="url",
            required=True,
            help="The portal you sign in to.",
        ),
        SetupField(
            key="account",
            label="Account name",
            required=True,
            help="Not the password — that stays in .env.",
        ),
    ),
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
