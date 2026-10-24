r"""邮箱这一族信源的**声明**（`specs/source-layer.md` §3.1 那条"加一个平台＝加一个包"）。

这一包与别家不同的一件事：它注册**多条**信源，而不是包顶层那一个 `SPEC`。理由在
:mod:`.providers` 的模块头里（一家 = 一条信源，五家共用同一只采集器）。发现那一层因此
只看到"这个包被导入了"——真正的验收是 :func:`source_ids` 里必须出现这张表上每一个 id，
那条由 ``tests/plobi/test_email_collector.py`` 钉住（**静默少注册一家**正是注册表头部
那条注记点名的失败形状：`_SPECS` 少一个 id ⇒ 那一行在屏上根本没有，而没人报错）。

分类是 `email` —— 第七个值，用户 10-24 拍的（「我们要添加一个邮箱信源分类」），
资格写在 `Docs/specs/source-layer.md` §2.2 那一行与 §2.3 第 4 条：轴仍是**首次配置要用户做什么**
（一个邮箱地址 + 一份授权码），不是扫码、不是路径、不是一个 URL。数据形状那一维是 `kind="mail"`。
"""

from __future__ import annotations

from plobi.collectors.registry import SetupField, SourceSpec, register

from .providers import PROVIDERS, EmailProvider

_ADDRESS = SetupField(key="address", label="Email address", kind="text", required=True,
                      help="The mailbox you sign in with, e.g. someone@qq.com")
#: 密钥那一档：值只进 `.env`（`PLOBI_SOURCE_<id>_PASSWORD`）、屏上打星、读数里只有"设过没有"。
#: 这一族的注记：QQ / 163 / Gmail 要的是**授权码或应用专用密码**，不是网页登录密码——
#: 那句话由 `fields.password` 那四语来说，不写在这一格的 `help` 里（help 是英文缺省值）。
_PASSWORD = SetupField(key="password", label="Password or app token", kind="secret", required=True)
#: 只有 `email_imap` 那一行有这一格：不在表里的那一家（企业邮箱、自建 IMAP）填服务器就能接。
_HOST = SetupField(key="imap_host", label="IMAP server", kind="text", required=True,
                   help="e.g. imap.example.com — always TLS on port 993")


def _spec(provider: EmailProvider) -> SourceSpec:
    """一家 provider → 一条信源的声明。能力位写的是**我们这侧的契约**，不是"协议理论上能给什么"。"""
    fields = [_ADDRESS, _PASSWORD]
    if provider.asks_for_host:
        fields.insert(0, _HOST)

    return SourceSpec(
        provider.source_id,
        capabilities={
            # 我们这侧**不回捞历史**：`pull()` 只取最近 `MAX_PER_ROUND` 封（协议能给，代码没做，
            # 所以这一格不能填 True——填了就是"以后加回捞不用改声明"的那种静默）。
            "history_backfill": False,
            # `pull(since=…)` 收了不用：每轮取最近那一截，幂等交给 Message-ID（与 zju 同写法）。
            "incremental_since": False,
            # 正文只解析 text/plain；HTML/附件**没实测**。留 None 是让消费方在"未实测"上报错，
            # 而不是拿一个 False 冒充测过（判据 5）。
            "supports_media": None,
            # 要用户自己填：邮箱地址 + 授权码（Gmail 那类还要先开应用专用密码）。
            "requires_manual_setup": True,
            # 邮件是追加型的：一轮里没出现 ≠ 被取消了。而且一轮只取最近那一截，
            # 按"本轮未出现"去关闭会把没取到的那些一起判死——那是纯粹的错。
            "authoritative_replace": False,
        },
        category="email",
        display_name=provider.display_name,
        site=provider.site,
        collecting=True,
        builder=_builder(provider),
        setup_fields=tuple(fields),
    )


def _builder(provider: EmailProvider):
    """执行方入口。**延迟 import**：`collector` 那一头要读注册表派生的词表，而注册表的发现
    正是在导入本包——顶层就 import 它会成一个环（与 `zju/_build` 同一处理）。"""

    def build() -> "EmailCollector":  # noqa: F821 - 延迟 import 的那一位，签名里只能写名字
        from .collector import EmailCollector

        return EmailCollector.build(provider)

    return build


SPECS: tuple[SourceSpec, ...] = tuple(_spec(provider) for provider in PROVIDERS)

for _spec_item in SPECS:
    register(_spec_item, replace=True)

__all__ = ["SPECS", "PROVIDERS"]
