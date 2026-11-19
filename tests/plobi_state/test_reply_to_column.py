"""`messages.reply_to` —— 这一条在回哪几条（引用条）。

协议最初只允许**一个**锚点，整条链路都按单值写死；改成「可多个」时，这条链路上
有四个会**静默**把 list 弄坏的地方（都不报错，引用就没了）：RPC 入口把 list 丢成
空串、`turn_context` 的 `str()` 变成 `"['a', 'b']"` 字面量、`resolve_reply_to` 的
`.strip()` 抛异常被 `except` 吞掉、往 TEXT 列直绑 list 报 sqlite 错。

这一档钉的是**列的形状**：多锚点存 JSON 数组、旧的裸 id 行照读、单值那一路
（Agent 侧 `channel.py` 的 `say`）一个字不变。

另见 `tests/agent/test_reply_to_plumbing.py`（链路）与
`tests/tui_gateway/test_prompt_submit_reply_to.py`（入口）。
"""

import json

import pytest

from plobi_state import SessionDB


@pytest.fixture
def db(tmp_path):
    return SessionDB(tmp_path / "state.db")


class TestColumnShape:
    def test_one_anchor_comes_back_as_one(self, db):
        db.create_session("s1", source="cli")
        db.append_message("s1", "user", "问", reply_to=["12"])

        assert json.loads(db.get_messages("s1")[0]["reply_to"]) == ["12"]

    def test_several_anchors_round_trip(self, db):
        db.create_session("s2", source="cli")
        db.append_message("s2", "user", "问", reply_to=["12", "34"])

        assert json.loads(db.get_messages("s2")[0]["reply_to"]) == ["12", "34"]

    def test_legacy_bare_id_stays_bare(self, db):
        """Agent 侧 `say` 仍写裸 id ⇒ 那一格一个字不变（读侧兼容两种形状）。"""
        db.create_session("s3", source="cli")
        db.append_message("s3", "user", "问", reply_to="12")

        assert db.get_messages("s3")[0]["reply_to"] == "12"

    def test_no_anchor_is_null(self, db):
        """没有引用不是一个「空引用」，是**没记**（NULL）。"""
        db.create_session("s4", source="cli")
        db.append_message("s4", "user", "普通一问")

        assert db.get_messages("s4")[0]["reply_to"] is None

    def test_empty_list_is_null(self, db):
        db.create_session("s5", source="cli")
        db.append_message("s5", "user", "空锚点", reply_to=[])

        assert db.get_messages("s5")[0]["reply_to"] is None


class TestRewritePathsKeepIt:
    def test_replace_messages_carries_the_list_through(self, db):
        """/retry /compress 走的是批量重写；那条路一旦丢字段，引用随压缩消失。"""
        db.create_session("s6", source="cli")
        db.append_message("s6", "user", "原问题", reply_to=["12", "34"])

        db.replace_messages("s6", [
            {"role": "user", "content": "压缩后的转写", "reply_to": ["12", "34"]},
            {"role": "assistant", "content": "答"},
        ])

        rows = db.get_messages("s6")

        assert json.loads(rows[0]["reply_to"]) == ["12", "34"]
        assert rows[1]["reply_to"] is None


class TestShapes:
    """`agent/reply_targets` —— 形状的归一只有这一处。"""

    def test_normalize_handles_every_shape(self):
        from agent.reply_targets import normalize_reply_to

        assert normalize_reply_to(None) == []
        assert normalize_reply_to("12") == ["12"]
        assert normalize_reply_to(12) == ["12"]
        assert normalize_reply_to(["12", "34"]) == ["12", "34"]
        assert normalize_reply_to(("12", "34")) == ["12", "34"]
        assert normalize_reply_to(["12", "12"]) == ["12"]
        assert normalize_reply_to(["12", "", None]) == ["12"]
        assert normalize_reply_to({"nope": 1}) == []
        # bool 是 int 的子类：True 不该变成一个叫 "True" 的锚点
        assert normalize_reply_to(True) == []

    def test_encode_keeps_the_legacy_single_value_byte_identical(self):
        from agent.reply_targets import encode_reply_to

        assert encode_reply_to("12") == "12"
        assert encode_reply_to(["12"]) == '["12"]'
        assert encode_reply_to(["12", "34"]) == '["12", "34"]'
        assert encode_reply_to(None) is None
        assert encode_reply_to([]) is None

    def test_decode_reads_both_shapes(self):
        from agent.reply_targets import decode_reply_to

        assert decode_reply_to('["12", "34"]') == ["12", "34"]
        assert decode_reply_to("12") == ["12"]
        assert decode_reply_to(None) == []
        assert decode_reply_to("") == []
