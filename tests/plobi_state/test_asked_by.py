"""`messages.asked_by` —— 谁发起的这条问（WP-L2-OUTSOURCE-ENTRY）。

用户在 L2 的会话里要分得开"我自己打的"与"L1 代我问的"，所以这一格必须：
写进去读得回、老库升级后是 NULL（不是空串也不是 owner）、改写的路不许把它抹掉、
API 白名单放行它（不然字段躺在库里，界面永远看不见）。
"""

import sqlite3

import pytest

from plobi_state import SessionDB


@pytest.fixture
def db(tmp_path):
    return SessionDB(tmp_path / "state.db")


class TestRoundTrip:
    def test_owner_and_l1_both_come_back(self, db):
        db.create_session("s1", source="cli")
        db.append_message("s1", "user", "我自己打的", asked_by="owner")
        db.append_message("s1", "user", "L1 代问的", asked_by="l1:sess-l1-42")

        assert [m["asked_by"] for m in db.get_messages("s1")] == ["owner", "l1:sess-l1-42"]

    def test_unstamped_is_null_not_empty_string_nor_owner(self, db):
        """NULL 是一个**产品要显式显示**的状态（"这条没记发起方"），不许塌成任何默认值。"""
        db.create_session("s2", source="cli")
        db.append_message("s2", "user", "没记发起方")

        assert db.get_messages("s2")[0]["asked_by"] is None

    def test_assistant_replies_need_no_initiator(self, db):
        db.create_session("s3", source="cli")
        db.append_message("s3", "user", "问", asked_by="owner")
        db.append_message("s3", "assistant", "答")

        assert db.get_messages("s3")[1]["asked_by"] is None


class TestRewritePathsKeepIt:
    def test_replace_messages_carries_it_through(self, db):
        """/retry /compress 走的是批量重写；那条路一旦丢字段，发起方就随压缩消失。"""
        db.create_session("s4", source="cli")
        db.append_message("s4", "user", "原问题", asked_by="l1:sess-l1-7")

        db.replace_messages("s4", [
            {"role": "user", "content": "压缩后的转写", "asked_by": "l1:sess-l1-7"},
            {"role": "assistant", "content": "答"},
        ])

        rows = db.get_messages("s4")
        assert rows[0]["asked_by"] == "l1:sess-l1-7"
        assert rows[1]["asked_by"] is None


class TestOldDatabasesUpgrade:
    def test_column_appears_on_open_and_history_reads_as_unrecorded(self, tmp_path):
        path = tmp_path / "legacy.db"
        SessionDB(path).create_session("keep", source="cli")
        # 模拟升级前的库：把那一格删掉，就像它从来没存在过
        with sqlite3.connect(path) as conn:
            conn.execute("ALTER TABLE messages DROP COLUMN asked_by")

        db = SessionDB(path)

        columns = {r[1] for r in db._conn.execute("PRAGMA table_info(messages)")}
        assert "asked_by" in columns, "声明式迁移没把这格补回来"
        assert db.get_messages("keep") == []


class TestApiStopsDroppingIt:
    def test_message_response_passes_it_and_still_drops_unknown_keys(self):
        from gateway.platforms.api_server import APIServerAdapter

        body = APIServerAdapter._message_response({
            "id": 1, "session_id": "s", "role": "user", "content": "x",
            "asked_by": "l1:sess-l1-9", "_internals": "不许外泄",
        })

        assert body["asked_by"] == "l1:sess-l1-9"
        assert "_internals" not in body

    def test_missing_field_stays_absent_rather_than_becoming_owner(self):
        from gateway.platforms.api_server import APIServerAdapter

        body = APIServerAdapter._message_response({"id": 2, "role": "user", "content": "y"})

        assert body.get("asked_by") is None
