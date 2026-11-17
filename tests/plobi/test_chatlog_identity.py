"""WP-WECHAT-IDENTITY-NAMES：微信腿「谁说的／我是谁／真名」三样。

11-17 真机现核（开发态家 `D:/Data/AppData/Plobi-Dev/plobi/pool.db`）：池里 **5035** 条微信消息，
`context.sender` 落成机器号的 **1115 条全是同一个 `wxid_bipxx6h3hlvp22`**（＝本机账号），
`context.talker_name` 仍是机器号或会话号的 **2427** 条，`is_self` **0** 条。

三处断链（都钉到行）：

1. ``client.py`` 把 ``is_self`` 解析出来了（三态，`None`＝"chatlog 没说"），
   可落点那一层**没带**这一格——全仓唯一消费点是喂模型的提示词那一行。
2. 上游 ``vendor/chatlog/internal/wechatdb/repository/message.go:55`` 那句
   ``if msg.SenderName == "" && !msg.IsSelf`` ⇒ **本人消息的 senderName 被故意留空**，
   而旧的取值链 ``_first(...)`` 跳过空串 ⇒ 落到 ``sender``＝微信 ID ⇒
   "我"自己发的那批在池里长成自己的机器号。
3. 窗口名取自 ``/api/v1/session``，而 4.x 那一格 ``nickName`` 是 ``LastSenderDisplayName``
   （**最后发言者的昵称**、不是群名）——可读群名在 ``/api/v1/chatroom``，采集侧没接。

这一档钉的是"**库里到底长了什么**"：机器号不许冒充人名、"是不是我发的"不许没有、
问不出就说不知道（不许猜、不许拿会话号顶替）。
"""

from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from plobi.agenda.service import AgendaService
from plobi.collectors.chatlog import roster, sidecar
from plobi.collectors.chatlog.client import ChatMessage, normalize_message
from plobi.collectors.chatlog.config import CollectorConfig
from plobi.collectors.chatlog.confirm import HeuristicConfirmer
from plobi.collectors.chatlog.pipeline import ChatlogPipeline, IngestReport
from plobi.collectors.chatlog.state import SeenStore, TalkerStore

NOW = datetime(2026, 8, 25, 10, 0)

#: 这台机器上的本机账号（11-17 真机现核：contact 表 id=351，nick=Nono、alias=ddy19858131702）。
ACCOUNT = "wxid_bipxx6h3hlvp22"
ACCOUNT_DIR = "wxid_bipxx6h3hlvp22_c19c"


class FakeClient:
    """侧车那三只列表口的替身（`roster._items` 走的是 ``client._get`` 这一缝）。"""

    def __init__(self, contacts=None, chatrooms=None, sessions=None):
        self.contacts = contacts or []
        self.chatrooms = chatrooms or []
        self.sessions = sessions or []

    def _get(self, path, params):
        if path == "/api/v1/contact":
            return {"items": self.contacts}
        if path == "/api/v1/chatroom":
            return {"items": self.chatrooms}
        if path == "/api/v1/session":
            return {"items": self.sessions}
        return {"items": []}

    def list_talker_sessions(self, keyword: str = "", limit: int = 10000):
        return [SimpleNamespace(id=row.get("userName", ""), name=row.get("nickName", ""))
                for row in self.sessions]

    def fetch(self, talker, day=None):
        return []

    def list_talkers(self, keyword: str = "", limit: int = 10000):
        return [row.get("userName", "") for row in self.sessions]

    def healthy(self):
        return True


@pytest.fixture(autouse=True)
def _pool(tmp_path):
    """落点是**池**：不钉这一格，`handle_message` 会去写真的共享那一份。"""
    import plobi.pool as pool

    original = pool.store.path
    pool.store.path = lambda: tmp_path / "pool.db"
    try:
        yield pool
    finally:
        pool.store.path = original


def _pooled(pool) -> list:
    return pool.query()


def _pipeline(tmp_path, client) -> ChatlogPipeline:
    return ChatlogPipeline(
        config=CollectorConfig(mode="whitelist", talkers=["班级群"], enabled=True),
        client=client,
        service=AgendaService(tmp_path / "agenda.db"),
        seen=SeenStore(tmp_path / "seen.db"),
        talkers=TalkerStore(tmp_path / "talkers.db"),
        confirmer=HeuristicConfirmer(now_factory=lambda: NOW),
    )


def _msg(**kwargs) -> ChatMessage:
    base = dict(
        msg_id="m1",
        talker="班级群",
        sender="",
        sent_at="2026-08-25 10:00:00",
        content="好的收到",
    )
    base.update(kwargs)
    return ChatMessage(**base)


# --------------------------------------------------------------------------- #
# ① 发件人：id 与名字分家，机器号不许冒充人名
# --------------------------------------------------------------------------- #


