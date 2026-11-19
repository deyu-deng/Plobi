"""`prompt.submit` 带的「在回哪几条」——RPC 入口这一跳。

协议改成多锚点时，这一跳是**第一个会静默把 list 弄坏的地方**：原来只收
`str | int`，传 list 被丢成空串，再被 `if reply_to:` 吃掉 ⇒ 整条引用无声消失
（不报错、不记日志）。

这一档钉三件事：入口**认得**一串 id、旧客户端的单个 id 仍认、形状归一只有一处。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture()
def server():
    with patch.dict(
        "sys.modules",
        {
            "plobi_constants": MagicMock(
                get_plobi_home=MagicMock(return_value="/tmp/plobi_test_reply_to")
            ),
            "plobi_cli.env_loader": MagicMock(),
            "plobi_cli.banner": MagicMock(),
            "plobi_state": MagicMock(),
        },
    ):
        import importlib

        mod = importlib.import_module("tui_gateway.server")
        yield mod
        mod._sessions.clear()
        mod._pending.clear()
        mod._answers.clear()


class TestEntry:
    def test_a_list_of_anchors_survives(self, server):
        """一串 id 原样过去——这是「一条消息可以回多条」的入口那一跳。"""
        assert server._reply_to_from_params({"reply_to": ["12", "34"]}) == ["12", "34"]

    def test_the_old_single_id_still_works(self, server):
        """旧客户端发单个 id（向后兼容）：仍认。"""
        assert server._reply_to_from_params({"reply_to": "12"}) == ["12"]
        assert server._reply_to_from_params({"reply_to": 12}) == ["12"]

    def test_missing_or_garbage_means_no_anchor(self, server):
        assert server._reply_to_from_params({}) == []
        assert server._reply_to_from_params({"reply_to": None}) == []
        assert server._reply_to_from_params({"reply_to": {"nope": 1}}) == []

    def test_the_rpc_body_actually_uses_this_helper(self, server, monkeypatch):
        """入口那一跳不许又自己 `str()` 一遍（那正是被拆掉的静默坑）。"""
        seen = {}

        def fake_entry(params):
            seen["params"] = params
            return ["12", "34"]

        monkeypatch.setattr(server, "_reply_to_from_params", fake_entry)

        # 会话查找会失败并提前返回 —— 但解析已经发生，我们要验的就是解析这一步。
        monkeypatch.setattr(
            server,
            "_sess_nowait",
            lambda params, rid: (None, {"error": {"code": 4001, "message": "no session"}}),
        )

        server._methods["prompt.submit"](
            "rid-1", {"session_id": "s1", "text": "hi", "reply_to": ["12", "34"]}
        )

        assert seen.get("params", {}).get("reply_to") == ["12", "34"]
