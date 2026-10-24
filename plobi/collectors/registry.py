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
import logging
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

logger = logging.getLogger(__name__)

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
#:
#: **第七个值 `email` 是用户 10-24 拍的**（「我们要添加一个邮箱信源分类。可以支持用户添加
#: 各种邮箱」）。加它的那句话按 §2.3 第 4 条写在该节里：它的轴仍是**首次配置要用户做什么**——
#: 「一个邮箱地址 + 一份授权码/密码」，既不是扫码/授权（消息、社交媒体）、不是指一个路径
#: （本地）、也不是给一个 URL（网站）。数据形状那一维还是 `kind="mail"`，与分类无关（裁 151 平权）。
SOURCE_CATEGORIES: tuple[str, ...] = ("message", "social", "website", "local", "system", "email", "custom")

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


#: 首次配置字段的控件形状闭集。
#:
#: **`secret` 是 10-22 新开的一格**（用户：「密码什么的就不要明文显示。用星号」）。
#: 它不是"带掩码的输入框"那么轻的一档，三条硬规矩一起才算数，缺一条就退回成
#: "把 token 存进 `sources.yaml`"那扇原本被堵死的正门：
#: ① 值**只走 `.env`**（`sources_config.set_secret` → `plobi_cli.config.save_env_value_secure`），
#: ② **永不出现在 payload 里**（只回"已设置/没设置"），③ 界面上是写入口，不是读数口（不留"上次填的值"）。
#: `cadence` 那一格同理不是换个控件：它存进 `sources.yaml` 的**仍是那一句 cron**，
#: 改的只是"别让普通人面对裸 cron 文本框"（运行时一个字没动）。
SETUP_FIELD_KINDS: tuple[str, ...] = ("text", "path", "url", "secret", "cadence")


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
        # 牙齿还是那一颗，只是从"这一层声明不出密钥"改成"**声明密钥就必须标出来**"：
        # 标了 `secret` 的键走 `.env`、不回显、不写进 `sources.yaml`（见 :data:`SETUP_FIELD_KINDS`
        # 那三条）；没标却长得像密钥的（`password_hint`、`my_token` 这种）照旧在**写声明那一刻**炸——
        # 那才是这颗牙 originally 拦的东西：一个自称"非密钥"、运行时却被当凭据用的字段。
        if is_secret_like(self.key) and self.kind != "secret":
            raise UnknownSetupField(
                f"{self.key} 看着像密钥，就必须声明成 kind='secret'（值只进 .env、永不出现在读数里）。"
                "把它当普通文本填进 `sources.yaml` 是另一条违规：那一档是给人看的配置，不是密钥库。"
            )
        if self.kind == "secret" and not is_secret_like(self.key):
            raise UnknownSetupField(
                f"{self.key} 声明成了密钥那一档，但这个名字不像密钥——掩码框配一个非密钥字段，"
                "下一个读的人就分不清哪一格是真的不能回显。"
            )


class UnknownCadence(ValueError):
    """这一句频率写得不合法。**拦在声明那一刻**，不等每天那一轮静默不打。"""


def validate_cadence(text: str) -> str:
    """把那一句频率交给**唯一的**那只手判一次（``cron/jobs.py::parse_schedule``）。

    这里不再写一份 cron 解析：同一件算术在仓里长两套，早晚不一致（裁定 94）。写法与那一条
    全局作业**完全同一句法**（``0 3 * * *`` / ``every 6h``），所以设置面上那一格不需要第二种语法，
    排障时也不需要第二种读法。
    """
    from cron import jobs as cron_jobs

    try:
        cron_jobs.parse_schedule(text)
    except (ValueError, RuntimeError) as exc:
        raise UnknownCadence(f"{text!r} 不是一句能用的频率：{exc}") from None

    return text.strip()


