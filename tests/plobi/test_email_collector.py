r"""邮箱这一族（`WP-SOURCE-LASTMILE` 规则 3–6）：一条 provider 表怎么变成五条信源、一封邮件怎么变成一行池数据。

跑法：`bash scripts/run_tests.sh tests/plobi/test_email_collector.py -q`

**零网络、零真账号**：`transport` 是注入位（判据 7 —— 默认回退到真网络调用就是那颗雷的形状），
真的那一只在 :class:`TestTheRealWiring` 里对着一个假 `imaplib` 对象验**它调了哪些命令**。
这一档真正要钉的是四句：
①这一族进注册表的是**五条**信源，不是"邮箱"这一条；
②**采集不许改用户那只邮箱**（`BODY.PEEK[]` + readonly select，一次 `STORE` 都不许有）；
③幂等键跟着信走（Message-ID），退化时才用 `账号:UID`；
④授权码只从 :func:`sources_config.secret_value` 那一只读口进来，且**不进**任何一句异常话。
"""

from __future__ import annotations

import email.utils
import imaplib
from email.utils import formatdate

import pytest

from plobi.collectors import registry, sources_config
from plobi.collectors.email import PROVIDERS
from plobi.collectors.email.collector import (
    FOLDER,
    KIND,
    MAX_PER_ROUND,
    EmailCollector,
    EmailUnavailable,
    ImapTransport,
)

SECRET = "hunter2-not-a-real-token"


def raw(subject="=?utf-8?B?5rK755CG?=", *, message_id="<m1@mail.test>", date_epoch=1_762_000_000,
        body="请查看货件名称", multipart=False) -> bytes:
    head = [
        f"Message-ID: {message_id}".encode(),
        b"From: =?utf-8?B?5LiL5YmN?= <xia@mail.test>",
        b"To: me@qq.com",
        f"Subject: {subject}".encode(),
        f"Date: {formatdate(date_epoch, usegmt=True)}".encode(),
    ]
    if not multipart:
        return b"\r\n".join(head + [
            b"Content-Type: text/plain; charset=utf-8",
            b"Content-Transfer-Encoding: base64",
            b"",
            b"6K+35p+l55yL6LSn5Lu25ZCN56ew",
            b"",
        ])

    return b"\r\n".join(head + [
        b'Content-Type: multipart/mixed; boundary="B"',
        b"",
        b"--B",
        b"Content-Type: text/plain; charset=utf-8",
        b"Content-Transfer-Encoding: base64",
        b"",
        b"6K+35p+l55yL6LSn5Lu25ZCN56ew",
        b"--B",
        b'Content-Type: application/pdf; name="invoice.pdf"',
        b"Content-Disposition: attachment; filename=\"invoice.pdf\"",
        b"",
        b"JVBERi0xLjQ=",
        b"--B--",
        b"",
    ])


class FakeTransport:
    """一只假信箱：记调用、按脚本回数据，不碰 imaplib。"""

    def __init__(self, *, uids=(), messages=None, login_error=None, connect_error=None) -> None:
        self.calls: list[str] = []
        self._uids = list(uids)
        self._messages = messages or {}
        self._login_error = login_error
        self._connect_error = connect_error

    def connect(self, address, secret):
        self.calls.append(f"connect:{address}")
        if self._connect_error:
            raise self._connect_error
        if self._login_error:
            raise self._login_error

    def select(self, folder):
        self.calls.append(f"select:{folder}")
        return len(self._uids)

    def uids(self):
        self.calls.append("uids")
        return list(self._uids)

    def fetch(self, uid):
        self.calls.append(f"fetch:{uid}")
        return self._messages.get(str(uid), b"")

    def close(self):
        self.calls.append("close")


def collector(*, source_id="email_qq", transport=None, uids=(), messages=None,
              limit=MAX_PER_ROUND, **kw) -> EmailCollector:
    box = transport or FakeTransport(uids=uids, messages=messages, **{
        k: kw[k] for k in ("login_error", "connect_error") if k in kw})

    return EmailCollector(source_id=source_id, provider=PROVIDERS[0], transport=box,
                          address="me@qq.com", secret=SECRET, limit=limit)


