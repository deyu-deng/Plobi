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

**分类**（`category`，2026-10-19 用户拍定，见 `Docs/specs/source-layer.md` §2）：轴是
**「首次配置要用户做什么」**，不是重要性、不是数据形状、不是来源方。它是**连接器的形状**，
和那五个能力位同一个套路：必填、缺位注册即报错、不许留空让人猜。

裁定 151 的平权约束在这一格是硬的：**闭集里没有任何一个值表示"更重要"**。加第七个值
不许用来"先放着"——要加就改规格 §2 并说清它为什么仍是连接器形状而不是数据形状。
"""

from __future__ import annotations

from dataclasses import dataclass, field
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


#: 分类闭集（用户 10-19 拍定）。**这不是等级表**——闭集里没有一个值表示"更重要"，
#: 顺序也不表达优先级（`specs/source-layer.md` §2.1）。`custom` 不是第六个分类，
#: 它是"都不合适？接一个自己的"那个逃生口，也就是可插拔在产品面上的样子。
SOURCE_CATEGORIES: tuple[str, ...] = ("message", "social", "website", "local", "system", "custom")

#: 给人看的名字。四语言在 `apps/desktop/src/i18n/{en,zh,ja,zh-hant}.ts` 的
#: `settings.sources.categories` —— 这里是 slug，不是文案（裁定 45.4 那条：slug 不上屏）。
CATEGORY_SLUGS: frozenset[str] = frozenset(SOURCE_CATEGORIES)


class UnknownCategory(ValueError):
    """分类值不认识——闭集不接受"先放着"，也不接受拼错。"""


@dataclass(frozen=True)
class SourceSpec:
    source_id: str
    capabilities: Mapping[str, Optional[bool]]
    #: 分类（闭集，见 :data:`SOURCE_CATEGORIES`）。**关键字位且必填**。
    #:
    #: 为什么是关键字位而不是排在能力位前面：这一格是 10-19 才加的，而**每一个**
    #: 现存构造点（信源本体 4 处 + 测试 7 处）都按 ``SourceSpec(id, {...})`` 那个
    #: 位置参数形状写的。插进位置参数会把它们全部变成"第二个位置参数突然是分类"——
    #: 那是**一次与本刀无关的静默错位**，而这里要防的正是错位。
    #:
    #: 为什么必填（没有默认值）：缺位就在**构造那一刻**报错，而不是"注册时才发现
    #: 少了一格"。默认值 ``None`` 会让漏写的那个悄悄过去，等于把 ``capabilities``
    #: 当年那道"缺一位 = 注册时报错"的口子重新开一次。
    category: str = field(kw_only=True)
    collecting: bool = True
    #: 执行方**构造这一条采集器**的唯一入口（`plobi/collectors/sweep.py` 只认这一格，不按名字分支）。
    #: `None` 是一个读数而不是省略：注册表说这条在采、却没有任何一方构造得出它 ——
    #: 2026-10-15 教务那一刀就断在这里（`collecting_sources()` 全仓零调用方）。
    builder: Optional[Callable[[], Any]] = None

    def __post_init__(self) -> None:
        if not self.source_id or not self.source_id.strip():
            raise ValueError("source_id 不能是空串")
        if not self.category:
            raise ValueError(f"{self.source_id} 没声明分类（category 必填，见 spec-layer §2）")
        if self.category not in CATEGORY_SLUGS:
            raise UnknownCategory(
                f"{self.source_id} 的分类 {self.category!r} 不在闭集里："
                f"{', '.join(SOURCE_CATEGORIES)}。要加新分类先改 spec-layer §2，"
                "并说清它为什么仍是「首次配置要用户做什么」而不是数据形状"
            )
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


def source_category(source_id: str) -> str:
    """这条信源属于哪个分类（spec-layer §2 的那个闭集）。

    读数而不是推导：分类是**声明**的一格，不是从能力位或名字猜出来的——和那五位
    同一个道理，猜出来的那份会让"这条为什么在这一类"永远问不到。
    """
    return spec(source_id).category


def sources_by_category() -> dict[str, tuple[str, ...]]:
    """按分类分组的信源 id。**分组顺序照闭集的顺序**，不按字母、不按信源名。

    面的分类筛选读它；空分类也出现（只是没有成员）——设置里那一栏要能显示
    "这一类还没有接任何信源"，那也是一句话，不该由界面自己编。
    """
    return {
        category: tuple(sorted(sid for sid, item in _SPECS.items() if item.category == category))
        for category in SOURCE_CATEGORIES
    }


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
            capabilities={
                "history_backfill": True,       # client.fetch(talker, day) 按天回溯
                "incremental_since": True,      # day 游标 + SeenStore
                "supports_media": False,        # 采集层代码零媒体处理
                "requires_manual_setup": True,  # 构建 + 数据密钥 + 微信登录
                "authoritative_replace": False,  # ingest 契约：本轮缺的不删
            },
            # 对话流：登录一次，之后是会话流（spec-layer §2.2「消息」）。
            category="message",
            # 执行方入口补上了（10-16）：此前这条每轮都被报成 `no_builder`，
            # 真原因问不到下一层。构造不做静默网络调用以外的假设——管线自己先探活。
            builder=_build_wechat,
        ),
        SourceSpec(
            "timetable",
            capabilities={
                "history_backfill": True,       # 文件里有什么就是什么
                "incremental_since": False,     # collector 收了 since 但忽略
                "supports_media": False,
                "requires_manual_setup": True,  # 用户指定目录里放一个 .ics
                "authoritative_replace": False,
            },
            # 「用户往某目录放一个 .ics」⇒ 连接器形状是磁盘路径，归「本地」不归「网站」
            # （这一档同时还有一个 `timetable.ics_url`，那是同一份数据的另一种接法，
            #   登记成一条还是两条属 spec-layer §10 未决，不在下面混着记）。
            category="local",
        ),
        SourceSpec(
            # 钉钉：本机有客户端，但那是网关侧的**收发通道**，采集层对它零实现。
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
            collecting=False,
        ),
        SourceSpec(
            # 学在浙大：Web 系统，无客户端可装，走接口直取。采集器在 `collectors/zju/`。
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
            # 采集器与入库都真跑过（交账在任务书里），缺的是**执行方那一层**，10-15 补在
            # `plobi/collectors/sweep.py`。用户 2026-10-15 13:0x 拍的形状是「每天自动采，
            # 新条目进待确认」⇒ 这一格从此是一条被点过的读数，不是我替他填的默认值（判据 1）。
            collecting=True,
            builder=_build_zju,
        ),
    )
)
