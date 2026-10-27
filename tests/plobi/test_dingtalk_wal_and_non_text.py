r"""钉钉本机库这一刀的续刀（`WP-DINGTALK-LOCAL-STORE`）：**合 WAL** 与**非文本那一句人话**。

跑法：`bash scripts/run_tests.sh tests/plobi/test_dingtalk_wal_and_non_text.py -q`

两条都钉在这台 10-26 的实测形状上：

1. `-wal` 的**头是明文、帧里的页数据是密文、帧校验和不按 SQLite 那套算**——所以合并的依据
   只能是"盐值一致 + 只认提交标记"，尾巴没收完的那一组必须整个丢掉（收了就合出一棵
   引用空页的树，`integrity_check` 当场红）。这一条不是学术问题：不合 WAL 的话这台机器上
   聊天 156 条、最新一条停在 10-24，合完 **246 条、最新 10-26 21:54**。
2. 非文本那一族的**真字段路径**（逐类扒过这台 155 条样本）：卡片/文件/链接/小程序都在
   `attachments[].extension` 那层套娃 JSON 里，图片在顶层。屏上原来打的是 `（card）` 这种
   英文键名——这一族不许再出现。

零真数据：库里每一行都是这个文件自己写进去的假句子。
"""

import base64
import collections
import json
import sqlite3
import struct
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from plobi.collectors.dingtalk import collector as collector_module
from plobi.collectors.dingtalk import store

UID = "666165872"
SALT = "0123456789abcdef0123456789abcdef"
PAGE = 4096
MSG_COLUMNS = "(cid, mid, senderId, createdAt, contentType, content, recallStatus, atIds)"
MSG_VALUES = "VALUES (?,?,?,?,?,?,?,?)"


def encrypt_pages(plain: bytes, key: bytes) -> bytes:
    """整页 ECB（与 `store._decrypt_block` 同一把形状）；页大小 4096，16 整除，不需要补位。"""
    out = bytearray()
    for offset in range(0, len(plain), PAGE):
        block = plain[offset:offset + PAGE]
        if len(block) % 16:
            block = block + bytes(16 - len(block) % 16)
        encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
        out += encryptor.update(block) + encryptor.finalize()
    return bytes(out)


def encrypt_wal(wal: bytes, key: bytes) -> bytes:
    """照这台的真形状加密一枚 WAL：**头与帧头留明文，只有帧里的页数据是密文**。

    这一条是 10-26 逐帧读出来的（`magic=0x377f0682`、`page=4096` 在明文里读得到）。
    测试要是把整份 WAL 一起加密，产品那只手就永远读不到帧——那正是"假通过"的形状。
    """
    page = struct.unpack(">I", wal[8:12])[0]
    out = bytearray(wal)
    offset = 32
    while offset + 24 + page <= len(out):
        out[offset + 24:offset + 24 + page] = encrypt_pages(
            bytes(out[offset + 24:offset + 24 + page]), key)
        offset += 24 + page
    return bytes(out)


def _msg(cid: str, mid: str, created: int, text: str, ctype: int = 1) -> tuple:
    body = json.dumps({"text": text}) if ctype == 1 else text
    return (cid, mid, "u-9", created, ctype, body, 0, "")