#: 每一条信源都该让用户改得动的那一格。**真源在这里，而且不由各个包自己引**：
#: :meth:`SourceSpec.__post_init__` 给**每一条**信源自动补上它（含第三方包，零改动）。
#: 为什么不许包自己声明——那样"以后加一条信源"就多一步"记得写频率代码"，而忘记的那一条
#: 在设置面上就少一格、在调度上就永远每轮都打，两种都是没人报的静默缺口（裁定 194 的
#: 通用性同一把尺：可插拔的那一层不该要求新来的人复刻旧来的人的样板）。
CADENCE_FIELD = SetupField(
    key="cadence",
    label="Collection cadence",
    kind="cadence",
    help="e.g. 0 3 * * * (daily at 03:00) or every 6h. Empty keeps it per round.",
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
    #: 这条信源**多久该被打一次**（裁定 191 二那一格）。写法就是 `cron/jobs.py` 那一句：
    #: `"0 3 * * *"`（每天 03:00）／`"every 2h"`／`""`。
    #:
    #: 空串是一个**读数**而不是省略：「这条没在代码里声明频率」⇒ 生效值跟着全局
    #: `plobi-source-collect` 那一轮，每轮都打——也就是本刀之前的现行行为。所以加这一格
    #: **不改动任何一条现存信源的运行时**，改的是"现在终于有地方可以设它了"。
    #:
    #: 为什么是**声明**而不是调度器：`cron` 那一头还是一条作业，跑到每一家之前先问
    #: :mod:`plobi.collectors.cadence` 一句「到点了没」。这里不新起一个闹钟。
    #:
    #: 为什么不是 `min_minutes_between_pulls` 那一格：那是**节流**（两次真打之间的上限，
    #: 防把站方打爆），住在各家采集器自己那侧，本刀一个字不动它。两把闸叠着生效，
    #: 谁也不替代谁——"我设成每天三点"回答不了"那家能扛多久一次"。
    cadence: str = field(default="", kw_only=True)
    #: 这一条**在哪个网站**（scheme + host 就够，不要路径）。10-22 为「添加网站之后自动同步
    #: 这个网站的图标」新开的一格：图标不是功能，所以它**不进**能力位、也不让前端硬编一张表
    #: （那张表一旦漏了一条，屏上就是一条"没记到名字的信源"配一个首字母）；声明了就有 favicon，
    #: 没声明（本地文件、消息客户端这类）就退前端那张品牌表，再退首字母。
    #: 空串是一个**读数**：这一条没有"一个网站"可去。
    site: str = field(default="", kw_only=True)
    #: 这条信源**自己叫什么**（用户 10-24：「网站就显示网站名就行了，不要叫什么"教务"」）。
    #:
    #: 它是**专名**，不是句子：不进四语言目录、不当文案翻译（与 :attr:`site` 同一档——
    #: 声明的是事实，话由界面给）。第三方包填了这一格，屏上就有名字；没填，
    #: :mod:`plobi.collectors.icons` 会拿它那个站**自报的名字**（首页 `<title>` /
    #: `og:site_name`，与图标共用那一次抓取）兜住。
    #:
    #: 为什么仍然允许界面盖过去：`apps/desktop/src/i18n/*.ts` 那张 `agenda.sources` 表
    #: 是我们自己那几条的**本地化**（微信／钉钉在四种语言里各有写法），与
    #: `SetupField.label` 那一格同一套路——声明给缺省值，语言目录优先。
    #: 空串是一个读数：这一条没给自己起名字。
    display_name: str = field(default="", kw_only=True)
    collecting: bool = True
    #: 执行方**构造这一条采集器**的唯一入口（`plobi/collectors/sweep.py` 只认这一格，不按名字分支）。
    #: `None` 是一个读数而不是省略：注册表说这条在采、却没有任何一方构造得出它 ——
    #: 2026-10-15 教务那一刀就断在这里（`collecting_sources()` 全仓零调用方）。
    builder: Optional[Callable[[], Any]] = None
    #: 这条信源**在本机到底成不成立**的那只只读口（用户 10-24：「钉钉你适配一下。我们组织
    #: 没有开放 CLI。要用别的方式」）。返回 `{"ready": bool, "code": str, "detail": str}`。
    #:
    #: 为什么要有这一格而不是在界面上按信源名字判"钉钉特殊"：今天 `builder` 为空 + 开关点着
    #: 被判成 `error/internal`（屏上一颗红点，意思是"我们的产品坏了"），而钉钉真正的话是
    #: **"这台机器给不出它要的东西"**——客户端装了但没登录过 ⇒ 本地库不存在。那是**未连接**，
    #: 不是故障。把这句话变成读数的唯一干净办法，就是让**这条信源自己**回答"你在这台机器上
    #: 成立吗"，公共层照旧一个字都不按名字分支（判据 3 与能力位同一把尺）。
    #:
    #: 与 `Collector.health()` 的分工：`health()` 是**构造得出采集器之后**的连通性；`probe` 是
    #: **构造不出采集器时**那一段的前置条件。两者不重叠，也不互相兜底。
    #: 不许抛：探针自己读不到盘/网络时该回 `ready=False` 加一句为什么，红点留给真的故障。
    probe: Optional[Callable[[], Mapping[str, Any]]] = None
    #: 首次配置要用户填的那几格，**由这一条自己声明**（见 :class:`SetupField`）。
    #: 默认空元组**不是**"可以随便省略"——空是一个合法读数，意思是"首次配置不需要用户
    #: 填任何非密钥的东西"（微信要的是扫码与密钥，两样都不在这一层，所以它就该是空的）。
    setup_fields: tuple[SetupField, ...] = ()
    #: 这一条在**本机有自己的常驻服务**时，那个服务的端口（`None`＝没有要开关的进程）。
    #:
    #: 用户 10-23 对着那一屏说：「**微信那个按钮形同虚设。我打开还是关闭都是显示未连接。
    #: 然后我希望开始和关闭是会真的切断这条信源的采集程序**」。开关若只改一个布尔、
    #: 却把进程留在那儿，它就是个假动作；这一格把"那颗开关背后其实有一个进程"变成**声明**，
    #: 于是 :mod:`plobi.collectors.sidecar` 能照它去拉起/收掉，而公共层仍然不按信源名字分支。
    sidecar_port: Optional[int] = None
    #: 拉起上面那个服务要跑的脚本（**仓库相对路径**，如 ``scripts/chatlog_server.ps1``）。
    #: 空串是一个读数：这一条声明了端口却没有给出怎么拉起它，那么"打开"只能如实报一句
    #: "我不会自己起它"，不许假装开了。
    sidecar_start: str = ""
    #: 这一条**按会话准入**吗（微信那种"一条信源里有很多个群与人"的形状）。
    #:
    #: 面不许按信源名字决定"要不要画那一份名单"（判据 4），所以这一格必须是**声明**：
    #: 声明了，抽屉里就多那一块"每个群聊/私聊采不采"；没声明就一个字都不多。
    #: 名单本身与准入落点早就有了（`/api/collect/talkers` 与 `TalkerStore`），
    #: 缺的只是把它挂到一个看得见的地方——用户 10-23 问的正是这一格。
    conversation_scoped: bool = False

    def __post_init__(self) -> None:
        if not self.source_id or not self.source_id.strip():
            raise ValueError("source_id 不能是空串")
        if not self.category:
            raise ValueError(f"{self.source_id} 没声明分类（category 必填，见 spec-layer §2）")
        if self.sidecar_port is not None and not (0 < int(self.sidecar_port) < 65536):
            raise ValueError(f"{self.source_id} 的 sidecar_port 不是合法端口：{self.sidecar_port}")
        if self.category not in CATEGORY_SLUGS:
            raise UnknownCategory(
                f"{self.source_id} 的分类 {self.category!r} 不在闭集里："
                f"{', '.join(SOURCE_CATEGORIES)}。要加新分类先改 spec-layer §2，"
                "并说清它为什么仍是「首次配置要用户做什么」而不是数据形状"
            )
        if self.cadence.strip():
            # 牙齿长在**声明那一刻**：`"0 3 * *"` 少一字段、`"every"` 少一个时长，都要在这里
            # 炸，而不是等每天那一轮静默不打（用户看到的还是"今天没东西进来"）。
            validate_cadence(self.cadence)
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
        if CADENCE_FIELD.key not in seen:
            # 自动补上去那一条：位置在末尾，**已声明过同名格的包不会被改动**（重复即报错那条
            # 检查在上面先跑）。frozen dataclass 只能这样改回去——改的是这一条自己的元组，
            # 不动那个模块级常量，所以四条信源不会共享到同一份可变状态上。
            object.__setattr__(self, "setup_fields", (*self.setup_fields, CADENCE_FIELD))


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
    """**这一轮该打哪几条** = 生效开关点着的那些条。

    2026-10-24 改过判据，改的只有"读哪一格"：这一函数从前只看 `SourceSpec.collecting`
    （代码里那份**默认值**），于是用户在设置页翻的那颗开关对每天那一轮**什么都不是**——
    关掉照样打、开着照样不动。那一屏的存在理由恰恰是回答「我明明关了怎么还在跑」
    （`sources_config.py` 头部与 `enabled_origin` 那几段注记写的就是这句），而当时
    这一条读口根本没接上它。这不是新拍的口径，是把已有的那句「生效值：用户翻过的优先」
    真的接到调度那一只手（用户 10-23 原话：「我希望开始和关闭是会真的切断这条信源的采集程序」）。

    为什么是改这一函数而不是在 `sweep.py` / 那条 cron 里再问一次开关：**一处判据一处真源**。
    那两个调用方现在一个字都不用动，也长不出第二套"什么算在采"。

    回落链本身仍然只在 `sources_config.effective_enabled` 那一处：这里一次调用都没自己写。
    配置层整个读不到时退回代码默认值并**出声**——那种时候"照旧跑"比"今天全停"更贴近
    用户的预期，但那句话必须留在日志里（静默回落成默认值正是"我明明关了怎么还在采"的形状）。
    """
    ensure_discovered()

    from plobi.collectors import sources_config

    selected: list[str] = []
    unreadable: list[str] = []

    for source_id, item in _SPECS.items():
        try:
            enabled = sources_config.effective_enabled(source_id)[0]
        except sources_config.SourceConfigError as exc:
            enabled = item.collecting
            unreadable.append(str(exc))
        if enabled:
            selected.append(source_id)

    if unreadable:
        logger.warning("registry: 配置层读不到，这一轮按代码默认值挑信源：%s", "; ".join(unreadable[:2]))

    return tuple(sorted(selected))


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

    before = set(_SPECS)

    for entry_point in entry_points:
        try:
            spec = entry_point.load()
        except Exception as exc:  # noqa: BLE001 - 见 CollectorDiscoveryError 的说明
            failures.append((entry_point.name, f"{type(exc).__name__}: {exc}"))

            continue

        source_id = getattr(spec, "source_id", None)
        if source_id:
            _EXTERNAL_SOURCE_IDS.add(str(source_id))
        else:
            # 载进来了但没有 `SPEC.source_id` —— 那它不是一条信源。**出声**：一个自称
            # 是采集器包却没有形状的东西，静默放过等于让用户装了个什么也没发生的东西。
            failures.append((entry_point.name, "这个包装上了，但它没有 SPEC.source_id：不是一条信源"))

    del before

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


#: 外面装的那些包报上来的信源 id。**记下来是因为卸的时候要用**——不记的话，卸完那个包
#: 之后这一行会永远留在注册表里，界面上是一条再也装不回来的僵尸行，而用户会以为
#: "卸了也没用"。
_EXTERNAL_SOURCE_IDS: set[str] = set()


def external_source_ids() -> frozenset[str]:
    """哪些条是**外面**装进来的。

    读数不是推导——判据是"这条自己报的来源是不是 entry-point"，而那是装的那一刻
    才知道的事，事后从名字或路径猜都会猜错。
    """
    ensure_discovered()

    return frozenset(_EXTERNAL_SOURCE_IDS)


def refresh_discovery() -> None:
    """重跑一次发现（装完 / 卸完立刻调）。

    与 :func:`ensure_discovered` 的区别：**先清外部那几条**，再从头发现。所以
    `plobi.collectors.external.refresh()` 报出来的 `removed` 是真的从表里走了。

    内置那几条**不清**——它们不在任何可卸载的包里，清了就没了，而 `import` 也不会
    再跑一遍已缓存的模块。这不是取巧：内置与外部的生命周期本来就不同（裁定：内置
    随代码走，外部随安装走）。
    """
    global _discovered

    for source_id in [sid for sid in _SPECS if sid in _EXTERNAL_SOURCE_IDS]:
        del _SPECS[source_id]

    _EXTERNAL_SOURCE_IDS.clear()
    _discovered = False
    ensure_discovered()


