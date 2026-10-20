"""chatlog collector — WeChat messages into agenda candidates.

Public surface:

- :class:`ChatlogPipeline` — run a sweep or handle one pushed message
- :class:`CollectorConfig` — whitelist + endpoint configuration
- :class:`IngestReport` — what a sweep did

``client`` / ``state`` / ``timeparse`` / ``rules`` are internals. Nothing in
this package may send more than the matched snippet off-device
(docs/adr/0010-collection-whitelist-privacy-boundary.md).
"""

from plobi.collectors.registry import CADENCE_FIELD, SourceSpec, register

from .client import ChatlogUnavailable, ChatMessage
from .config import CollectorConfig
from .confirm import Candidate, Confirmer, ConfirmContext, HeuristicConfirmer, NullConfirmer
from .pipeline import ChatlogDead, ChatlogPipeline, IngestReport


def _build() -> ChatlogPipeline:
    """微信那条的执行方入口。

    缺 ``builder`` 的时候，每天那一轮把 ``wechat`` 报成 ``no_builder``——"注册表说这条在采，
    但没有任何一方构造得出它的采集器"——于是**真原因永远问不到**：sidecar 是没产物、
    没配置、没密钥还是没在听，全都藏在一句"不会有东西进来"后面。构造出来之后，
    这些由 ``ChatlogPipeline.collect()`` 逐条报状态。

    管线自己会先探活再花钱（不健康就抛带状态的 ``ChatlogDead``），所以这里不需要
    再套一层闸；构造全部走产品自己的落点（`$PLOBI_HOME` 下的配置与 sidecar handoff），
    没有第二个真源。

    补上这一格是 10-16 的事（此前这条每轮都被报成 `no_builder`，真原因问不到下一层）。
    构造不做静默网络调用以外的假设——管线自己先探活。
    """
    from .pipeline import ChatlogPipeline as _ChatlogPipeline

    return _ChatlogPipeline()


SPEC = SourceSpec(
    "wechat",
    capabilities={
        "history_backfill": True,       # client.fetch(talker, day) 按天回溯
        "incremental_since": True,      # day 游标 + SeenStore
        "supports_media": False,        # 采集层代码零媒体处理
        "requires_manual_setup": True,  # 构建 + 数据密钥 + 微信登录
        "authoritative_replace": False,  # ingest 契约：本轮缺的不删
    },
    # 对话流：登录一次，之后是会话流（spec-layer §2.2「消息」）。
    category="message",
    # 这一格里原来**一个字段都没有**，那是真话：微信首次配置要的是扫码与数据密钥，
    # 两样都不在「非密钥参数」这一层。现在多出来的一格也不是密钥——是**这条信源多久该被打一次**
    # （裁定 191 二），它本来就属于用户那一层，所以由这一条自己声明出来（`CADENCE_FIELD` 的
    # 真源在注册表，四个包引同一份）。
    setup_fields=(CADENCE_FIELD,),
    builder=_build,
)

register(SPEC, replace=True)

__all__ = [
    "SPEC",
    "Candidate",
    "ChatMessage",
    "ChatlogDead",
    "ChatlogPipeline",
    "ChatlogUnavailable",
    "CollectorConfig",
    "Confirmer",
    "ConfirmContext",
    "HeuristicConfirmer",
    "IngestReport",
    "NullConfirmer",
]