class TestOneProviderIsOneSource:
    """五条信源，不是一条「邮箱」——一家一个开关、一家一份频率、一家一格授权码。"""

    def test_every_provider_landed_in_the_registry(self) -> None:
        """这一包注册的是**多条**，包顶层因此没有单个 `SPEC`；发现那一层"导入过"不等于"注册上了"。

        静默少一家就是屏上少一行而没人报错——正是注册表头部点名的那种失败形状。
        """
        registry.ensure_discovered()
        ids = set(registry.source_ids())

        assert {p.source_id for p in PROVIDERS} <= ids

    def test_each_one_is_in_the_email_category(self) -> None:
        """第七个分类的资格（规格 §2.2 那一行）：轴是"首次配置要用户做什么"。"""
        for provider in PROVIDERS:
            assert registry.spec(provider.source_id).category == "email"

    @pytest.mark.parametrize("key", ("address", "password"))
    def test_the_two_fields_every_mailbox_needs_are_declared_required(self, key) -> None:
        fields = {f.key: f for f in registry.spec("email_qq").setup_fields}

        assert fields[key].required is True

    def test_the_secret_is_declared_a_secret(self) -> None:
        """`password` 必须是 `kind="secret"`：否则它会把授权码写进 `sources.yaml` 那面给人看的墙。"""
        fields = {f.key: f for f in registry.spec("email_qq").setup_fields}

        assert fields["password"].kind == "secret"

    def test_only_the_generic_row_asks_for_a_server(self) -> None:
        """表里那四家自己知道服务器；`email_imap` 那一行是"不在表里的那一家也能接"。"""
        fields = {f.key for f in registry.spec("email_imap").setup_fields}
        qq = {f.key for f in registry.spec("email_qq").setup_fields}

        assert "imap_host" in fields
        assert "imap_host" not in qq

    def test_the_cadence_field_came_from_the_shared_layer(self) -> None:
        """每源一行频率那一格是**注册表自动补的**（裁 191 二），这一族没自己写第二份。"""
        assert "cadence" in {f.key for f in registry.spec("email_gmail").setup_fields}

    def test_the_name_is_the_providers_own(self) -> None:
        assert registry.spec("email_qq").display_name == "QQ Mail"

    def test_media_stays_unmeasured_rather_than_pretending_false(self) -> None:
        """`supports_media` 留 `None`：附件与 HTML 这一版没实测，判据 5 不许拿 False 冒充测过。"""
        with pytest.raises(registry.CapabilityUnmeasured):
            registry.require("email_qq", "supports_media")

    def test_the_host_is_tls_only(self) -> None:
        """不给"要不要加密"那一格：留那个选项就等于允许明文把授权码发出去。"""
        assert all(p.port == 993 for p in PROVIDERS)


