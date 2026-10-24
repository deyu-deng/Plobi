r"""本机库那一路的读手与采集器（`WP-DINGTALK-LOCAL-STORE`）——**全部用我自己造的假库**。

跑法：`bash scripts/run_tests.sh tests/plobi/test_dingtalk_local_store.py -q`

这一档为什么值得单独写：这台机器上真库的形状与调研件写的不一样（`DBFiles/dingtalk.db`
而不是 `dingtalk_encrypt.db`；`_v3` 目录名不是 UID），而这两条都是**实测出来的**（读数在
`Docs/reference/dingtalk-collection-survey.md` §4 那一节的更正里）。测试钉的是同一批事实的
可执行版本：目录认错的形状、钥匙那 16 个字符是 **ASCII 十六进制**（不是原始摘要——那一支我试过，
不通）、以及**没点名的会话一条都不许进**。

零真数据：库里每一行都是这个文件自己写进去的假句子；不碰 `%APPDATA%`、不碰他的钉钉一个字节。
"""

from __future__ import annotations

import base64
import json
import sqlite3
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from plobi.collectors.dingtalk import collector as collector_module
from plobi.collectors.dingtalk import localdb, store

UID = "666165872"
SALT = "0123456789abcdef0123456789abcdef"
PAGE = 4096


def encrypt_pages(plain: bytes, key: bytes) -> bytes:
    """整页 ECB（与 `store._decrypt_block` 同一把形状）。页大小是 4096，16 整除，不需要补位。"""
    out = bytearray()
    for offset in range(0, len(plain), PAGE):
        block = plain[offset:offset + PAGE]
        if len(block) % 16:
            block = block + bytes(16 - len(block) % 16)
        encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
        out += encryptor.update(block) + encryptor.finalize()
    return bytes(out)