@pytest.fixture()
def wal_profile(tmp_path, monkeypatch) -> Path:
    """一枚"客户端还留着 -wal 没 checkpoint"的假资料目录：基础库两行，WAL 里再两行。"""
    root = tmp_path / "DingTalk"
    profile = root / "e34da8455d4779ae5786_v3"
    (profile / "DBFiles").mkdir(parents=True)
    (root / "log").mkdir(parents=True)

    plain = profile / "DBFiles" / "plain.db"
    plain.unlink(missing_ok=True)
    connection = sqlite3.connect(plain)
    connection.execute("PRAGMA page_size=4096")
    connection.execute("PRAGMA journal_mode=wal")
    connection.execute("CREATE TABLE tbconversation (cid TEXT, title TEXT, type INTEGER, lastModify TEXT)")
    connection.execute("CREATE TABLE tbmsg_000 "
                       "(cid TEXT, mid TEXT, senderId TEXT, createdAt INTEGER,"
                       " contentType INTEGER, content TEXT, recallStatus INTEGER, atIds TEXT)")
    connection.executemany(f"INSERT INTO tbconversation VALUES (?,?,?,?)",
                           [("cid-one", "工程组", 2, "1792000000000")])
    connection.executemany(f"INSERT INTO tbmsg_000 {MSG_COLUMNS} {MSG_VALUES}",
                           [_msg("cid-one", "1001", 1_762_000_000_000, "基础库里那一句"),
                            _msg("cid-one", "1002", 1_762_000_060_000, "基础库里第二句")])
    connection.commit()
    # 先 checkpoint 一次：让"基础库"真的带着表结构与那两行（这台真库就是这个形状——
    # `dingtalk.db` 有 1197 页、`-wal` 另有 1308 帧；反倒 `calendar_v2.db` 基础库只剩一页）
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    base_bytes = plain.read_bytes()

    connection.executemany(f"INSERT INTO tbmsg_000 {MSG_COLUMNS} {MSG_VALUES}",
                           [_msg("cid-one", "2001", 1_762_000_120_000, "还在 WAL 里的那一句"),
                            _msg("cid-one", "2002", 1_762_000_180_000, "WAL 里最新一句")])
    connection.commit()
    wal_bytes = Path(str(plain) + "-wal").read_bytes()
    connection.close()
    assert wal_bytes, "假库没留下 WAL，这测试就没对象"

    key = store.derive_key(UID, SALT)
    (profile / "DBFiles" / "dingtalk.db").write_bytes(encrypt_pages(base_bytes, key))
    (profile / "DBFiles" / "dingtalk.db-wal").write_bytes(encrypt_wal(wal_bytes, key))
    plain.unlink(missing_ok=True)
    Path(str(plain) + "-wal").unlink(missing_ok=True)
    Path(str(plain) + "-shm").unlink(missing_ok=True)

    (profile / "user_config").write_bytes(base64.b64encode(json.dumps({"salt": SALT}).encode()))
    (root / "log" / "cef_debug.log.2026-10-26").write_text(f"x real_uid={UID} y\n", encoding="utf-8")
    monkeypatch.setenv("PLOBI_DINGTALK_DATA_DIR", str(root))
    monkeypatch.setenv("PLOBI_DINGTALK_LOCAL_DIR", str(tmp_path / "local"))
    monkeypatch.delenv("DINGTALK_UID", raising=False)
    return profile        # 交资料目录那一格本身：测试要能摸到 `DBFiles/dingtalk.db-wal`


def _rows(opened) -> list[str]:
    return [row["text"] for row in store.recent_messages(opened, ["cid-one"], limit=50)]


class TestTheWalIsMerged:
    def test_rows_still_in_the_wal_are_read(self, wal_profile) -> None:
        opened = store.open_local_store()
        try:
            texts = _rows(opened)
        finally:
            opened.close()

        assert "还在 WAL 里的那一句" in texts, "不合 WAL 就是把他刚发的话当成不存在"
        assert len(texts) == 4

    def test_the_merged_copy_passes_integrity_check(self, wal_profile) -> None:
        opened = store.open_local_store()
        try:
            verdict = opened.connection.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            opened.close()

        assert verdict == "ok", "合坏了的树比少读几行糟得多"

    def test_the_copy_does_not_inherit_a_wal_of_its_own(self, wal_profile) -> None:
        opened = store.open_local_store()
        opened.close()

        leftovers = sorted(p.name for p in Path(store.local_dir()).iterdir()
                           if p.name.endswith(("-wal", "-shm")))
        assert leftovers == [], "副本旁边留一枚对不上盐值的 WAL，等于让 SQLite 再猜一次"

    def test_an_uncommitted_tail_frame_is_dropped_wholesale(self, wal_profile) -> None:
        """尾巴上没收完的那一组必须整个丢掉：它的页号没人承诺过，收下就引用空页。"""
        db = wal_profile / "DBFiles" / "dingtalk.db"
        wal = Path(str(db) + "-wal")
        raw = wal.read_bytes()
        header = raw[:32]
        salt = header[16:24]
        bogus_page = encrypt_pages(bytes(PAGE), store.derive_key(UID, SALT))
        salts = struct.unpack(">2I", salt)
        frame_head = struct.pack(">6I", 9999, 0, salts[0], salts[1], 0, 0)
        wal.write_bytes(raw + frame_head + bogus_page)

        opened = store.open_local_store()
        try:
            verdict = opened.connection.execute("PRAGMA integrity_check").fetchone()[0]
            texts = _rows(opened)
        finally:
            opened.close()

        assert verdict == "ok"
        assert len(texts) == 4, "未提交那一组不该被算进来"

    def test_a_wal_we_cannot_trust_leaves_the_base_copy_working(self, wal_profile) -> None:
        """WAL 认不出来（换代了）就退回基础库那一版——**少几帧比整族读不到好**，也不许抛。"""
        wal = wal_profile / "DBFiles" / "dingtalk.db-wal"
        raw = bytearray(wal.read_bytes())
        raw[16:24] = b"\x00" * 8  # 盐值改成对不上
        wal.write_bytes(bytes(raw))

        opened = store.open_local_store()
        try:
            texts = _rows(opened)
        finally:
            opened.close()

        assert texts == ["基础库里那一句", "基础库里第二句"]