def test_normalizer_keeps_the_id_and_the_name_in_two_slots():
    """侧车给了名字与 id：名字归名字那一格，id 归 id 那一格。"""
    msg = normalize_message({
        "content": "好的收到",
        "talker": "50264584189@chatroom",
        "sender": "wxid_3r3j7wcvmul222",
        "senderName": "邱德彧",
    })

    assert msg.sender == "邱德彧"
    assert msg.sender_id == "wxid_3r3j7wcvmul222"


def test_normalizer_never_falls_back_to_the_machine_id_as_a_name():
    """**本人**的消息：侧车**故意**把 senderName 留空（`message.go:55`）。

    旧形状在这里把机器号捡起来当名字 ⇒ 池里"我"的那批长成自己的机器号。
    现在那一格是**空**（"没查到名字"），机器号在 ``sender_id`` 那一格。
    """
    msg = normalize_message({
        "content": "好的收到",
        "talker": "wxid_c1g832e6jk5322",
        "sender": ACCOUNT,
        "senderName": "",
    })

    assert msg.sender == ""
    assert msg.sender_id == ACCOUNT
    assert not msg.sender.startswith("wxid_")


# --------------------------------------------------------------------------- #
# ② 本机账号：候选从配置来，**由名单验**
# --------------------------------------------------------------------------- #


def test_account_candidates_cover_every_underscore_prefix(tmp_path):
    """账号目录名不是裸 wxid（上游叫它 `AccountName`）——候选把每个下划线前缀都摆出来。"""
    engine_dir = tmp_path / "chatlog"
    engine_dir.mkdir()
    (engine_dir / "chatlog-server.json").write_text(json.dumps({
        "data_dir": r"D:\Data\Local\Weixin\xwechat_files\wxid_bipxx6h3hlvp22_c19c",
        "data_key": "should-never-be-read",
    }), encoding="utf-8")

    candidates = sidecar.account_candidates({"CHATLOG_DIR": str(engine_dir)})

    assert ACCOUNT in candidates
    assert ACCOUNT_DIR in candidates
    assert candidates[0] == ACCOUNT_DIR, "整段排第一（最可能），但**不假设**后缀是几位"


def test_account_candidates_read_nothing_but_the_shape(tmp_path):
    """密钥的值一个字都不往外带——这一档只取 `data_dir`。"""
    engine_dir = tmp_path / "chatlog"
    engine_dir.mkdir()
    (engine_dir / "chatlog.json").write_text(json.dumps({
        "account": ACCOUNT_DIR,
        "data_key": "secret-value-must-not-leak",
    }), encoding="utf-8")

    candidates = sidecar.account_candidates({"CHATLOG_DIR": str(engine_dir)})

    assert ACCOUNT in candidates
    assert all("secret" not in c for c in candidates)


def test_account_username_is_verified_against_the_contact_list():
    """候选在名单里 ⇒ 认；不在 ⇒ **空串**（不知道就说不知道）。"""
    client = FakeClient(contacts=[{"userName": ACCOUNT, "nickName": "Nono"}])

    assert roster.account_username(client, [ACCOUNT_DIR, ACCOUNT]) == ACCOUNT
    assert roster.account_username(client, [ACCOUNT_DIR]) == ""
    assert roster.account_username(client, []) == ""


def test_account_username_refuses_a_candidate_that_is_not_a_wxid():
    """候选在名单里但不是 `wxid_` 开头 ⇒ 不认（那是会话号，不是账号）。"""
    client = FakeClient(contacts=[{"userName": "filehelper", "nickName": "文件传输助手"}])

    assert roster.account_username(client, ["filehelper"]) == ""


# --------------------------------------------------------------------------- #
# ③ 落池：is_self 三态 ＋ 人名
# --------------------------------------------------------------------------- #


def test_pool_row_marks_the_account_itself_as_self(tmp_path, monkeypatch, _pool):
    """本机账号自己发的那条：``is_self`` 是 true，"谁说的"写**本机昵称**。

    昵称查得到就写真名（"Nono"）——那一格是事实；连它也查不到才写「我」。
    """
    client = FakeClient(contacts=[{"userName": ACCOUNT, "nickName": "Nono"}])
    monkeypatch.setattr(sidecar, "account_candidates", lambda *a, **k: [ACCOUNT_DIR, ACCOUNT])
    pipeline = _pipeline(tmp_path, client)

    pipeline.handle_message(_msg(msg_id="mine", sender_id=ACCOUNT), IngestReport())

    context = _pooled(_pool)[0]["context"]
    assert context["is_self"] is True
    assert context["sender"] == "Nono"
    assert context["sender_id"] == ACCOUNT


def test_pool_row_writes_me_only_when_the_account_has_no_name_anywhere(tmp_path, monkeypatch, _pool):
    """侧车说这条是本人发的、而本机昵称哪儿都查不到 ⇒ 那一格写「我」。

    这一格是确定的（就是本人），写「我」不是编名字，是"这一格没有名字可写"。
    （**没验出本机账号**是另一回事：那一条 `is_self` 是 null，不许写成 true。）
    """
    client = FakeClient(contacts=[])
    monkeypatch.setattr(sidecar, "account_candidates", lambda *a, **k: ["wxid_nobody_xxx"])
    pipeline = _pipeline(tmp_path, client)

    pipeline.handle_message(_msg(msg_id="mine-anonymous", sender_id=ACCOUNT, is_self=True),
                            IngestReport())

    context = _pooled(_pool)[0]["context"]
    assert context["is_self"] is True
    assert context["sender"] == "我"


