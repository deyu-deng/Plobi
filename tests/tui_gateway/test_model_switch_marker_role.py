"""Tests for _append_model_switch_marker role fix (issue #48338).

The model switch marker must NOT use role="system" because strict providers
(vLLM, Qwen) reject system messages that appear mid-conversation. Using
role="user" is safe — the system prompt is prepended to the API message list,
so a user-role marker can appear at any later position, and the gateway's
sanitize/merge pass already coalesces consecutive user messages.

R-092 (2026-10-15) adds the other half of the contract: the marker is
**in-memory only**. It used to be persisted as a user row, which made it
masquerade as something the user said inside L1's memory, re-billed on every
turn, and became a second source of truth for "which leg is this chat on" —
that fact belongs to ``session_model_usage`` and nowhere else.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

from tui_gateway.server import _append_model_switch_marker


class TestAppendModelSwitchMarkerRole:
    """Verify the marker uses role='user', not role='system'."""

    def test_marker_uses_user_role(self) -> None:
        """The history entry must be role='user', not role='system'."""
        session: dict = {"session_key": "test-session", "history": []}
        _append_model_switch_marker(session, model="gpt-4o", provider="openai")
        assert len(session["history"]) == 1
        entry = session["history"][0]
        assert entry["role"] == "user", (
            f"Expected role='user' but got role='{entry['role']}'. "
            "Strict providers (vLLM, Qwen) reject mid-conversation system messages."
        )

    def test_marker_content_preserved(self) -> None:
        """The marker content must still describe the model switch."""
        session: dict = {"session_key": "s", "history": []}
        _append_model_switch_marker(session, model="qwen3.6-35b", provider="vllm")
        content = session["history"][0]["content"]
        assert "qwen3.6-35b" in content
        assert "vllm" in content
        assert "model" in content.lower()

    def test_marker_with_empty_provider(self) -> None:
        """Provider part should be omitted when provider is empty."""
        session: dict = {"session_key": "s", "history": []}
        _append_model_switch_marker(session, model="claude-sonnet-4", provider="")
        content = session["history"][0]["content"]
        assert "claude-sonnet-4" in content
        assert "via provider" not in content

    def test_marker_with_lock(self) -> None:
        """Marker should work correctly when session has a history_lock."""
        session: dict = {
            "session_key": "s",
            "history": [],
            "history_lock": threading.Lock(),
        }
        _append_model_switch_marker(session, model="gpt-4o", provider="openai")
        assert len(session["history"]) == 1
        assert session["history"][0]["role"] == "user"

    def test_marker_increments_history_version(self) -> None:
        """history_version should be incremented after appending."""
        session: dict = {"session_key": "s", "history": [], "history_version": 5}
        _append_model_switch_marker(session, model="gpt-4o", provider="openai")
        assert session["history_version"] == 6

    def test_no_marker_for_none_session(self) -> None:
        """None session should be a no-op."""
        _append_model_switch_marker(None, model="gpt-4o", provider="openai")

    def test_no_marker_for_empty_session_key(self) -> None:
        """Empty session_key should be a no-op."""
        session: dict = {"session_key": "", "history": []}
        _append_model_switch_marker(session, model="gpt-4o", provider="openai")
        assert len(session["history"]) == 0

    def test_marker_not_mid_history_system_after_turns(self) -> None:
        """The marker appended after real turns must not be a system role.

        Reproduces the #48338 shape: a switch mid-conversation must not inject
        a second system message after user/assistant turns, which strict
        OpenAI-compatible providers reject.
        """
        db = MagicMock()
        session: dict = {
            "session_key": "sess-1",
            "history": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
            ],
            "history_version": 7,
            "agent": SimpleNamespace(_session_db=db),
        }
        _append_model_switch_marker(
            session, model="qwen3.6-35b", provider="vllm"
        )
        marker = session["history"][-1]
        assert marker["role"] == "user"
        assert session["history_version"] == 8
        # R-092: 这条通知**不落库**——落库就等于把它长在这页聊天里，
        # 它在 L1 的记忆中冒充用户说的话，且每轮重新付一次 token。
        db.append_message.assert_not_called()

    def test_a_switch_writes_nothing_to_the_session_store_at_all(self) -> None:
        """判据（R-092）：切换通知只活在内存里这一轮，**库里一条都不许有**；
        「这一页现在走哪条腿」的唯一真源是 `session_model_usage`，不是聊天消息。
        断言写成关系（整个 store 零调用），不是快照某一句文案。"""
        db = MagicMock()
        session: dict = {
            "session_key": "sess-9",
            "history": [{"role": "user", "content": "hi"}],
            "agent": SimpleNamespace(_session_db=db),
        }

        _append_model_switch_marker(session, model="workbuddy/hy3-x", provider="aigw")
        _append_model_switch_marker(session, model="workbuddy/hy3", provider="aigw")

        assert db.method_calls == [], f"切换通知仍然碰了会话库：{db.method_calls}"
        # 但正在跑的这一轮必须仍然知道腿换了，否则它答「我用的是哪条腿」会拿旧值。
        assert len(session["history"]) == 3
        assert "workbuddy/hy3" in session["history"][-1]["content"]