def make_plain_db(path: Path, messages: list[tuple[str, str, str, int, int, str, int]]) -> None:
    """一枚 page_size=4096 的明文库，表名与列名照这台真库抄（正文全是假句子）。"""
    path.unlink(missing_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA page_size=4096")
    connection.execute("CREATE TABLE tbconversation (cid TEXT, title TEXT, type INTEGER, lastModify TEXT)")
    connection.execute("CREATE TABLE tbmsg_000 (cid TEXT, mid TEXT, senderId TEXT, createdAt INTEGER,"
                       " contentType INTEGER, content TEXT, recallStatus INTEGER, atIds TEXT)")
    connection.execute("CREATE TABLE tbmsg_001 (cid TEXT, mid TEXT, senderId TEXT, createdAt INTEGER,"
                       " contentType INTEGER, content TEXT, recallStatus INTEGER, atIds TEXT)")
    connection.executemany("INSERT INTO tbconversation VALUES (?,?,?,?)",
                           [("cid-one", "工程组", 2, "1"), ("cid-two", "李四", 1, "2")])
    connection.executemany("INSERT INTO tbmsg_000 VALUES (?,?,?,?,?,?,?,?)",
                           [(cid, mid, sender, created, ctype,
                             json.dumps({"contentType": ctype, "text": text}), recall, "")
                            for cid, mid, sender, created, ctype, text, recall in messages])
    connection.commit()
    connection.close()


@pytest.fixture()
def fake_profile(tmp_path, monkeypatch) -> Path:
    """一个"看起来像已登录"的假资料目录：`{20hex}_v3/DBFiles/dingtalk.db` + `user_config` + 日志。"""
    root = tmp_path / "DingTalk"
    profile = root / "e34da8455d4779ae5786_v3"
    (profile / "DBFiles").mkdir(parents=True)
    (root / "log").mkdir(parents=True)
    # 安装器会留下的那几个目录——它们**不是**资料目录（上一版把它们认成了 8 个已登录账号）
    (root / "config").mkdir()
    (root / "defEmotion").mkdir()
    (root / "EarlyConfigV2").mkdir()

    plain = profile / "DBFiles" / "plain.db"
    make_plain_db(plain, [
        ("cid-one", "1001", "u-9", 1_762_000_000_000, 1, "这份料明天上午给我", 0),
        ("cid-one", "1002", "u-9", 1_762_000_060_000, 1, "图在这条消息的附件里", 1),   # 撤回 ⇒ 不取
        ("cid-two", "1003", "u-8", 1_762_000_090_000, 501, "", 0),                    # 文件，无正文
        ("cid-one", "1004", "u-9", 1_762_000_120_000, 1, "记得审一下", 0),
    ])
    key = store.derive_key(UID, SALT)
    (profile / "DBFiles" / "dingtalk.db").write_bytes(encrypt_pages(plain.read_bytes(), key))
    plain.unlink()
    (profile / "user_config").write_bytes(base64.b64encode(json.dumps({"salt": SALT}).encode()))
    (root / "log" / "cef_debug.log.2026-10-24").write_text(f"x real_uid={UID} y\n", encoding="utf-8")

    monkeypatch.setenv("PLOBI_DINGTALK_DATA_DIR", str(root))
    monkeypatch.setenv("PLOBI_DINGTALK_LOCAL_DIR", str(tmp_path / "local"))
    monkeypatch.delenv("DINGTALK_UID", raising=False)
    return root


class TestTheDirectoryShapeIsReadCorrectly:
    """上一版把 `%APPDATA%\\DingTalk` 下**任何一个**目录都认成资料目录，于是报出"有 8 个已登录的资料目录"。"""

    def test_only_account_shaped_dirs_are_profiles(self, fake_profile) -> None:
        found = store.profiles()

        assert [p.name for p in found] == ["e34da8455d4779ae5786_v3"]

    def test_a_v2_dir_named_after_the_uid_is_also_a_profile(self, tmp_path) -> None:
        root = tmp_path / "R"
        (root / "666165872_v2" / "DBFiles").mkdir(parents=True)

        assert [p.name for p in store.profiles([root])] == ["666165872_v2"]

    def test_the_database_is_read_from_dbfiles_on_this_version(self, fake_profile) -> None:
        profile = store.profiles()[0]

        assert store.db_path(profile).match("DBFiles/dingtalk.db")

    def test_the_legacy_location_next_to_the_config_is_still_read(self, tmp_path) -> None:
        profile = tmp_path / "aabbccddeeff_v3"
        (profile / "DBFiles").mkdir(parents=True)
        (profile / "dingtalk_encrypt.db").write_bytes(b"")

        assert store.db_path(profile).name == "dingtalk_encrypt.db"


class TestTheKey:
    def test_the_key_is_the_first_16_hex_characters_as_ascii(self) -> None:
        """不是那 16 字节原始摘要——这一条我是实测出来的（原始摘要那一支解不开页头）。"""
        key = store.derive_key(UID, SALT)

        assert len(key) == 16 and key.decode("ascii").isalnum()

    def test_v2_needs_no_salt(self) -> None:
        import hashlib

        assert store.derive_key_v2(UID) == hashlib.md5(UID.encode()).hexdigest()[:16].encode()

    def test_a_wrong_uid_does_not_open_the_store(self, fake_profile, monkeypatch) -> None:
        monkeypatch.setattr(store, "uid_candidates", lambda *a, **kw: ["0000000000"])

        with pytest.raises(store.LocalStoreUnavailable) as caught:
            store.open_local_store()

        assert caught.value.code == "local_store_missing"

    def test_a_missing_log_leaves_no_uid_and_says_so(self, fake_profile) -> None:
        (fake_profile / "log" / "cef_debug.log.2026-10-24").unlink()

        with pytest.raises(store.LocalStoreUnavailable) as caught:
            store.open_local_store(candidates=[])

        assert "UID" in caught.value.detail and caught.value.code == "local_store_missing"

    def test_the_env_uid_is_honoured(self, fake_profile) -> None:
        (fake_profile / "log" / "cef_debug.log.2026-10-24").unlink()

        opened = store.open_local_store(candidates=[UID])

        assert opened.conversations == 2
        opened.close()


class TestTheStoreOpens:
    def test_the_decrypted_copy_is_readable_and_reports_the_conversation_count(self, fake_profile) -> None:
        opened = store.open_local_store()

        try:
            assert opened.conversations == 2
            assert [row["title"] for row in store.list_conversations(opened)] == ["李四", "工程组"]
            assert Path(store.local_dir()).is_dir(), "解密副本落在 PLOBI_HOME 那一侧（D 盘），不在仓里"
        finally:
            opened.close()

    def test_the_source_directory_is_never_written_to(self, fake_profile) -> None:
        profile_db = store.db_path(store.profiles()[0])
        before = profile_db.read_bytes()

        store.open_local_store().close()

        assert profile_db.read_bytes() == before, "这一族对源文件只读——一个字节都不许动"

    def test_only_messages_of_admitted_conversations_are_read(self, fake_profile) -> None:
        opened = store.open_local_store()

        try:
            rows = store.recent_messages(opened, ["cid-two"])
            assert [row["cid"] for row in rows] == ["cid-two"]
            assert store.recent_messages(opened, []) == [], "没点名 = 一条都不读，这一条不许有别的解释"
        finally:
            opened.close()

    def test_a_recalled_message_is_not_read_out(self, fake_profile) -> None:
        opened = store.open_local_store()

        try:
            mids = [row["mid"] for row in store.recent_messages(opened, ["cid-one"])]
        finally:
            opened.close()

        assert mids == ["1001", "1004"], "撤回那条(1002)不该被当成还在"


class TestTheCollector:
    def test_a_row_becomes_an_occurrence_with_the_message_s_own_time(self, fake_profile) -> None:
        opened = store.open_local_store()
        collected = collector_module.DingTalkCollector(local_store=opened, cids=["cid-one"])

        rows = collected.pull()

        assert [row.external_id for row in rows] == ["cid-one:1001", "cid-one:1004"]
        assert rows[0].kind == "message"
        assert rows[0].title == "这份料明天上午给我"
        assert rows[0].start_at.replace("-", "").replace(":", "").replace(" ", "").isdigit()
        assert rows[0].evidence["talker"] == "cid-one"
        assert rows[0].evidence["talker_name"] == "工程组"
        assert "local-store:" in rows[0].evidence["origin"]
        opened.close()

    def test_a_non_text_message_lands_with_no_body_but_names_its_kind(self, fake_profile) -> None:
        """卡片/文件不解析正文——但这一行要说得出它是哪一类，否则"没有正文"会被读成"没有消息"。"""
        opened = store.open_local_store()
        collected = collector_module.DingTalkCollector(local_store=opened, cids=["cid-two"])

        row = collected.pull()[0]

        assert row.evidence["text"] == ""
        assert row.evidence["content_kind"] == "file"
        assert row.title == "（file）"
        opened.close()

    def test_nothing_is_admitted_by_default_and_health_says_that_in_two_sentences(self, fake_profile) -> None:
        opened = store.open_local_store()
        collected = collector_module.DingTalkCollector(local_store=opened, cids=[])

        assert collected.pull() == []
        health = collected.health()
        assert health["ok"] is False and health["state"] == "needs_setup"
        assert "2 个会话" in health["detail"], "库在位 ≠ 采得到——两句话都要说"
        opened.close()

    def test_the_cid_list_comes_from_the_configured_field(self, fake_profile) -> None:
        collected = collector_module.DingTalkCollector.build(
            config={"conversations": "cid-one，cid-two"}, opener=lambda: store.open_local_store())

        assert collected.cids == ["cid-one", "cid-two"], "全角逗号也算分隔：中文输入法下打出来的是它"
        collected.store.close()

    def test_the_conversation_field_is_declared_required(self) -> None:
        """必填是真的必填：没点名就采不到东西。这一格是"假动作"与"闸门"的分界。"""
        from plobi.collectors import registry

        registry.ensure_discovered()
        fields = {field.key: field for field in registry.spec("dingtalk").setup_fields}

        assert fields["conversations"].required is True
        assert fields["conversations"].kind == "text"

    def test_the_talker_panel_is_not_claimed_yet(self) -> None:
        """那块"每个群/每个人采不采"的面板今天只认微信那一份 store。
        把钉钉指过去会画出一屏错的数据——所以这一格必须留 `False`，等面板真的通用了再翻。"""
        from plobi.collectors import registry

        registry.ensure_discovered()

        assert registry.spec("dingtalk").conversation_scoped is False