def test_pool_row_marks_somebody_else_as_not_self(tmp_path, monkeypatch, _pool):
    """别人发的那条：``is_self`` 是 false，"谁说的"是人名。"""
    client = FakeClient(contacts=[
        {"userName": ACCOUNT, "nickName": "Nono"},
        {"userName": "wxid_3r3j7wcvmul222", "nickName": "邱德彧"},
    ])
    monkeypatch.setattr(sidecar, "account_candidates", lambda *a, **k: [ACCOUNT_DIR, ACCOUNT])
    pipeline = _pipeline(tmp_path, client)

    pipeline.handle_message(
        _msg(msg_id="theirs", sender="邱德彧", sender_id="wxid_3r3j7wcvmul222"),
        IngestReport(),
    )

    context = _pooled(_pool)[0]["context"]
    assert context["is_self"] is False
    assert context["sender"] == "邱德彧"


def test_pool_row_keeps_is_self_unknown_when_nobody_says(tmp_path, monkeypatch, _pool):
    """侧车没说、也没验出本机账号 ⇒ 那一格是**null**，不是猜出来的 false。"""
    client = FakeClient(contacts=[])
    monkeypatch.setattr(sidecar, "account_candidates", lambda *a, **k: ["wxid_nobody_xxx"])
    pipeline = _pipeline(tmp_path, client)

    pipeline.handle_message(_msg(msg_id="unknown"), IngestReport())

    context = _pooled(_pool)[0]["context"]
    assert context["is_self"] is None


def test_pool_row_never_lands_the_machine_id_as_the_sender_name(tmp_path, monkeypatch, _pool):
    """查不到名字时："谁说的"是**空**，机器号在 ``sender_id`` 那一格。"""
    client = FakeClient(contacts=[{"userName": ACCOUNT, "nickName": "Nono"}])
    monkeypatch.setattr(sidecar, "account_candidates", lambda *a, **k: [ACCOUNT_DIR, ACCOUNT])
    pipeline = _pipeline(tmp_path, client)

    pipeline.handle_message(
        _msg(msg_id="anon", sender="", sender_id="wxid_3r3j7wcvmul222"),
        IngestReport(),
    )

    context = _pooled(_pool)[0]["context"]
    assert context["sender"] == "", "查不到名字时是**空**，机器号不许顶上来"
    assert context["sender_id"] == "wxid_3r3j7wcvmul222"


def test_pool_row_falls_back_to_the_contact_table_for_the_name(tmp_path, monkeypatch, _pool):
    """侧车没给名字（v3 那一族就是）⇒ 查联系人名单拿备注/昵称。"""
    client = FakeClient(contacts=[
        {"userName": "wxid_3r3j7wcvmul222", "remark": "邱老师", "nickName": "小邱"},
    ])
    monkeypatch.setattr(sidecar, "account_candidates", lambda *a, **k: [ACCOUNT_DIR, ACCOUNT])
    pipeline = _pipeline(tmp_path, client)

    pipeline.handle_message(
        _msg(msg_id="byid", sender="", sender_id="wxid_3r3j7wcvmul222"),
        IngestReport(),
    )

    context = _pooled(_pool)[0]["context"]
    assert context["sender"] == "邱老师", "备注优先于昵称（他那句「尽量显示我的备注」）"


# --------------------------------------------------------------------------- #
# ④ 窗口名：先问群那张表，再退回 session
# --------------------------------------------------------------------------- #


def test_talker_name_prefers_the_chatroom_table_over_the_session_list(tmp_path):
    """4.x 的 session `nickName` 是**最后发言者的昵称**——拿它当窗口名就是一句假话。

    真群名在 ``/api/v1/chatroom``：先问它，问不到再退回 session。
    """
    client = FakeClient(
        chatrooms=[{"name": "50264584189@chatroom", "nickName": "电子工程训练（乙）"}],
        sessions=[{"userName": "50264584189@chatroom", "nickName": "青の寂"}],
    )
    pipeline = _pipeline(tmp_path, client)

    names = pipeline._talker_name_map()

    assert names["50264584189@chatroom"] == "电子工程训练（乙）"


def test_talker_name_falls_back_to_the_session_list_when_the_table_is_silent(tmp_path):
    """群那张表查不到 ⇒ 退回 session（总比空着强）。"""
    client = FakeClient(
        chatrooms=[],
        sessions=[{"userName": "wxid_c1g832e6jk5322", "nickName": "同门师兄"}],
    )
    pipeline = _pipeline(tmp_path, client)

    names = pipeline._talker_name_map()

    assert names["wxid_c1g832e6jk5322"] == "同门师兄"
