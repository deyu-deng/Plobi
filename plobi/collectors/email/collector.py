r"""邮箱采集器 —— IMAP 只读拉最近若干封，**落点只有信息池**（裁定 193）。

三条不是洁癖的规矩：

1. **只读，不动用户那只邮箱**。取正文用 `BODY.PEEK[]` 而不是 `BODY[]`：后者会把邮件标成已读，
   而"被 Plobi 采过"绝不该改变用户在客户端里看到的未读数。同理不 `STORE`、不 `EXPUNGE`、不删。
2. **一轮有上限**（:data:`MAX_PER_ROUND`，取最近的那些）。不做全量回捞的理由与教务那家一样：
   一次拖走整箱既慢，也把"这一条到底通不通"这件事埋进几万行的解析里。回捞历史是另一刀。
3. **凭据只从 :func:`plobi.collectors.sources_config.secret_value` 那一只读口拿**，
   而且**永不进**异常信息、日志、命令行、产物。认证失败那句只说"没认过"，不复述服务器给的原文
   ——很多实现会把用户名连同失败原因一起打回来，那一行里可能有比用户名更多的东西。

时间：`occurred_at` = **这封信自己的时间**（`Date` 头），按裁定 189 只交给池那唯一的归一点，
本文件不做第二套归一（`email.message` 给的带时区时刻先折到本机时区，再写成库认的朴素 ISO）。

`transport` 是**必填位**（判据 7：默认回退到真网络调用就是那颗雷的形状）——测试注一只假信箱，
生产由 :meth:`EmailCollector.build` 显式接 :class:`ImapTransport`。
"""

from __future__ import annotations

import email.utils
import imaplib
import logging
import re
from email import message_from_bytes
from email.header import decode_header, make_header
from email.message import Message
from typing import Any, Optional

from ..base import Occurrence
from .providers import EmailProvider

logger = logging.getLogger(__name__)

#: 一封信进池时的 `kind`（分类那一维是"首次配置要你做什么"，数据形状归这里 —— §2.1）。
KIND = "mail"
#: 一轮最多取几封。到顶就在读数里说一句"还有更多没取"，不许静默截断（那条「成功但空货」
#: 的教训反过来同样成立：截了不说，下一次就以为箱子里只有这么多）。
MAX_PER_ROUND = 200
#: 那一扇信箱。这一版只读收件箱：`INBOX` 是每一家都有的那一扇，而"订阅哪些文件夹"
#: 需要 IMAP LIST 的一层形状，不在这一刀里顺手做。
FOLDER = "INBOX"


class EmailUnavailable(RuntimeError):
    """这一条这轮跑不成，但**说得出是哪一种**跑不成。

    `state` 是给 `sources_api.status_of` 那张词表的读数（`auth_failed` /
    `network_unreachable` / `collect_failed`），`detail` 是给人排障的那一句。两者都不含凭据。
    """

    def __init__(self, state: str, detail: str) -> None:
        super().__init__(detail)
        self.state = state
        self.detail = detail


