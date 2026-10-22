"""本科教务（`zdbk`/`jwglxt`）—— 一条信源＝一个网站（裁定 194）。

这一条**不叫"课表源"**：课表只是这一家给得出的东西之一。同一包里 10-21 实测到并采进来的
还有考试、成绩、主修、实践分（端点与形状都在 :mod:`.collector` 的表里，带证据）。
按裁定 194，也不许为它们再开第二、第三、第四条信源——进池用 `kind` 区分。

:data:`SPEC` 是这一条向注册表自报的形状（`specs/source-layer.md` §3.1）：加一条信源＝加一个
这样的包，`registry.py` 里不出现任何信源 id。
"""

from plobi.collectors.registry import SourceSpec, register


def _build() -> object:
    """执行方入口。``transport`` 是**必填位**，这里显式给生产那一只（裁定 104 二）。

    延迟 import：`collector` 那头要读注册表派生的词表，而注册表的发现正是在导入本包。
    """
    from ..zju.auth import UrllibTransport
    from .collector import ZdbkCollector as _ZdbkCollector

    return _ZdbkCollector(transport=UrllibTransport())


SPEC = SourceSpec(
    "zdbk",
    capabilities={
        # 实测（10-21 两轮探查）：主修 238 条、成绩 25 条、考试 40 条，且
        # `totalCount == items.length`（`pageSize` 是 15 却一次给全）⇒ 一次拿全，含历年学期。
        "history_backfill": True,
        # 我们这侧的契约：`pull(since=…)` 收了不用，每轮全量 + 按学校给的 id 幂等。
        # 这一格说的不是"学校没有增量接口"——那句没测过。
        "incremental_since": False,
        # 实测：三个 JSON 端点的行字段全是文本/数字（`kcb` 是 `<br>` 拼的文本），
        # 没有附件、图片、语音这类字段。
        "supports_media": False,
        # 账密在根 `.env`（走 `auth.read_credentials()` 那一只读路）。`getKaptchaStatus`
        # 与课表那一条的 `captcha_error` 都是**站方行为**，不是这一条的性质：撞上就报
        # 「要人工验证码」并停这一轮（用户 10-21 拍），不许自动识别。
        "requires_manual_setup": True,
        # False，两条理由都是实测出来的：① 课表与考试按 `xnm/xqm`（学年/学期）查，
        # 一轮只代表那一学期那一轮，跨学期"没出现"根本不是取消；② 实践分那页只有 2 行
        # HTML，缺席更是没有信息。⇒ 缺席什么时候算取消，等把校历与学期码那格测清楚再说。
        "authoritative_replace": False,
    },
    # 连接器形状是「一个网址 + 账密」，没有客户端要装 ⇒ 「网站」不是「消息」。
    category="website",
    site="https://zdbk.zju.edu.cn",  # 图标从这一格抓一次（collectors/icons.py）；不是配置项，改它要重新装
    collecting=True,
    builder=_build,
)

register(SPEC, replace=True)

__all__ = ["SPEC"]
