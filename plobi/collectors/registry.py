"""信源注册表 —— 加一个平台 = **加一个自带 ``SPEC`` 的包**，本文件一行不改（判据 3）。

病根形状（裁定 98 二/判据 3）：`plobi/agenda/store.py` 原来拿一个硬编 tuple 当信源词表，
加「学在浙大」就得动公共层——那就不叫可插拔。现在词表由这张表派生，存储层只在校验时来读。

2026-10-19 往前一步（`Docs/specs/source-layer.md` §3.1）：连**这张表自己**那份手写
`_declare(...)` 元组也拆了——那四条搬进各自的包，由 :func:`ensure_discovered` 发现。
本文件**不再出现任何信源 id**（包括注释里），这条由
``tests/plobi/test_source_registry_pluggable.py::test_registry_module_names_no_source_at_all`` 钉住。

能力位是**声明式**的（裁定 98 三判据 4）：调度层读 `capability()`，不许按信源名字
分支。取值口径有一条与网关**相反**，是故意的：

- 网关那两个 `getattr(adapter, SUPPORTS_*, True)` 兜底是 True（读不到就当支持）；
- 采集侧**读不到就报错，未实测就报错**（``None`` 会被 :func:`require` 拒掉）。
  理由与判据 7 那个病根同源：兜底猜一个值＝静默按某种口径跑，用户以为在采、其实没采。

``collecting=False`` 表示"这个名字已经在词表里、但没有任何东西在采它"（钉钉只有网关侧收发通道）——
登记归口与假数据占位是两件事：**登记可以，编造读数不行**（判据 5 未验就这么记）。

**分类**（`category`，2026-10-19 用户拍定，见 `Docs/specs/source-layer.md` §2）：轴是
**「首次配置要用户做什么」**，不是重要性、不是数据形状、不是来源方。它是**连接器的形状**，
和那五个能力位同一个套路：必填、缺位注册即报错、不许留空让人猜。

裁定 151 的平权约束在这一格是硬的：**闭集里没有任何一个值表示"更重要"**。加第七个值
不许用来"先放着"——要加就改规格 §2 并说清它为什么仍是连接器形状而不是数据形状。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import metadata
import importlib
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

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


class UnknownSetupField(ValueError):
    """首次配置那一格声明得不合法——**这一层的牙齿就是这一条**。"""


#: 键名里带这些片段的一律视为「这是密钥」。**按名字拦，不按值拦**——值可能是一段
#: base64，正则扫不出来；「有人把 token 填进表单」这一种只有按名字拦得住。
#: 真源在这里而不是 ``sources_config``：**声明层**（:class:`SetupField`）在 import 时
#: 就要用它拦一道，两边共用一份才不会各自长出一套。
SECRET_LIKE_KEYS: frozenset[str] = frozenset({
    "password", "passwd", "secret", "token", "api_key", "apikey",
    "credential", "cookie", "session_key", "private_key", "access_key",
})


def is_secret_like(key: str) -> bool:
    # 分隔符归一：`API-KEY`、`api key`、`ApiKey` 与 `api_key` 是同一个意思，
    # 不归一的话拦的是拼写而不是**类别**——那就等于给了绕过它的正门。
    normalized = key.strip().lower().replace("-", "_").replace(" ", "_")

    return any(marker in normalized for marker in SECRET_LIKE_KEYS)


#: 首次配置字段的控件形状闭集。**没有 `password`**：密钥不属于这一层，
#: 硬编一个"带掩码的输入框"等于在产品面上开一个把 token 存进 `sources.yaml` 的正门。
SETUP_FIELD_KINDS: tuple[str, ...] = ("text", "path", "url")


@dataclass(frozen=True)
class SetupField:
    """首次配置时用户要填的**一格非密钥**参数，由这条信源自己声明。

    形状照 `capabilities` 那一套：**声明式、缺位注册即报错**。理由也一样——
    面上不该硬编「课表要路径、教务要地址」这张表，那张表一变就得改 `apps/desktop/**`，
    而 spec §8 有一条验收就是「加一条新信源不需要改 `apps/desktop/**`」。

    **`label` 是英文缺省值，不是文案**（裁定 45.4：slug 不上屏）。界面先查语言目录里的
    `settings.sources.fields.<key>`，查不到才回这一格——所以一个第三方包**零改动**就能
    出现在界面上，只是没翻译；我们自己那四条四语言齐全。
    """

    key: str
    label: str
    #: 控件形状，见 :data:`SETUP_FIELD_KINDS`。
    kind: str = "text"
    required: bool = False
    help: str = ""

    def __post_init__(self) -> None:
        if not self.key or not self.key.strip():
            raise UnknownSetupField("key 不能是空串")
        if self.kind not in SETUP_FIELD_KINDS:
            raise UnknownSetupField(
                f"{self.key} 的 kind={self.kind!r} 不在闭集里：{', '.join(SETUP_FIELD_KINDS)}"
            )
        # 唯一的牙齿：这一层**声明不出一个密钥来**。密钥归 `.env`
        # （`Docs/README.md` §0 那条纪律）。挡在**写声明的时候**，所以它压根
        # 不可能被声明出来——比运行时再拦一道强，因为运行时那一道只挡得住手填。
        if is_secret_like(self.key):
            raise UnknownSetupField(
                f"{self.key} 看着像密钥，声明层不收：这一层只放路径 / 地址 / 账号名。"
                "密钥写进 .env。这一格是给人看的表单，不是给机器的密钥库。"
            )


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
    #: 首次配置要用户填的那几格，**由这一条自己声明**（见 :class:`SetupField`）。
    #: 默认空元组**不是**"可以随便省略"——空是一个合法读数，意思是"首次配置不需要用户
    #: 填任何非密钥的东西"（微信要的是扫码与密钥，两样都不在这一层，所以它就该是空的）。
    setup_fields: tuple[SetupField, ...] = ()

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
        seen: set[str] = set()
        for setup in self.setup_fields:
            if setup.key in seen:
                raise UnknownSetupField(f"{self.source_id} 声明了两个同名的首次配置字段：{setup.key}")
            seen.add(setup.key)


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
    ensure_discovered()

    if source_id not in _SPECS:
        raise UnknownSource(source_id)
    del _SPECS[source_id]


def spec(source_id: str) -> SourceSpec:
    ensure_discovered()

    try:
        return _SPECS[source_id]
    except KeyError:
        raise UnknownSource(source_id) from None


def source_ids() -> tuple[str, ...]:
    ensure_discovered()

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
    grouped: dict[str, tuple[str, ...]] = {}

    for category in SOURCE_CATEGORIES:
        members = [sid for sid in source_ids() if _SPECS[sid].category == category]

        grouped[category] = tuple(sorted(members))

    return grouped


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
    ensure_discovered()

    return tuple(sorted(sid for sid, item in _SPECS.items() if item.collecting))


# ---------------------------------------------------------------------------
# 发现（spec-layer §3.1「乙」）
#
# 加一条信源＝加一个目录（内置）或装一个包（外部），**Plobi 核心一行不改**。形状照仓内
# 两处先例抄，不发明新的：``tools/registry.py``（认模块、不认文件名；模块顶层调
# ``register()`` 即被认领）与 ``plobi_cli/plugins.py``（用户自己装的走同一套发现）。
#
# **三条施工约束（照抄进来的是形状，不是这三句的理由）**：
#
# 1. :func:`ensure_discovered` 幂等，且**每个读口都先调它**。这不是洁癖：``_SPECS``
#    是空的 ⇒ :func:`source_ids` 回空元组 ⇒ sweep「成功」地跑完、零条进账、
#    ``/api/collect/sources` 回一个空名单。**那是最危险的静默失败**，形状和
#    ``no_builder`` 当年被藏起来的那次一模一样。
# 2. 发现失败**出声**（:class:`CollectorDiscoveryError`），不许吞成"这条不存在"。
# 3. ``builder`` 仍是唯一构造入口，``sweep.py`` 继续不按名字分支——解耦动的是**注册**，
#    不是调度。
# ---------------------------------------------------------------------------

#: 外部包声明自己的那组 entry-point（``[project.entry-points."plobi.collectors"]``）。
#: 一条信源 = 包里那个 ``SPEC`` 对象。
EXTERNAL_ENTRY_POINT_GROUP = "plobi.collectors"

#: 内置采集器的约定：一个包一个信源，包顶层导出 ``SPEC``。**认符号、不认文件名**
#: ——所以重命名模块不会悄悄把一条信源从注册表里弄掉（那正是 tools 那次事故的形状）。
BUILTIN_SPEC_ATTRIBUTE = "SPEC"


class CollectorDiscoveryError(RuntimeError):
    """某个采集器包没能被载入。**出声，不吞**。

    吞掉的形状：某条信源因为一个 import 错误消失了，而 :func:`source_ids` 少报一个 id
    ——用户看到的是"今天没东西进来"，永远问不到"某个包没载入"。所以这里把包名与原始
    异常一起带上来，让这条失败**在读数里排第一**而不是消失在集合里。
    """

    def __init__(self, failures: list[tuple[str, str]]) -> None:
        detail = "; ".join(f"{module}: {reason}" for module, reason in failures)
        super().__init__(f"{len(failures)} 个采集器包没能载入：{detail}")
        self.failures = dict(failures)


_discovered = False


def _discover_builtin() -> list[tuple[str, str]]:
    """导入每个内置采集器包，让它顶层的 ``SPEC`` 把自己登记进来。"""
    failures: list[tuple[str, str]] = []
    package = __name__.rsplit(".", 1)[0]
    package_dir = Path(__file__).resolve().parent

    for path in sorted(package_dir.glob("*/__init__.py")):
        module_name = f"{package}.{path.parent.name}"

        try:
            module = importlib.import_module(module_name)
        except Exception as exc:  # noqa: BLE001 - 见 CollectorDiscoveryError 的说明
            failures.append((module_name, f"{type(exc).__name__}: {exc}"))

            continue

        if not hasattr(module, BUILTIN_SPEC_ATTRIBUTE):
            # 不是采集器包（公共层的一个子包）。**不报错**：发现认的是"有没有 SPEC"，
            # 不是"目录长得像不像"。
            continue

    return failures


def _discover_external() -> list[tuple[str, str]]:
    """载入第三方装的信源包（entry-point 组）。

    `importlib.metadata` 在没装任何东西时给空集合，那不是错误——这是**零外部信源**
    这一配置的正常读数，不是"发现失败了"。
    """
    failures: list[tuple[str, str]] = []

    try:
        entry_points = metadata.entry_points(group=EXTERNAL_ENTRY_POINT_GROUP)
    except Exception as exc:  # noqa: BLE001 - 见 CollectorDiscoveryError 的说明
        return [("importlib.metadata", f"{type(exc).__name__}: {exc}")]

    for entry_point in entry_points:
        try:
            entry_point.load()
        except Exception as exc:  # noqa: BLE001 - 见 CollectorDiscoveryError 的说明
            failures.append((entry_point.name, f"{type(exc).__name__}: {exc}"))

    return failures


def ensure_discovered() -> None:
    """把内置与外部的采集器都载入注册表。**幂等**，且每个读口都会先调它。

    失败的包不静默：载入失败抛 :class:`CollectorDiscoveryError`（一次全抛，不是第一条），
    因为"三条包没载入"与"一条包没载入"是两种不同的排障，而抛第一条会让剩下几条永远
    看不见。**已经登记好的不受影响**——发现失败抛的是异常，不是清空 `_SPECS`。
    """
    global _discovered

    if _discovered:
        return

    # 先置位再干活：万一某个包在导入时又回头读这张表（教务那条就会，因为它那层要读这张
    # 表派生的词表），递归进来不会把同一个包导两次——`register()` 对重复登记本来就会抛。
    _discovered = True

    failures = _discover_builtin() + _discover_external()

    if failures:
        raise CollectorDiscoveryError(failures)