def _attachment(ctype: int, extension: dict) -> str:
    return json.dumps({"contentType": ctype,
                       "attachments": [{"extension": json.dumps(extension, ensure_ascii=False),
                                        "size": 0, "type": ctype, "url": ""}]})


class TestNonTextReadsAsASentence:
    """每一类只从**实测有的那几格**取（键路径抄自这台 155 条样本），不猜、不编。"""

    def test_plain_text_is_still_the_message_itself(self):
        assert store.describe_of(json.dumps({"text": "这份料明天上午给我"}), 1) == "这份料明天上午给我"

    def test_file_names_and_sizes_come_from_the_attachment_extension(self):
        raw = _attachment(501, {"f_name": "学期校历.pdf", "f_size": str(2_411_520), "f_type": "pdf"})

        assert store.describe_of(raw, 501) == "学期校历.pdf（2.3 MB）"

    def test_image_names_live_at_the_top_level_not_in_attachments(self):
        raw = json.dumps({"filename": "IMG_0042.jpg", "picSize": 1_048_576, "width": 1080})

        assert store.describe_of(raw, 2) == "IMG_0042.jpg（1.0 MB）"

    def test_an_interactive_card_reads_the_chinese_line_of_its_i18n_table(self):
        raw = _attachment(2950, {"widgetName": "taskCard", "miniAppId": "500000000",
                                 "LastMessageI18n": json.dumps({"en_US": "Task updated",
                                                                "zh_CN": "任务已更新"},
                                                               ensure_ascii=False)})

        assert store.describe_of(raw, 2950) == "任务已更新"

    def test_a_ding_reads_its_content(self):
        raw = _attachment(1600, {"dingId": "3", "dingContent": "明天九点开会", "dingSenderId": "9"})

        assert store.describe_of(raw, 1600) == "明天九点开会"

    def test_a_markdown_card_reads_its_title(self):
        raw = _attachment(1200, {"title": "会议提醒", "markdown": "### 正文很长" * 30})

        assert store.describe_of(raw, 1200) == "会议提醒"

    def test_something_we_do_not_parse_stays_empty_and_says_so_by_name(self):
        assert store.describe_of(json.dumps({"attachments": [{"extension": "{}"}]}), 7777) == ""
        assert store.content_label(7777) == "非文本消息"
        assert store.content_label(2950) == "卡片"
        assert store.content_label(501) == "文件"
        assert store.content_label(2) == "图片"

    def test_a_broken_payload_is_not_a_crash(self):
        assert store.describe_of("不是 JSON", 2950) == ""
        assert store.describe_of(None, 1) == ""


