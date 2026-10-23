"""chatlog collector — WeChat messages into agenda candidates.

Public surface:

- :class:`ChatlogPipeline` — run a sweep or handle one pushed message
- :class:`CollectorConfig` — whitelist + endpoint configuration
- :class:`IngestReport` — what a sweep did

``client`` / ``state`` / ``timeparse`` / ``rules`` are internals. Nothing in
this package may send more than the matched snippet off-device
(docs/adr/0010-collection-whitelist-privacy-boundary.md).
"""

from plobi.collectors.registry import SourceSpec, register

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
    site="https://weixin.qq.com",  # 图标从这一格抓一次（`collectors/icons.py`）；不是配置项，改它要重新装

    # 开关的另一半：这一条背后有一个常驻进程（chatlog），关掉它就该真的收掉（用户 10-23）。
    # 端口与启动脚本都是**声明**，公共层照它们办事、仍然不出现任何信源名字（判据 4）。
    sidecar_port=5030,
    sidecar_start="scripts/chatlog_server.ps1",
    # 一条信源里面有很多个会话 ⇒ 采集情况要能按群聊/私聊逐个定（名单与准入在 `/api/collect/talkers`）。
    conversation_scoped=True,

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
