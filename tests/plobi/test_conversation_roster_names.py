"""设置页那份会话名单的三格判据（`WP-SOURCE-SETUP-GUIDANCE` 那一族的续刀，纸 §4.9/§6）。

钉的是**形状**，不是"某台机器上今天有哪几扇"：

1. 名字从联系人／群那两张表来，**备注优先**（用户 10-26：「尽量显示我的备注。这样我好辨认」）；
2. 群名**只认 `chatroom` 那张表**——session 自带的 `nickName` 对群是某个成员的名字，
   拿它当群名就是一句假话（这台现核 `56172831152@chatroom` 写的就是一个人名）；
3. 什么都查不到 ⇒ 那一格是会话号 + `resolved=False`，界面上念成"没查到名字"，不假装是个人；
4. 三组（群／私聊／公众号）是**名单那一档**的词表，与池里 `visibility` 那两档分开：
   `gh_*` 进池仍归 `private`（裁 187①），在名单里排"公众号"那一组；
5. 时间每扇都带上，且没给时间就什么都不画（不拿"现在"顶一个假读数）。
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from plobi.collectors.chatlog import roster  # noqa: E402
from plobi.collectors.chatlog.client import ChatlogClient, ChatlogUnavailable, pool_columns  # noqa: E402
from plobi.collectors.dingtalk import store  # noqa: E402

ROOM_ID = "56172831152@chatroom"
PERSON_ID = "wxid_k4e2zzvrbfvs22"
GH_ID = "gh_6ee08156edb6"


class FakeClient:
    """只填 `_get` 这一缝：`roster` 用的就是产物自带的那三只列表口，路径逐字对得上。"""

    def __init__(self, *, sessions, contacts, rooms, fail=()):
        self.tables = {"/api/v1/session": sessions, "/api/v1/contact": contacts,
                       "/api/v1/chatroom": rooms}
        self.fail = set(fail)
        self.asked: list[str] = []

    def _get(self, path, params):
        self.asked.append(path)
        if path in self.fail:
            raise ChatlogUnavailable(f"no {path}")
        return {"items": self.tables[path]}


def _client():
    """三张表的样本，形状逐字照这台现读到的那份（键名与空值都一样）。"""
    return FakeClient(
        sessions=[
            {"userName": ROOM_ID, "nickName": "慕东昊", "nTime": "2026-10-26T15:48:03+08:00"},
            {"userName": PERSON_ID, "nickName": "张某某", "nTime": "2026-10-25T09:00:00+08:00"},
            {"userName": GH_ID, "nickName": "", "nTime": "2026-10-24T11:00:00+08:00"},
        ],
        # 群里那一格 `nickName` 是**成员名**（这台现核的形状）；群名只有 chatroom 表给得出。
        contacts=[{"userName": PERSON_ID, "remark": "老张", "nickName": "张某某"},
                  {"userName": GH_ID, "remark": "教务处公告", "nickName": "浙大教务处"}],
        rooms=[{"name": ROOM_ID, "remark": "", "nickName": "工程组"}],
    )


class TestNames:
    def test_remark_outranks_everything_the_sidecar_already_said(self):
        rows = roster.rows(_client(), status_of=lambda _talker: None)
        by_id = {row["id"]: row for row in rows}

        assert by_id[PERSON_ID]["name"] == "老张", "备注是他自己写的称呼，必须压过昵称与会话号"

    def test_group_name_comes_from_the_room_table_not_the_member_name(self):
        rows = roster.rows(_client(), status_of=lambda _talker: None)
        room = next(row for row in rows if row["id"] == ROOM_ID)

        assert room["name"] == "工程组"
        assert "慕东昊" not in room["name"], "把群成员名当群名上屏就是一句假话"

    def test_official_account_falls_to_the_contact_table(self):
        rows = roster.rows(_client(), status_of=lambda _talker: None)

        assert next(row for row in rows if row["id"] == GH_ID)["name"] == "教务处公告",             "备注压过昵称——这一条对公众号也一样成立"

    def test_nothing_found_is_an_id_said_as_an_id(self):
        client = FakeClient(sessions=[{"userName": "brandsessionholder", "nickName": "", "nTime": ""}],
                           contacts=[], rooms=[])

        row = roster.rows(client, status_of=lambda _talker: None)[0]

        assert row["name"] == "brandsessionholder" and row["resolved"] is False
        assert roster.rows(_client(), status_of=None)[0]["resolved"] is True

    def test_a_broken_name_table_does_not_empty_the_roster(self):
        """查名字那两条腿断了，名单还要出（缺的名字由 `resolved` 说明白）。"""
        client = _client()
        client.fail = {"/api/v1/contact", "/api/v1/chatroom"}

        rows = roster.rows(client, status_of=lambda _talker: None)

        assert len(rows) == 3
        # 只有 1:1 那一扇能用 session 自带的昵称；群不许（那句为什么在 `label_for`）。
        resolved = {row["id"]: row["resolved"] for row in rows}

        assert resolved == {PERSON_ID: True, ROOM_ID: False, GH_ID: False}
        assert client.asked.count("/api/v1/session") == 1


class TestGroups:
    def test_the_three_groups_are_shape_only(self):
        assert [roster.talker_kind(x) for x in (ROOM_ID, PERSON_ID, GH_ID, "", None)] == \
            ["group", "private", "official", "private", "private"]

    def test_every_roster_kind_is_one_the_ui_has_words_for(self):
        kinds = {row["kind"] for row in roster.rows(_client(), status_of=lambda _t: None)}

        assert kinds == set(roster.ROSTER_KINDS), "名单的词表是闭集，多出一个是界面上一句没有的话"

    def test_pool_vocabulary_is_untouched_by_the_roster_vocabulary(self):
        """`gh_*` 在名单里是第三组，进池仍归 `private`（裁 187① 那两档不许被我这一刀改掉）。"""
        assert pool_columns(GH_ID)["visibility"] == "private"
        assert pool_columns(ROOM_ID)["visibility"] == "group"
        assert pool_columns("") == {"conversation": "", "visibility": ""}

    def test_status_defaults_to_collecting_not_a_third_word(self):
        rows = roster.rows(_client(), status_of=None)

        assert {row["status"] for row in rows} == {"pending"}
        assert roster.rows(_client(), status_of=lambda _t: "excluded")[0]["status"] == "excluded"


class TestTime:
    def test_every_row_carries_the_session_own_time(self):
        rows = roster.rows(_client(), status_of=lambda _t: None)

        assert next(row for row in rows if row["id"] == ROOM_ID)["last_at"] == "2026-10-26T15:48:03+08:00"

    def test_no_time_is_an_empty_string_not_a_made_up_now(self):
        client = FakeClient(sessions=[{"userName": PERSON_ID}], contacts=[], rooms=[])

        assert roster.rows(client, status_of=None)[0]["last_at"] == ""


class TestDingTalkRosterShape:
    """两族共用一份名单组件 ⇒ 时间的形状也必须一致（一个 `Date.parse` 认得的 ISO）。"""

    def test_epoch_milliseconds_become_a_local_offset_iso(self):
        # 这台现核：`tbconversation.lastModify` 178 行全是 integer 毫秒。
        iso = store.local_iso_from_epoch(1792822458246)

        assert iso[:4].isdigit() and "+" in iso or iso.endswith("Z")
        assert json.dumps({"last_at": iso})  # 可序列化 = 可直接进那条路由

    def test_unreadable_times_are_empty_not_zero(self):
        for raw in ("", None, "abc", 0, -1):
            assert store.local_iso_from_epoch(raw) == "", f"{raw!r} 不该被折成一个时刻"

    def test_seconds_are_still_seconds(self):
        assert store.local_iso_from_epoch(1792822458)[:10] == store.local_iso_from_epoch(1792822458000)[:10]

    def test_the_handler_hands_time_and_named_to_the_panel(self, monkeypatch):
        """两族共用一块面板 ⇒ 声明那一头也得把这两格交出去。

        这台 10-26 现读到过缺口：钉钉 178 行一个时间都没有，倒序因此排不动。
        """
        from plobi.collectors import dingtalk as pkg
        from plobi.collectors import talkers

        closed: list[bool] = []

        class Opened:
            def close(self):
                closed.append(True)

        class Ledger:
            def status_of(self, source, talker):
                return "excluded" if talker == "cid-one" else "pending"

        monkeypatch.setattr(store, "open_local_store", lambda *a, **kw: Opened())
        monkeypatch.setattr(store, "list_conversations", lambda _opened, *, limit=None: [
            {"cid": "cid-one", "title": "工程组", "type": "group",
             "last_at": "2026-10-26T15:48:03+08:00", "last_modify": "1792822458246"},
            {"cid": "cid-two", "title": "", "type": "private",
             "last_at": "", "last_modify": "0"}])
        monkeypatch.setattr(talkers, "Ledger", Ledger)

        rows = {row["id"]: row for row in pkg._conversations()}

        assert rows["cid-one"]["last_at"] == "2026-10-26T15:48:03+08:00"
        assert rows["cid-two"]["resolved"] is False, "标题空 ⇒ 那一格是会话号，界面要念成「没查到名字」"
        assert rows["cid-two"]["name"] == "cid-two"
        assert rows["cid-one"]["status"] == "excluded"
        assert closed == [True], "那条路要关连接，不许留一枚打开的句柄"
