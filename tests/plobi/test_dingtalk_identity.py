r"""WP-DINGTALK-IDENTITY-MEDIA：钉钉腿「分组／谁说的／真名／非文本／真字节」。

11-17 真机现核（开发态家 `D:/Data/AppData/Plobi-Dev/plobi/pool.db`）：池里 **1500** 条钉钉消息，
`conversation`／`visibility` 两列 **0/1500 全空**，`context.sender_name` **0 行**，
`context.is_self` **0 行**；非文本那几类（语音 3／视频 202／钉盘文件 500·501）落成「（非文本消息）」占位。

四样都在**采集侧那一层**丢的，补齐不需要新数据源：

1. **会话那两列**——微信腿在 `chatlog/pipeline.py` **自己**填，钉钉腿走公共落点 `sweep.py`，
   那里 `PoolItem(...)` 只给了 `occurred_at` ⇒ 同一个池两套写法，按会话筛不动、分不了群。
   会话名单那层其实齐：`store.list_conversations` **已经返回 `type`**（1=私聊 89／2=群 95）。
2. **发件人真名**——`tbuser_profile_v2`（**2146 行 nick**）与 `tbuser_group_nick`（群里的备注名）
   这一族以前**零引用** ⇒ 发件人只剩一个数字 uid（现核：1525/1565 条能命中，97%）。
3. **是不是我本人发的**——本机 uid（`store.uid` ＝ 解开钥匙的那个 uid；现核 `5786844176`，
   nick 邓德彧，在库里发了 84 条）对照，**三态**（True／False／None＝没验出来）。
4. **真字节**——消息体里**原样**带着的 URL：免鉴权的那族真取回来，要凭据的那族
   **如实说缺凭据**（盘上没有凭据），**不许拿缩略图冒充原件**。

零真数据：库里每一行都是这个文件自己写进去的假句子；网络那一格用夹具挡着（不许拿真账号的
链接当判据）。真采读数那一档见纸上 §3 判据 6（要钉钉客户端在跑）。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plobi.collectors import sweep
from plobi.collectors.base import Occurrence
from plobi.collectors.dingtalk import store
from plobi.collectors.dingtalk.collector import DingTalkCollector

#: 这台机器上的本机账号（11-17 真机现核：nick 邓德彧）。
MINE = "5786844176"
#: 群里另一个人（`tbuser_profile_v2` 那一行）。
OTHER = "659392009"

CONVERSATION_TABLE = "CREATE TABLE tbconversation (cid TEXT, title TEXT, type INTEGER, lastModify TEXT)"
MESSAGE_TABLE = ("CREATE TABLE tbmsg_000 (cid TEXT, mid TEXT, senderId TEXT, createdAt INTEGER,"
                 " contentType INTEGER, content TEXT, recallStatus INTEGER, atIds TEXT)")
PROFILE_TABLE = "CREATE TABLE tbuser_profile_v2 (uid TEXT, nick TEXT, realName TEXT, alias TEXT)"
GROUP_NICK_TABLE = "CREATE TABLE tbuser_group_nick (cid TEXT, uid TEXT, groupNick TEXT)"
IMAGE_TABLE = ("CREATE TABLE im_image_info (mid TEXT, url_md5 TEXT, local_path TEXT, size INTEGER,"
               " image_type INTEGER, create_at INTEGER)")


def fake_store(*, conversations=(), messages=(), profiles=(), group_nicks=(), images=(),
               uid=MINE) -> SimpleNamespace:
    """一内存库装**真表真列**：昵称表与群备注表以前零引用，这一档要把它们接上。"""
    connection = sqlite3.connect(":memory:")
    for table in (CONVERSATION_TABLE, MESSAGE_TABLE, PROFILE_TABLE, GROUP_NICK_TABLE, IMAGE_TABLE):
        connection.execute(table)
    connection.executemany("INSERT INTO tbconversation VALUES (?,?,?,?)", conversations)
    connection.executemany("INSERT INTO tbmsg_000 VALUES (?,?,?,?,?,?,?,?)", messages)
    connection.executemany("INSERT INTO tbuser_profile_v2 VALUES (?,?,?,?)", profiles)
    connection.executemany("INSERT INTO tbuser_group_nick VALUES (?,?,?)", group_nicks)
    connection.executemany("INSERT INTO im_image_info VALUES (?,?,?,?,?,?)", images)
    connection.commit()

    return SimpleNamespace(connection=connection, profile="p-test", rows=len(messages),
                           uid=uid, close=connection.close)


def message(cid: str, mid: str, sender: str, *, text: str = "好的收到", ctype: int = 1,
            created: int = 1_762_000_000_000) -> tuple:
    body = json.dumps({"text": text}, ensure_ascii=False) if ctype == 1 else text
    return (cid, mid, sender, created, ctype, body, 0, "")


def attachment(ctype: int, extension: dict, **extra) -> str:
    """`attachments[].extension` 那一层套娃（这台的真形状：外面是列表，每格再解一次 JSON）。"""
    payload = {"contentType": ctype,
               "attachments": [{"extension": json.dumps(extension, ensure_ascii=False),
                                "size": 0, "type": ctype, "url": "", **extra}]}
    return json.dumps(payload, ensure_ascii=False)


@pytest.fixture()
def pool(tmp_path, monkeypatch):
    """落点是**池**：不钉这一格，测试会写进真的共享那一份。"""
    import plobi.pool as pool_module

    original = pool_module.store.path
    pool_module.store.path = lambda: tmp_path / "pool.db"
    try:
        yield pool_module
    finally:
        pool_module.store.path = original


def collector(opened, **kwargs) -> DingTalkCollector:
    """默认**不触网、不碰真机**：日历那一枚库不开（测试不认本机客户端），
    本机索引不读（`media={}`），远端那一格由各档自己给夹具。"""
    kwargs.setdefault("media", {})

    def no_calendar():
        raise store.LocalStoreUnavailable("local_store_missing", "测试里不开日历那一枚库")

    kwargs.setdefault("calendar_opener", no_calendar)
    return DingTalkCollector(local_store=opened, **kwargs)


# --------------------------------------------------------------------------- #
# ① 会话那两列：池里真的分得出群与私聊
# --------------------------------------------------------------------------- #


def test_the_pool_rows_carry_the_conversation_and_the_visibility(pool) -> None:
    """同一扇会话的那些行要能筛得出来；群与私聊要分得开。"""
    opened = fake_store(
        conversations=[("group-1", "工程组", 2, "1792000000000"),
                       ("private-1", "659392009", 1, "1792000000000")],
        messages=[message("group-1", "m1", OTHER), message("private-1", "m2", MINE)],
    )
    sweep._collect_one("dingtalk", collector(opened), service=None)

    rows = {row["external_id"]: row for row in pool.query(source="dingtalk")}
    assert rows["group-1:m1"]["conversation"] == "group-1"
    assert rows["group-1:m1"]["visibility"] == "group"
    assert rows["private-1:m2"]["conversation"] == "private-1"
    assert rows["private-1:m2"]["visibility"] == "private"


def test_another_source_keeps_those_two_columns_empty(pool) -> None:
    """**别的源没有会话概念就留空**——不许顺手替它们编一个。"""

    class Other:
        def pull(self):
            return [Occurrence(source="email_qq", external_id="x1", title="别的源",
                               start_at="2026-11-01 09:00:00", kind="note",
                               evidence={"text": "一封信"})]

    sweep._collect_one("email_qq", Other(), service=None)

    row = pool.query(source="email_qq")[0]
    assert row["conversation"] == "" and row["visibility"] == ""


# --------------------------------------------------------------------------- #
# ② 发件人：人名，不是数字 uid
# --------------------------------------------------------------------------- #


def test_the_sender_is_a_name_not_a_number(pool) -> None:
    """`tbuser_profile_v2` 那一格现成：以前零引用 ⇒ 发件人只剩一个 uid。"""
    opened = fake_store(
        conversations=[("group-1", "工程组", 2, "1792000000000")],
        messages=[message("group-1", "m1", OTHER)],
        profiles=[(OTHER, "邱德彧", "", ""), (MINE, "邓德彧", "", "")],
    )
    sweep._collect_one("dingtalk", collector(opened), service=None)

    context = pool.query(source="dingtalk")[0]["context"]
    assert context["sender_name"] == "邱德彧"
    assert context["sender_id"] == OTHER


def test_a_group_note_wins_over_the_global_nick(pool) -> None:
    """群里的备注名比全局昵称更贴近"他怎么称呼对方" ⇒ **群备注优先**。"""
    opened = fake_store(
        conversations=[("group-1", "工程组", 2, "1792000000000")],
        messages=[message("group-1", "m1", OTHER)],
        profiles=[(OTHER, "邱德彧", "", "")],
        group_nicks=[("group-1", OTHER, "邱老师")],
    )
    sweep._collect_one("dingtalk", collector(opened), service=None)

    context = pool.query(source="dingtalk")[0]["context"]
    assert context["sender_name"] == "邱老师"


def test_an_unknown_sender_leaves_the_name_empty(pool) -> None:
    """查不到就**空着**——空着是"没查到"，拿 uid 顶上就是"编了一个名字"。"""
    opened = fake_store(
        conversations=[("group-1", "工程组", 2, "1792000000000")],
        messages=[message("group-1", "m1", "999999999")],
    )
    sweep._collect_one("dingtalk", collector(opened), service=None)

    context = pool.query(source="dingtalk")[0]["context"]
    assert context["sender_name"] == ""
    assert context["sender_id"] == "999999999"


# --------------------------------------------------------------------------- #
# ③ 是不是我本人发的：三态
# --------------------------------------------------------------------------- #


def test_my_own_message_is_marked_as_self(pool) -> None:
    """本机 uid 对照 ⇒ `is_self` 是 true，"谁说的"是**本机昵称**。"""
    opened = fake_store(
        conversations=[("private-1", "659392009", 1, "1792000000000")],
        messages=[message("private-1", "m1", MINE)],
        profiles=[(MINE, "邓德彧", "", "")],
    )
    sweep._collect_one("dingtalk", collector(opened), service=None)

    context = pool.query(source="dingtalk")[0]["context"]
    assert context["is_self"] is True
    assert context["sender_name"] == "邓德彧"


def test_somebody_else_is_not_self(pool) -> None:
    opened = fake_store(
        conversations=[("group-1", "工程组", 2, "1792000000000")],
        messages=[message("group-1", "m1", OTHER)],
        profiles=[(OTHER, "邱德彧", "", "")],
    )
    sweep._collect_one("dingtalk", collector(opened), service=None)

    context = pool.query(source="dingtalk")[0]["context"]
    assert context["is_self"] is False


def test_is_self_stays_unknown_when_the_account_is_not_known(pool) -> None:
    """没验出本机 uid ⇒ 那一格是 **null**，不是猜出来的 false。"""
    opened = fake_store(
        conversations=[("group-1", "工程组", 2, "1792000000000")],
        messages=[message("group-1", "m1", OTHER)],
        uid="",
    )
    sweep._collect_one("dingtalk", collector(opened), service=None)

    context = pool.query(source="dingtalk")[0]["context"]
    assert context["is_self"] is None


def test_the_private_window_shows_the_other_side_name(pool) -> None:
    """私聊那一格库里存的是**对方 uid** ⇒ 窗口名换成对方的名字。"""
    opened = fake_store(
        conversations=[("659392009:5786844176", "659392009", 1, "1792000000000")],
        messages=[message("659392009:5786844176", "m1", MINE)],
        profiles=[(OTHER, "邱德彧", "", "")],
    )
    sweep._collect_one("dingtalk", collector(opened), service=None)

    context = pool.query(source="dingtalk")[0]["context"]
    assert context["talker_name"] == "邱德彧"


# --------------------------------------------------------------------------- #
# ④ 非文本不再占位
# --------------------------------------------------------------------------- #


def test_a_voice_message_reads_as_its_duration(pool) -> None:
    """语音那一族的**时长**是毫秒（`audioContent.duration`）——库里本来就有。"""
    opened = fake_store(
        conversations=[("group-1", "工程组", 2, "1792000000000")],
        messages=[message("group-1", "m1", OTHER, ctype=3,
                          text=json.dumps({"contentType": 3,
                                           "audioContent": {"duration": 1000,
                                                            "mediaId": "@lR_PJx9ZOsv"},
                                           }, ensure_ascii=False))],
    )
    sweep._collect_one("dingtalk", collector(opened), service=None)

    row = pool.query(source="dingtalk")[0]
    assert row["title"] == "语音（1 秒）"
    assert "非文本消息" not in row["title"]


def test_a_video_message_reads_as_its_duration_and_size(pool) -> None:
    opened = fake_store(
        conversations=[("group-1", "工程组", 2, "1792000000000")],
        messages=[message("group-1", "m1", OTHER, ctype=202,
                          text=attachment(202, {"duration": "23", "fileType": "mp4"},
                                          size=2_619_160))],
    )
    sweep._collect_one("dingtalk", collector(opened), service=None)

    row = pool.query(source="dingtalk")[0]
    assert row["title"] == "视频（23 秒，2.5 MB）"
    assert "非文本消息" not in row["title"]


def test_a_space_file_reads_as_its_name_and_size(pool) -> None:
    """钉盘文件：**文件名与大小本来就在库里**（`f_name`／`f_size`）。"""
    opened = fake_store(
        conversations=[("group-1", "工程组", 2, "1792000000000")],
        messages=[message("group-1", "m1", OTHER, ctype=500,
                          text=attachment(500, {"f_name": "浙江大学三好杯规程.pdf",
                                                "f_size": "524503", "f_type": "pdf"}))],
    )
    sweep._collect_one("dingtalk", collector(opened), service=None)

    row = pool.query(source="dingtalk")[0]
    assert row["title"] == "浙江大学三好杯规程.pdf（512.2 KB）"
    assert "非文本消息" not in row["title"]


def test_a_news_card_reads_its_title(pool) -> None:
    """资讯（300）那一族的正文是一张表单，标题在那对 `{k, v}` 里。"""
    form = json.dumps([{"k": "新闻标题：", "v": "第九期“启真探业”校外行业导师一览"}],
                      ensure_ascii=False)
    opened = fake_store(
        conversations=[("group-1", "工程组", 2, "1792000000000")],
        messages=[message("group-1", "m1", OTHER, ctype=300,
                          text=attachment(300, {"author": "就业指导与服务中心",
                                                "b_form": form}))],
    )
    sweep._collect_one("dingtalk", collector(opened), service=None)

    row = pool.query(source="dingtalk")[0]
    assert row["title"] == "第九期“启真探业”校外行业导师一览"
    assert row["context"]["content_kind"] == "news"


# --------------------------------------------------------------------------- #
# ⑤ 真字节：本机缓存优先 → 免鉴权原样取 → 要凭据的如实说
# --------------------------------------------------------------------------- #


def test_a_locally_cached_file_is_copied_into_plobis_own_home(tmp_path) -> None:
    """本机缓存件（`im_image_info.local_path`）**不触网**就搬进 Plobi 的家。"""
    source = tmp_path / "resource_cache" / "ab" / "shot.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"\x89PNG" + b"x" * 120)

    opened = fake_store(
        conversations=[("group-1", "工程组", 2, "1792000000000")],
        messages=[message("group-1", "m1", OTHER, ctype=2,
                          text=json.dumps({"contentType": 2, "filename": "shot.png",
                                           "picSize": 124}, ensure_ascii=False))],
        images=[("m1", "md5x", str(source), 4, 1, 1_780_000_000)],
    )
    into = tmp_path / "home"
    opened_collector = collector(
        opened, media=None,
        stage_media=lambda items: store.stage_media(items, into=into),
    )
    occurrences = opened_collector.pull()
    items = occurrences[0].evidence["media"]

    assert items, "本机有的件必须搬得进来"
    assert Path(items[0]["saved_to"]).is_file()
    assert Path(items[0]["saved_to"]).read_bytes() == source.read_bytes()


def test_the_url_in_the_message_body_is_fetched_verbatim(tmp_path, monkeypatch) -> None:
    """**原样** GET 消息体里那条 URL——自己拼的六种变体实测全 404。"""
    payload = b"\x89PNG" + b"y" * 40
    seen: list[str] = []

    class FakeResponse:
        status_code = 200
        headers = {"Content-Type": "image/png"}
        content = payload

        def close(self) -> None:
            pass

    def fake_get(url, timeout=None):
        seen.append(url)
        return FakeResponse()

    monkeypatch.setattr(requests, "get", fake_get)
    url = "https://down-cdn.dingtalk.com/media/fixture-thing_200_200.jpg"

    result = store.fetch_media(url, into=tmp_path / "home")

    assert seen == [url], "必须原样取，不许自己拼"
    assert result["saved_to"]
    assert Path(result["saved_to"]).read_bytes() == payload


def test_the_credential_family_says_it_cannot_be_fetched(tmp_path) -> None:
    """`down.dingtalk.com` 那一族要登录凭据（实测 403）⇒ **如实说，不许假成功**。"""
    result = store.fetch_media("https://down.dingtalk.com/ddmedia/whatever.jpg",
                               into=tmp_path / "home")

    assert result["status"] == "needs_credential"
    assert "saved_to" not in result
    assert result["reason"]


def test_a_message_with_a_credential_url_reports_it_as_pending(tmp_path, pool, monkeypatch) -> None:
    """要凭据的那一族在池里是**待取**（带原因），不是"这条没有媒体"。"""
    opened = fake_store(
        conversations=[("group-1", "工程组", 2, "1792000000000")],
        messages=[message("group-1", "m1", OTHER, ctype=2,
                          text=json.dumps({"contentType": 2, "filename": "shot.png",
                                           "url": "https://down.dingtalk.com/ddmedia/abc.jpg"},
                                          ensure_ascii=False))],
    )
    into = tmp_path / "home"
    opened_collector = collector(
        opened, media={},
        media_fetcher=lambda url: store.fetch_media(url, into=into),
    )
    sweep._collect_one("dingtalk", opened_collector, service=None)

    context = pool.query(source="dingtalk")[0]["context"]
    assert context["media"] == []
    pending = context["media_pending"]
    assert pending and pending[0]["status"] == "needs_credential"
    assert pending[0]["url"].endswith("abc.jpg")
    assert "saved_to" not in pending[0]


def test_a_free_url_is_fetched_and_lands_in_the_pool_row(tmp_path, pool, monkeypatch) -> None:
    """免鉴权的那一族：真字节取回来，池里那一行指得到**真的那个文件**。"""
    payload = b"GIF89a" + b"z" * 30

    class FakeResponse:
        status_code = 200
        headers = {"Content-Type": "image/gif"}
        content = payload

        def close(self) -> None:
            pass

    monkeypatch.setattr(requests, "get", lambda url, timeout=None: FakeResponse())
    opened = fake_store(
        conversations=[("group-1", "工程组", 2, "1792000000000")],
        messages=[message("group-1", "m1", OTHER, ctype=2,
                          text=json.dumps({"contentType": 2, "filename": "sticker.gif",
                                           "url": "https://down-cdn.dingtalk.com/ddmedia/xyz.gif"},
                                          ensure_ascii=False))],
    )
    into = tmp_path / "home"
    opened_collector = collector(
        opened, media={},
        media_fetcher=lambda url: store.fetch_media(url, into=into),
    )
    sweep._collect_one("dingtalk", opened_collector, service=None)

    context = pool.query(source="dingtalk")[0]["context"]
    saved = context["media"]
    assert saved and saved[0]["saved_to"]
    assert Path(saved[0]["saved_to"]).is_file()
    assert Path(saved[0]["saved_to"]).read_bytes() == payload
    assert "media_pending" not in context


def test_the_message_body_urls_are_picked_up_verbatim() -> None:
    """消息体里**原样**带着的那条 URL 要被认出来——自己拼的形状一概不认。"""
    content = json.dumps({"contentType": 2,
                           "url": "https://down-cdn.dingtalk.com/media/lQDPNC_200_200.jpg",
                           "authMedia": {"authMediaId": "$iwEeAqNq"}},
                          ensure_ascii=False)

    urls = store.media_urls_of(content)

    assert [item["url"] for item in urls] == [
        "https://down-cdn.dingtalk.com/media/lQDPNC_200_200.jpg"]
    assert urls[0]["needs_credential"] is False


def test_a_third_party_url_is_not_treated_as_media() -> None:
    """群里那些课程链接不是媒体件——不许去下人家的网页。"""
    content = json.dumps({"contentType": 102,
                           "attachments": [{"url": "https://classroom.zju.edu.cn/livingroom"}]},
                          ensure_ascii=False)

    assert store.media_urls_of(content) == []


def test_a_credential_url_is_marked_as_needing_a_credential() -> None:
    content = json.dumps({"contentType": 2,
                           "url": "https://down.dingtalk.com/ddmedia/abc.jpg"},
                          ensure_ascii=False)

    urls = store.media_urls_of(content)

    assert urls[0]["needs_credential"] is True
