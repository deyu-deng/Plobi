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


def _conversations() -> list[dict]:
    """微信那份会话名单，从**侧车**枚举、从**它自己那张 `TalkerStore`** 读准入状态。

    侧车没在跑就抛（`state=unavailable` 那句为什么由 `sources_api` 那一层收成读数）——
    这一族的判据 2 早就写死了："枚举没抛异常"不等于活着，0 个会话与到不了是两句话。

    每一格都按界面上那一行给：`name` 是**他认得出的名字**（备注优先，查不到才是会话号并由
    `resolved` 说明白）、`kind` 是群／私聊／公众号里的哪一组、`last_at` 是这一扇最后一条在什么时候。
    那一手的判据不写在这里，在 :mod:`.roster`（三张表怎么拼、群为什么不许用 `nickName`）。
    """
    from . import roster
    from .collect_api import get_pipeline

    pipeline = get_pipeline()
    health = pipeline.health()
    if not health["ok"]:
        raise RuntimeError(health["detail"] or "采集器没在应答")

    return roster.rows(pipeline.client, status_of=pipeline.talkers.status_of)


def _admit(talker: str, status: str) -> None:
    """写决定到**微信自己那张表**：不搬到通用 `Ledger`，因为一条会话的准入状态只该 live 一处。"""
    from .collect_api import get_pipeline

    get_pipeline().talkers.set_status(
        talker, {"known": "known", "excluded": "excluded"}.get(status, "pending"))


#: 这只探针会交出去的码，**写成字面量**是给 `scripts/check_status_copy_coverage.py` 数得出来：
#: 只写 `{"code": state}` 的话，屏幕上真会出现的一句话在目录里缺没缺句子没人知道（门只能靠猜）。
#: 与 `sidecar.diagnose` 那一族 `STATE_*` 逐一对应，测试 `test_source_screen_copy_names_nothing.py` 钉住不漂移。
PROBE_CODES = ("build_missing", "go_missing", "key_missing", "not_listening", "ready")


def _prepare() -> tuple[bool, str]:
    """拉起服务之前的那次准备：替他试一次取密钥。失败也只是"少一件事做成"，不是故障。"""
    from . import keyhand

    return keyhand.fetch()


def _probe() -> dict:
    """微信这一条在本机**缺哪一步**——构建工具、可执行产物、数据密钥、服务应答，四步分别说。

    与钉钉那枚探针同一只手（`SourceSpec.probe`），公共层照样不认识微信：这一句只报前提，
    不报今天采没采到，也不报"能采多少"。四步的判断本来就在采集器自己那侧（`sidecar.diagnose`），
    这里只是把它接到声明层那一只手上，让设置页说得得出名字——
    10-25 之前这一条没有探针，四种不同的病在屏幕上并成一句「采集服务未能启动」。
    """
    from plobi.collectors import sidecar as common
    from . import sidecar as local

    listening = common.port_listening(SPEC.sidecar_port or 0)
    state, note = local.diagnose(healthy=listening)
    return {"ready": state == local.STATE_READY, "code": state, "detail": note}


SPEC = SourceSpec(
    "wechat",
    capabilities={
        "history_backfill": True,       # client.fetch(talker, day) 按天回溯
        "incremental_since": True,      # day 游标 + SeenStore
        # 10-31 起是真话：一条三天窗口（1679 条）里认出 146 件、落进池 12 件（1.88 MB），
        # 池里那一行的 `context.media` 带着读得回来的路径；只有图没有字的那条标题是「（图片）」。
        # 没落成的那些不是"坏了"而是"侧车今天还不知道"（微信没 checkpoint）——`media_absent` 那一格说这件事。
        "supports_media": True,
        "requires_manual_setup": True,  # 构建 + 数据密钥 + 微信登录
        "authoritative_replace": False,  # ingest 契约：本轮缺的不删
    },
    # 对话流：登录一次，之后是会话流（spec-layer §2.2「消息」）。
    category="software",
    site="https://weixin.qq.com",  # 图标从这一格抓一次（`collectors/icons.py`）；不是配置项，改它要重新装
    summary="本机微信的会话与群聊",

    # 开关的另一半：这一条背后有一个常驻进程（chatlog），关掉它就该真的收掉（用户 10-23）。
    # 端口与启动脚本都是**声明**，公共层照它们办事、仍然不出现任何信源名字（判据 4）。
    sidecar_port=5030,
    sidecar_start="scripts/chatlog_server.ps1",
    # 一条信源里面有很多个会话 ⇒ 采集情况要能按群聊/私聊逐个定。
    # 10-24 起这两只手就是那"怎么定"的正身：名单从侧车枚举、决定写回它自己那张 `TalkerStore`，
    # 设置页那块面板（`sources/conversation-list.tsx`）与钉钉看到的是**同一个组件、同一条路由**。
    conversation_scoped=True,
    conversations=_conversations,
    admission=_admit,
    # 缺哪一步要分得开（裁定 203 第 2 条：取不到就明说什么取不到）——这一格让上面那只手有地方说话。
    probe=_probe,
    # 数据密钥**不由他抄**：点开开关那一刻产品自己用 vendored 产物那一条 `key` 子命令取一次
    # （裁定 203 口径 2）。取没取到只回一句话，值永不出门；这一步抛了什么也不许把那次点击炸掉。
    sidecar_prepare=_prepare,

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