class TestTheCalendarLeg:
    """日历那一枚库（`calendar_v2.db`）进池这一腿（用户 10-26 拍：「进池，kind 我定」）。"""

    @staticmethod
    def _make_calendar_db(profile: Path) -> None:
        plain = profile / "DBFiles" / "plain_cal.db"
        connection = sqlite3.connect(plain)
        connection.execute("PRAGMA page_size=4096")
        connection.executescript(
            "CREATE TABLE calendar (calendar_id INTEGER, biz_type TEXT, source INTEGER, folder_id TEXT);"
            "CREATE TABLE calendar_event (event_id INTEGER, calendar_id INTEGER, folder_id TEXT,"
            " subject TEXT, location TEXT, start_time INTEGER, end_time INTEGER,"
            " all_day_event INTEGER, unique_id TEXT);")
        connection.execute("INSERT INTO calendar VALUES (777, 'meeting', 2, 'f-1')")
        connection.executemany("INSERT INTO calendar_event VALUES (?,?,?,?,?,?,?,?,?)",
                               [(1001, 777, "f-1", "周会", "3 号楼 201", 1_792_900_000_000,
                                 1_792_903_800_000, 0, "u-1"),
                                (1002, 777, "f-1", "   ", "", 1_792_910_000_000, 0, 1, "u-2")])
        connection.commit()
        connection.close()
        (profile / "DBFiles" / store.CALENDAR_DB).write_bytes(
            encrypt_pages(plain.read_bytes(), store.derive_key(UID, SALT)))
        plain.unlink()

    @pytest.fixture()
    def cal_profile(self, wal_profile) -> Path:
        self._make_calendar_db(wal_profile)
        return wal_profile

    def test_the_calendar_rows_land_with_the_event_kind(self, cal_profile) -> None:
        opened = store.open_local_store()
        try:
            collected = collector_module.DingTalkCollector(local_store=opened)
            occurrences = collected.pull()
        finally:
            opened.close()

        events = [o for o in occurrences if o.kind == collector_module.KIND_EVENT]
        assert [o.title for o in events] == ["周会"], "标题空白那一行不该进池（读不懂的读数比空着更贵）"
        assert events[0].external_id == "event:1001"
        assert events[0].location == "3 号楼 201"
        assert events[0].end_at and events[0].end_at > events[0].start_at
        assert events[0].evidence["calendar_id"] == "777"
        assert events[0].evidence["origin"].endswith(store.CALENDAR_DB)
        assert collected.calendar_note.startswith("1 条")

    def test_chat_and_calendar_come_out_of_the_same_round(self, cal_profile) -> None:
        opened = store.open_local_store()
        try:
            kinds = collections.Counter(o.kind for o in collector_module.DingTalkCollector(
                local_store=opened).pull())
        finally:
            opened.close()

        assert kinds[collector_module.KIND] == 4 and kinds[collector_module.KIND_EVENT] == 1

    def test_a_missing_calendar_store_does_not_kill_the_chat_leg(self, wal_profile) -> None:
        """日历那枚库开不了 ⇒ 聊天那 4 条照进，但那一句为什么必须在 `health()` 里说得出。"""
        opened = store.open_local_store()
        try:
            collected = collector_module.DingTalkCollector(local_store=opened)
            occurrences = collected.pull()
            detail = collected.health()["detail"]
        finally:
            opened.close()

        assert [o.kind for o in occurrences] == [collector_module.KIND] * 4
        assert "日历：读不到" in detail, "把「这枚库盘上没有」闷成一句「没日程」就是假读数"


class TestThePoolRow:
    """采集器那一格：解析不出来的也得是**一句中文**，不是 `（card）` 这种键名。"""

    def _pull(self, monkeypatch, rows):
        opened = type("Opened", (), {"profile": "p", "connection": None})()
        monkeypatch.setattr(store, "list_conversations",
                            lambda _s, **kw: [{"cid": "cid-one", "title": "工程组", "type": "group",
                                               "last_at": "", "last_modify": ""}])
        monkeypatch.setattr(store, "recent_messages", lambda _s, _c, **kw: rows)
        return collector_module.DingTalkCollector(local_store=opened, titles={"cid-one": "工程组"}).pull()

    def test_an_unparsable_card_is_labelled_in_chinese(self, monkeypatch) -> None:
        occurrences = self._pull(monkeypatch, [{
            "cid": "cid-one", "mid": "m1", "sender_id": "u-9", "created_at": "1762000000000",
            "content_type": 2950, "text": "", "at_ids": "", "shard": "tbmsg_000"}])

        assert occurrences[0].title == "（卡片）"
        assert "card" not in occurrences[0].title

    def test_a_parsed_file_reads_as_its_name(self, monkeypatch) -> None:
        occurrences = self._pull(monkeypatch, [{
            "cid": "cid-one", "mid": "m2", "sender_id": "u-9", "created_at": "1762000000000",
            "content_type": 501, "text": store.describe_of(
                _attachment(501, {"f_name": "校历.pdf", "f_size": "2048"}), 501),
            "at_ids": "", "shard": "tbmsg_000"}])

        assert occurrences[0].title == "校历.pdf（2.0 KB）"
        assert occurrences[0].evidence["content_kind"] == "file"
