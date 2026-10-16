"""信源注册表 —— 加一个平台 = 在这里登记一行 + 实现 :class:`Collector`，**不改公共层**（判据 3）。

病根形状（裁定 98 二/判据 3）：`plobi/agenda/store.py` 原来拿一个硬编 tuple 当信源词表，
加「学在浙大」就得动公共层——那就不叫可插拔。现在词表由这张表派生，存储层只在校验时来读。

能力位是**声明式**的（裁定 98 三判据 4）：调度层读 `capability()`，不许写
`if source == "wechat"`。取值口径有一条与网关**相反**，是故意的：

- 网关那两个 `getattr(adapter, SUPPORTS_*, True)` 兜底是 True（读不到就当支持）；
- 采集侧**读不到就报错，未实测就报错**（``None`` 会被 :func:`require` 拒掉）。
  理由与判据 7 那个病根同源：兜底猜一个值＝静默按某种口径跑，用户以为在采、其实没采。

``collecting=False`` 表示"这个名字已经在词表里、但没有任何东西在采它"（钉钉只有网关侧收发通道、
学在浙大还没接）——登记归口与假数据占位是两件事：**登记可以，编造读数不行**（判据 5 未验就这么记）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Optional

# 人手写、不是采来的——它是日程库里的"非信源信源"，归存储层自己的词表，不进注册表。
# ⚠️ **这一档允许按名字分支**（`agenda/service.py` 里那两处 `if source == "manual"`）：
# 它不是平台，是**来源档位**——"人写的条目不许被采集器覆盖"这种规则永远不该改成读能力位，
# 因为没有任何 collector 会来声明它。下一个读的人别"顺手统一"。
NON_COLLECTOR_SOURCES: tuple[str, ...] = ("manual",)

#: 每个信源必须逐位声明的能力（缺一位 = 注册时报错，不许留空让人猜）。
CAPABILITY_KEYS: tuple[str, ...] = (
    "history_backfill",      # 能不能拉历史（按天回溯…）
    "incremental_since",     # pull(since=…) 认不认
    "supports_media",        # 图 / 语音 / 文件
    "requires_manual_setup",  # 用户必须人肉做一次（构建、密钥、放文件、扫码…）
    "authoritative_replace",  # 本轮没出现的行可否视为取消（现契约做不到 ⇒ False）
)


class UnknownSource(KeyError):
    """信源没在这张表里登记过——它就不该出现在库里。"""


class UnknownCapability(KeyError):
    """能力位名字问错了（拼错的能力位不该默默当假）。"""


class CapabilityUnmeasured(RuntimeError):
    """登记了但没实测（``None``）。消费方要的是读数，不是一个猜出来的默认值。"""


@dataclass(frozen=True)
class SourceSpec:
    source_id: str
    capabilities: Mapping[str, Optional[bool]]
    collecting: bool = True
    #: 执行方**构造这一条采集器**的唯一入口（`plobi/collectors/sweep.py` 只认这一格，不按名字分支）。
    #: `None` 是一个读数而不是省略：注册表说这条在采、却没有任何一方构造得出它 ——
    #: 2026-10-15 教务那一刀就断在这里（`collecting_sources()` 全仓零调用方）。
    builder: Optional[Callable[[], Any]] = None

    def __post_init__(self) -> None:
        if not self.source_id or not self.source_id.strip():
            raise ValueError("source_id 不能是空串")
        missing = [key for key in CAPABILITY_KEYS if key not in self.capabilities]
        if missing:
            raise ValueError(f"{self.source_id} 少了能力位声明：{', '.join(missing)}")
        extra = [key for key in self.capabilities if key not in CAPABILITY_KEYS]
        if extra:
            raise ValueError(f"{self.source_id} 声明了不认识的能力位：{', '.join(extra)}")


_SPECS: dict[str, SourceSpec] = {}


def register(spec: SourceSpec, *, replace: bool = False) -> SourceSpec:
    if spec.source_id in NON_COLLECTOR_SOURCES:
        raise ValueError(f"{spec.source_id} 不是信源，它是 {NON_COLLECTOR_SOURCES} 那一档")
    if spec.source_id in _SPECS and not replace:
        raise ValueError(f"{spec.source_id} 已登记；要改请显式 replace=True")
    _SPECS[spec.source_id] = spec
    return spec


def unregister(source_id: str) -> None:
    """测试与"这一档撤了"的出口——不存在就报错，不静默。"""
    if source_id not in _SPECS:
        raise UnknownSource(source_id)
    del _SPECS[source_id]


def spec(source_id: str) -> SourceSpec:
    try:
        return _SPECS[source_id]
    except KeyError:
        raise UnknownSource(source_id) from None


def source_ids() -> tuple[str, ...]:
    return tuple(sorted(_SPECS))


def allowed_sources() -> tuple[str, ...]:
    """库里 `source` 列的合法取值 = 人手写的档 + 这张表派生的信源（判据 3 的那一条）。"""
    return (*NON_COLLECTOR_SOURCES, *source_ids())


def capability(source_id: str, key: str) -> Optional[bool]:
    """读数：True / False / None（登记了但未实测）。不认识就报错，不返回默认值。"""
    spec_source = spec(source_id)
    if key not in CAPABILITY_KEYS:
        raise UnknownCapability(key)
    return spec_source.capabilities[key]


def require(source_id: str, key: str) -> bool:
    """调度层用这个：未实测（``None``）当场报错，逼人去测或去改形状，不许猜。"""
    value = capability(source_id, key)
    if value is None:
        raise CapabilityUnmeasured(f"{source_id}.{key} 登记了但没实测，不许拿猜测当读数")
    return value


def collecting_sources() -> tuple[str, ...]:
    return tuple(sorted(sid for sid, item in _SPECS.items() if item.collecting))


def _build_zju() -> Any:
    """教务那条的执行方入口。**延迟 import**：`collectors/zju` 那头要读这张表派生的词表。

    `transport` 是**必填位**（判据 7：默认回退到真网络调用就是那颗雷的形状），所以这里显式给
    生产那一只 `UrllibTransport` —— 这一格写漏一次，每天那一轮就稳定报 `build_failed`
    （10-15 13:07 真跑就是这么抓出来的，不是测试抓的）。
    """
    from plobi.collectors.zju import ZjuCollector
    from plobi.collectors.zju.auth import UrllibTransport

    return ZjuCollector(transport=UrllibTransport())


def _build_wechat() -> Any:
    """微信那条的执行方入口。

    缺这一格的时候，每天那一轮把 `wechat` 报成 ``no_builder``——"注册表说这条在采，
    但没有任何一方构造得出它的采集器"——于是**真原因永远问不到**：sidecar 是没产物、
    没配置、没密钥还是没在听，全都藏在一句"不会有东西进来"后面。构造出来之后，
    这些由 ``ChatlogPipeline.collect()`` 逐条报状态。

    管线自己会先探活再花钱（不健康就抛带状态的 ``ChatlogDead``），所以这里不需要
    再套一层闸；构造全部走产品自己的落点（`$PLOBI_HOME` 下的配置与 sidecar handoff），
    没有第二个真源。
    """
    from plobi.collectors.chatlog.pipeline import ChatlogPipeline

    return ChatlogPipeline()


def _declare(items: Iterable[SourceSpec]) -> None:
    for item in items:
        register(item, replace=True)


_declare(
    (
        SourceSpec(
            "wechat",
            {
                "history_backfill": True,       # client.fetch(talker, day) 按天回溯
                "incremental_since": True,      # day 游标 + SeenStore
                "supports_media": False,        # 采集层代码零媒体处理
                "requires_manual_setup": True,  # 构建 + 数据密钥 + 微信登录
                "authoritative_replace": False,  # ingest 契约：本轮缺的不删
            },
            # 执行方入口补上了（10-16）：此前这条每轮都被报成 `no_builder`，
            # 真原因问不到下一层。构造不做静默网络调用以外的假设——管线自己先探活。
            builder=_build_wechat,
        ),
        SourceSpec(
            "timetable",
            {
                "history_backfill": True,       # 文件里有什么就是什么
                "incremental_since": False,     # collector 收了 since 但忽略
                "supports_media": False,
                "requires_manual_setup": True,  # 用户指定目录里放一个 .ics
                "authoritative_replace": False,
            },
        ),
        SourceSpec(
            # 钉钉：本机有客户端，但那是网关侧的**收发通道**，采集层对它零实现。
            "dingtalk",
            {
                "history_backfill": False,      # Stream 长连接只收连上之后的消息
                "incremental_since": True,
                "supports_media": None,         # 本仓只做文本收发，没测过媒体
                "requires_manual_setup": True,  # 开放平台建应用 + 两个 key
                "authoritative_replace": False,
            },
            collecting=False,
        ),
        SourceSpec(
            # 学在浙大：Web 系统，无客户端可装，走接口直取。采集器在 `collectors/zju/`。
            "zju",
            {
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
            # 采集器与入库都真跑过（交账在任务书里），缺的是**执行方那一层**，10-15 补在
            # `plobi/collectors/sweep.py`。用户 2026-10-15 13:0x 拍的形状是「每天自动采，
            # 新条目进待确认」⇒ 这一格从此是一条被点过的读数，不是我替他填的默认值（判据 1）。
            collecting=True,
            builder=_build_zju,
        ),
    )
)