class TestAPullIsJustMail:
    def test_one_message_becomes_one_row(self) -> None:
        rows = collector(uids=("7",), messages={"7": raw()}).pull()

        assert len(rows) == 1
        row = rows[0]
        assert (row.source, row.kind) == ("email_qq", KIND)
        assert row.title == "治理"
        assert row.evidence["text"] == "请查看货件名称"
        assert row.evidence["account"] == "me@qq.com"
        assert row.evidence["folder"] == FOLDER

    def test_the_dedup_key_is_the_message_id(self) -> None:
        """幂等键跟着**信**走：同一封邮件转发/另存之后 UID 会变，Message-ID 不会。"""
        rows = collector(uids=("7",), messages={"7": raw()}).pull()

        assert rows[0].external_id == "m1@mail.test"

    def test_without_a_message_id_the_key_carries_the_account(self) -> None:
        """退化那一支必须带账号：同一家两个号（或两台服务器）撞上同一个 UID 时不许互相盖。"""
        rows = collector(uids=("7",), messages={"7": raw(message_id="")}).pull()

        assert rows[0].external_id == "me@qq.com:7"

    def test_a_bare_numeric_message_id_is_not_trusted(self) -> None:
        """裸一个 `12345`（没有域）那种残值会跨站撞键 —— 当没有，退到 `账号:UID` 更安全。"""
        rows = collector(uids=("7",), messages={"7": raw(message_id="12345")}).pull()

        assert rows[0].external_id == "me@qq.com:7"

    def test_the_time_is_the_message_s_own_moment(self) -> None:
        """`occurred_at` = 这封信自己的时间（裁 189）；形状交给池那唯一归一点，这里只出朴素 ISO。"""
        import datetime

        row = collector(uids=("7",), messages={"7": raw()}).pull()[0]

        assert "T" not in row.start_at and "+" not in row.start_at
        local = datetime.datetime.fromisoformat(row.start_at)
        if local.tzinfo is None:
            local = local.astimezone()
        assert abs((local - datetime.datetime.fromtimestamp(1_762_000_000,
                                                            datetime.timezone.utc)).total_seconds()) < 2

    def test_a_missing_date_lands_as_no_time_not_a_made_up_one(self) -> None:
        row = collector(uids=("7",), messages={"7": raw().replace(b"Date:", b"X-Date:")}).pull()[0]

        assert row.start_at == ""

    def test_a_multipart_message_lands_the_plain_text_and_not_the_attachment(self) -> None:
        """附件不进：`supports_media` 那一格因此还是未实测，正文只取 text/plain 那一段。"""
        row = collector(uids=("7",), messages={"7": raw(multipart=True)}).pull()[0]

        assert row.evidence["text"] == "请查看货件名称"
        assert "JVBERi0xLjQ" not in row.evidence["text"]

    def test_a_subjectless_message_falls_back_to_the_sender(self) -> None:
        row = collector(uids=("7",), messages={"7": raw(subject="", message_id="<x@mail.test>")}).pull()[0]

        assert row.title == "下前 <xia@mail.test>"

    def test_a_round_takes_at_most_the_cap_and_says_so_in_the_log(self, caplog) -> None:
        """到顶就出声：截了不说，下一次就以为箱子里只有这么多。"""
        import logging

        caplog.set_level(logging.INFO)
        uids = [str(n) for n in range(1, MAX_PER_ROUND + 51)]
        box = FakeTransport(uids=uids, messages={u: raw() for u in uids})

        rows = collector(transport=box).pull()

        assert len(rows) == MAX_PER_ROUND
        assert "还有 50 封没取" in caplog.text

    def test_the_box_is_closed_even_when_the_pull_fails(self) -> None:
        box = FakeTransport(uids=("7",), messages={"7": raw()})
        box.uids = lambda: (_ for _ in ()).throw(EmailUnavailable("collect_failed", "列不出"))

        with pytest.raises(EmailUnavailable):
            collector(transport=box).pull()

        assert "close" in box.calls


class TestTheRealWiring:
    """生产那一只手到底对服务器说了哪些命令——**只读**是这里的全部判据。"""

    class FakeImap:
        def __init__(self, *, login_error=None) -> None:
            self.commands: list[tuple] = []
            self._login_error = login_error

        def login(self, user, secret):
            self.commands.append(("login", user, secret))
            if self._login_error:
                raise self._login_error
            return "OK", [b""]

        def select(self, mailbox, readonly=False):
            self.commands.append(("select", mailbox, readonly))
            return "OK", [b"3"]

        def uid(self, command, *args):
            self.commands.append(("uid", command, *args))
            if command == "SEARCH":
                return "OK", [b"1 2 3"]
            return "OK", [(b"3 (BODY[] {7}", b"hello\r\n"), b")"]

        def close(self):
            self.commands.append(("close",))

        def logout(self):
            self.commands.append(("logout",))

    def _transport(self, monkeypatch, imap) -> ImapTransport:
        monkeypatch.setattr(imaplib, "IMAP4_SSL", lambda *a, **kw: imap)
        transport = ImapTransport("imap.test", 993)
        transport.connect("me@mail.test", SECRET)
        return transport

    def test_fetching_uses_PEEK_so_the_mailbox_state_does_not_move(self, monkeypatch) -> None:
        imap = self.FakeImap()
        transport = self._transport(monkeypatch, imap)
        transport.select(FOLDER)
        transport.fetch("3")

        assert ("uid", "FETCH", "3", "BODY.PEEK[]") in imap.commands
        assert not any(c[0] == "uid" and c[1] == "FETCH" and c[3] == "BODY[]" for c in imap.commands)

    def test_the_mailbox_is_selected_readonly(self, monkeypatch) -> None:
        imap = self.FakeImap()
        self._transport(monkeypatch, imap).select(FOLDER)

        assert ("select", f'"{FOLDER}"', True) in imap.commands

    def test_nothing_here_stores_or_expunges(self, monkeypatch) -> None:
        imap = self.FakeImap()
        transport = self._transport(monkeypatch, imap)
        transport.select(FOLDER)
        transport.uids()
        transport.fetch("3")
        transport.close()

        verbs = {c[1] for c in imap.commands if c[0] == "uid"}

        assert verbs == {"SEARCH", "FETCH"}, "改了邮箱状态的动作一个都不许出现在这一族里"

    def test_a_login_failure_says_auth_failed_and_does_not_echo_the_secret(self, monkeypatch) -> None:
        imap = self.FakeImap(login_error=imaplib.IMAP4.error("LOGIN failed for me@mail.test"))
        monkeypatch.setattr(imaplib, "IMAP4_SSL", lambda *a, **kw: imap)

        with pytest.raises(EmailUnavailable) as caught:
            ImapTransport("imap.test", 993).connect("me@mail.test", SECRET)

        assert caught.value.state == "auth_failed"
        assert SECRET not in caught.value.detail

    def test_an_unreachable_server_is_network_unreachable_not_a_crash(self, monkeypatch) -> None:
        def boom(*a, **kw):
            raise OSError("connection refused")

        monkeypatch.setattr(imaplib, "IMAP4_SSL", boom)

        with pytest.raises(EmailUnavailable) as caught:
            ImapTransport("imap.test", 993).connect("me@mail.test", SECRET)

        assert caught.value.state == "network_unreachable"


