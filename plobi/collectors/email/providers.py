r"""邮箱这一族的 provider 表 —— 「可以支持用户添加各种邮箱」里那个"各种"落在这一张表上。

形状（用户 2026-10-24 原话：「我们要添加一个邮箱信源分类。可以支持用户添加各种邮箱」）：
**一家 = 一条信源**，各带自己那颗开关、自己那份频率、自己那格授权码（`kind="secret"` ⇒ 只进
`.env`、屏上打星、读数里只有"设过没有"）。这一条与裁定 194「一个信源 = 一个网站」是同一把尺的
两个面：**连接器形状是一台 IMAP 服务器 + 一个邮箱账号**，一家换一个账号就是换一条信源，
而不是在一条信源里挂一张账号名单。

为什么是"一张表注册多条"而不是"每家拆一个包"：五家共用同一只采集器与同一份契约，
逐家拆包要把那 200 行复制五遍（复用优先）。注册表那条真判据是「`registry.py` 里不许出现
任何信源 id」，这一族一个字没破：id 全在这张表里，公共层不认识它们。

为什么**不**做"任意多个任意账号"：那需要注册表从 `sources.yaml` **派生** `source_id`
（今天是代码/包派生），是架构新的一格，不在这一刀里偷偷实现 —— 纸上 §4 第 2 条把它列成待拍。
`email_imap` 那一行是这一版的兜底：不在表里的服务器（企业邮箱、自建 Dovecot）填 host 就能接。

各家 host 是公开常识（`imap.qq.com` / `imap.163.com` / `imap.gmail.com` /
`outlook.office365.com`），**本轮没拿任何真账号实测过连接**——所以 :mod:`.collector` 里那几个
能力位写的是"我们这侧的契约"，`supports_media` 留 `None`（未实测，判据 5 不许拿 False 冒充测过）。
Gmail / Outlook 这类要"应用专用密码 / app token"，不是登录密码：界面上那一格叫「密码或授权码」。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EmailProvider:
    source_id: str
    #: 这条信源**自己叫什么** —— 专名，不是句子（见 `registry.SourceSpec.display_name`）。
    #: 界面语言目录仍然排在它前面（四语各有写法），这一格是"目录里没有也能读出来"的那份缺省。
    display_name: str
    #: 那台 IMAP 服务器。空串是一个**读数**：这一家要用户自己填（`email_imap` 那一行）。
    host: str
    #: 品牌站点 —— 只用来抓一次图标（`collectors/icons.py`）；不是配置项，改它要重新装。
    site: str = ""
    #: 端口。这一版只走 TLS（993），不给人一个"要不要加密"的选项：留那个选项就等于
    #: 允许明文把授权码发出去，而那不是配置、是一颗雷。
    port: int = 993

    @property
    def asks_for_host(self) -> bool:
        """这一家要不要用户自己填服务器。"""
        return not self.host


#: 表里每一家都会变成一条信源（`source_id` 就是它在词表与池里的名字）。
PROVIDERS: tuple[EmailProvider, ...] = (
    EmailProvider("email_qq", "QQ Mail", "imap.qq.com", site="https://mail.qq.com"),
    EmailProvider("email_163", "NetEase Mail (163)", "imap.163.com", site="https://mail.163.com"),
    # Gmail 走的是"应用专用密码"（要两步验证），不是登录密码 —— 那一格的标签已经按这个写。
    EmailProvider("email_gmail", "Gmail", "imap.gmail.com", site="https://mail.google.com"),
    EmailProvider("email_outlook", "Outlook / Microsoft 365", "outlook.office365.com",
                  site="https://outlook.office.com/mail/"),
    # 不在表里的那一家（企业邮箱、自建 IMAP）：填服务器就能接。
    EmailProvider("email_imap", "Custom IMAP mailbox", ""),
)