class ImapTransport:
    """这一族唯一真正碰 `imaplib` 的那只手（其余都走注入的假信箱）。"""

    def __init__(self, host: str, port: int, *, timeout: float = 20.0) -> None:
        self._host = host
        self._port = port
        self._timeout = timeout
        self._box: Optional[imaplib.IMAP4_SSL] = None

    def connect(self, address: str, secret: str) -> None:
        try:
            box = imaplib.IMAP4_SSL(self._host, self._port, timeout=self._timeout)
        except (OSError, imaplib.IMAP4.error) as exc:
            raise EmailUnavailable("network_unreachable",
                                   f"连不上 {self._host}:{self._port}（{type(exc).__name__}）") from None
        try:
            box.login(address, secret)
        except imaplib.IMAP4.error as exc:
            self._close(box)
            # 只带异常**类名**，不带 `exc` 的文本：那一串是服务器原话，形状不可控。
            raise EmailUnavailable("auth_failed", f"{self._host} 拒绝了这组账号/授权码"
                                   f"（{type(exc).__name__}）") from None

        self._box = box

    def select(self, folder: str) -> int:
        box = self._require()
        kind, data = box.select(f'"{folder}"', readonly=True)

        if kind != "OK":
            raise EmailUnavailable("collect_failed", f"打不开那一扇信箱：{folder}")
        try:
            return int(data[0]) if data and data[0] else 0
        except (TypeError, ValueError):
            return 0

    def uids(self) -> list[str]:
        box = self._require()
        kind, data = box.uid("SEARCH", None, "ALL")

        if kind != "OK":
            raise EmailUnavailable("collect_failed", "列不出这箱邮件的 UID")

        return [str(one.decode() if isinstance(one, bytes) else one)
                for one in (data[0].split() if data and data[0] else [])]

    def fetch(self, uid: str) -> bytes:
        box = self._require()
        kind, data = box.uid("FETCH", uid, "BODY.PEEK[]")

        if kind != "OK" or not data:
            return b""
        for item in data:
            if isinstance(item, tuple) and len(item) > 1:
                return bytes(item[1])
        return b""

    def close(self) -> None:
        if self._box is not None:
            self._close(self._box)
            self._box = None

    @staticmethod
    def _close(box: imaplib.IMAP4_SSL) -> None:
        # 两步都要走：`close()` 收那一扇（readonly 那一边服务器可能回 NO，不算失败），
        # `logout()` 才真的把连接放掉。少第二步就是每轮攒一条半死的 TLS 连接。
        for method in ("close", "logout"):
            try:
                getattr(box, method)()
            except Exception:  # noqa: BLE001 - 收尾失败不许盖住真正的读数
                logger.debug("imap %s failed", method)

    def _require(self) -> imaplib.IMAP4_SSL:
        if self._box is None:
            raise EmailUnavailable("collect_failed", "还没连上就没法读这一箱")
        return self._box


class EmailCollector:
    """一家邮箱 = 一条信源 = 这一只采集器的一个实例。"""

    def __init__(self, *, source_id: str, provider: EmailProvider, transport: Any,
                 address: str, secret: str, folder: str = FOLDER,
                 limit: int = MAX_PER_ROUND) -> None:
        self.source_id = source_id
        self.provider = provider
        self.transport = transport
        self.address = address
        self._secret = secret
        self.folder = folder
        self.limit = limit

    @classmethod
    def build(cls, provider: EmailProvider, *, config: Any = None,
              secret_reader: Any = None) -> "EmailCollector":
        """生产那一条接线：读配置层那两格 + 接真的 IMAP。

        `config` / `secret_reader` 是**注入位**（测试不碰盘也不碰环境），默认走那两只唯一的读口：
        `sources_config.setup_params` 给非密钥那几格，`sources_config.secret_value` 给授权码。
        """
        from plobi.collectors import sources_config

        params = (config if config is not None else sources_config.setup_params(provider.source_id))
        read_secret = secret_reader if secret_reader is not None else (
            lambda key: sources_config.secret_value(provider.source_id, key))
        address = str(params.get("address") or "").strip()
        secret = str(read_secret("password") or "").strip()
        host = (provider.host or str(params.get("imap_host") or "")).strip()

        if not address or not secret:
            raise EmailUnavailable("auth_failed", f"{provider.source_id} 还没填齐邮箱地址与授权码")
        if not host:
            raise EmailUnavailable("collect_failed", f"{provider.source_id} 没有 IMAP 服务器可连")

        return cls(source_id=provider.source_id, provider=provider,
                   transport=ImapTransport(host, provider.port), address=address, secret=secret)

    def health(self) -> dict:
        """能连、能登、能数列 —— 不取正文，所以这一句比 `pull()` 便宜。"""
        try:
            self.transport.connect(self.address, self._secret)
            count = self.transport.select(self.folder)
            self.transport.close()
        except EmailUnavailable as exc:
            return {"ok": False, "detail": exc.detail, "state": exc.state}

        return {"ok": True, "detail": f"{self.folder} 里 {count} 封", "state": "ready"}

    def pull(self) -> list[Occurrence]:
        """最近 :data:`MAX_PER_ROUND` 封 → `Occurrence` 行。**纯读，一个字都不写库**。

        落池由公共层那一条通用路做（`sweep._collect_one` 里"只有 `pull()`"那一支），
        这里不新起第二条落库路（裁定 193）。
        """
        self.transport.connect(self.address, self._secret)
        try:
            self.transport.select(self.folder)
            uids = self.transport.uids()
            picked = uids[-self.limit:] if self.limit else uids
            truncated = len(uids) - len(picked)

            rows: list[Occurrence] = []
            for uid in picked:
                raw = self.transport.fetch(uid)
                if not raw:
                    continue
                row = self._occurrence(uid, message_from_bytes(raw))
                if row is not None:
                    rows.append(row)

            if truncated > 0:
                logger.info("email %s: 这一轮只取了最近 %d 封，还有 %d 封没取",
                            self.source_id, len(picked), truncated)
            return rows
        finally:
            self.transport.close()

    def _occurrence(self, uid: str, message: Message) -> Optional[Occurrence]:
        """一封邮件 → 一行。**幂等键优先 Message-ID**：UID 只在服务器那一箱里稳定，
        同一封邮件转发/另存之后 UID 会变，而 Message-ID 跟着信走。取不到才退 UID（带账号，
        同一家两个号不会互相盖）。"""
        message_id = _message_id(message)
        subject = _decode(message.get("Subject"))
        sender = _decode(message.get("From"))

        return Occurrence(
            source=self.source_id,
            external_id=message_id or f"{self.address}:{uid}",
            title=subject or sender,
            start_at=_when(message),
            kind=KIND,
            evidence={
                "text": _body_text(message),
                "from": sender,
                "to": _decode(message.get("To")),
                "subject": subject,
                "account": self.address,
                "folder": self.folder,
                "uid": str(uid),
                "message_id": message_id,
                # 这一格是**这行自己的出处**：以后有人问"这条是谁的哪个文件夹里的哪一封"，
                # 不必再猜一次键名怎么拼（P6：出处跟着数据走，不留一张外部对照表）。
                "origin": f"imap://{self.provider.host or '<user-supplied>'}/{self.folder}/{uid}",
            },
        )