class TestBuildReadsTheConfigLayer:
    def test_the_address_and_the_secret_come_from_the_two_read_ports(self) -> None:
        seen: list[str] = []
        box = collector(transport=FakeTransport())  # 只为拿一只 collector 的形状，这里不用它

        def secret_reader(key):
            seen.append(key)
            return SECRET

        built = EmailCollector.build(PROVIDERS[0], config={"address": "me@qq.com", "password": "x"},
                                     secret_reader=secret_reader)

        assert built.address == "me@qq.com"
        assert seen == ["password"], "授权码只走 secret_value 那一只读口，不读 sources.yaml 那一格"
        assert box.source_id == built.source_id == "email_qq"

    def test_the_generic_row_reads_the_host_the_user_typed(self) -> None:
        hosts = []
        original = ImapTransport.__init__

        def spy(self, host, port, **kw):
            hosts.append((host, port))
            return original(self, host, port, **kw)

        ImapTransport.__init__ = spy
        try:
            EmailCollector.build(PROVIDERS[-1], config={"address": "me@corp.cn", "imap_host": "imap.corp.cn"},
                                 secret_reader=lambda key: SECRET)
        finally:
            ImapTransport.__init__ = original

        assert hosts == [("imap.corp.cn", 993)]

    def test_nothing_is_dialed_before_the_credentials_are_there(self) -> None:
        """没填齐就说没填齐，不去拨那一次连接（拨了也只是拿一个 auth_failed 盖住真原因）。"""
        with pytest.raises(EmailUnavailable) as caught:
            EmailCollector.build(PROVIDERS[0], config={"address": ""}, secret_reader=lambda k: None)

        assert caught.value.state == "auth_failed"

    def test_a_provider_without_a_host_and_without_a_typed_one_says_so(self) -> None:
        with pytest.raises(EmailUnavailable) as caught:
            EmailCollector.build(PROVIDERS[-1], config={"address": "me@x.test"},
                                 secret_reader=lambda k: SECRET)

        assert caught.value.state == "collect_failed"


class TestTheSecretReadPort:
    """`secret_value` 是这一族新加的那只手——它的存在理由：一处读口 = 一处能负责"不进日志"的地方。"""

    @pytest.fixture(autouse=True)
    def _home(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("PLOBI_HOME", str(tmp_path))
        monkeypatch.delenv("PLOBI_SOURCE_EMAIL_QQ_PASSWORD", raising=False)

    def test_unset_is_none_not_an_empty_string(self) -> None:
        assert sources_config.secret_value("email_qq", "password") is None

    def test_what_was_written_is_what_build_reads(self, monkeypatch) -> None:
        sources_config.set_secret("email_qq", "password", SECRET)
        monkeypatch.setattr("plobi_cli.config.load_env", lambda *a, **kw: {
            sources_config.secret_env_name("email_qq", "password"): SECRET})

        built = EmailCollector.build(PROVIDERS[0], config={"address": "me@qq.com"})

        assert built._secret == SECRET

    def test_the_value_never_appears_in_the_row_read_for_the_screen(self, monkeypatch) -> None:
        """界面上那一格只有"设过没有"——值进了 payload 就是"星号"那一格的牙齿掉了。"""
        monkeypatch.setattr("plobi_cli.config.load_env", lambda *a, **kw: {
            sources_config.secret_env_name("email_qq", "password"): SECRET})

        assert sources_config.secret_configured("email_qq") == {"password": True}
        assert SECRET not in repr(sources_config.secret_configured("email_qq"))