_ID_STRIP = re.compile(r"[<>]")


def _message_id(message: Message) -> str:
    raw = str(message.get("Message-ID") or "").strip()
    if not raw or "@" not in raw:
        # 裸一个 `12345` 那种残值会跨域撞键（不同站点的序号会重），当没有更诚实。
        return ""
    return _ID_STRIP.sub("", raw)[:200]


def _decode(raw: Any) -> str:
    """RFC 2047 那一串（`=?utf-8?B?...?=`）折成人能读的一行。解不开就原样给。"""
    if raw is None:
        return ""
    try:
        return str(make_header(decode_header(str(raw)))).strip()
    except (UnicodeDecodeError, ValueError):
        return str(raw).strip()


def _when(message: Message) -> str:
    """`Date` 头 → 本机时区的朴素 ISO。**只有这一只手写格式化**，归一仍交池那一点（裁 189）。"""
    try:
        parsed = email.utils.parsedate_to_datetime(str(message.get("Date") or ""))
    except (TypeError, ValueError):
        return ""
    if not parsed:
        return ""
    if parsed.tzinfo is None:
        return parsed.strftime("%Y-%m-%d %H:%M:%S")
    # `astimezone()` 不带参数 = 折到**本机**时区：库里的时间一直是"本机朴素时刻"这个口径。
    return parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _body_text(message: Message) -> str:
    """正文只取 text/plain 那一段（HTML 与附件都不进 —— `supports_media` 那一格因此留 `None`）。"""
    part = message
    if message.is_multipart():
        for candidate in message.walk():
            if candidate.get_content_maintype() == "text" and \
                    (candidate.get_content_subtype() or "") == "plain" and \
                    candidate.get("Content-Disposition") not in ("attachment", "inline"):
                part = candidate
                break
        else:
            return ""

    try:
        payload = part.get_payload(decode=True)
    except (TypeError, ValueError):
        return ""
    if not payload:
        return ""
    charset = part.get_content_charset() or "utf-8"

    try:
        return payload.decode(charset, errors="replace").strip()
    except LookupError:
        return payload.decode("utf-8", errors="replace").strip()
